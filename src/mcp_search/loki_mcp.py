"""MCP server for querying centralized logs via Loki's LogQL API."""

import os
from datetime import datetime, timedelta, timezone

import httpx
from fastmcp import FastMCP

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")

mcp = FastMCP("loki-logs")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_default(value: str | None, default: datetime) -> str:
    if value:
        return value
    return default.isoformat()


def _build_selector(
    host: str | None = None,
    source: str | None = None,
    level: str | None = None,
    service: str | None = None,
) -> str:
    """Build a LogQL stream selector from optional label filters."""
    parts = []
    if host:
        parts.append(f'host="{host}"')
    if source:
        parts.append(f'source="{source}"')
    if level:
        parts.append(f'level="{level}"')
    if service:
        parts.append(f'service="{service}"')
    return "{" + ", ".join(parts) + "}" if parts else '{source=~".+"}'


async def _query_range(logql: str, start: str, end: str, limit: int = 100) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": start,
                "end": end,
                "limit": limit,
                "direction": "backward",
            },
        )
        r.raise_for_status()
        data = r.json()

    results = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            # Convert nanosecond timestamp to readable
            dt = datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
            results.append({
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "host": labels.get("host", ""),
                "source": labels.get("source", ""),
                "service": labels.get("service", labels.get("unit", "")),
                "level": labels.get("level", labels.get("detected_level", "")),
                "message": line,
            })

    # Sort by time descending
    results.sort(key=lambda r: r["time"], reverse=True)
    return results


async def _label_values(label: str, selector: str = "") -> list[str]:
    async with httpx.AsyncClient(timeout=10) as client:
        params = {}
        if selector:
            params["query"] = selector
        r = await client.get(
            f"{LOKI_URL}/loki/api/v1/label/{label}/values",
            params=params,
        )
        r.raise_for_status()
    return sorted(r.json().get("data", []))


def _format_table(rows: list[dict], keys: list[str]) -> str:
    if not rows:
        return "No results."
    str_rows = [[str(row.get(k, "")) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    return "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


def _format_logs(results: list[dict]) -> str:
    if not results:
        return "No log entries found."
    lines = []
    for r in results:
        prefix = f"[{r['time']}] [{r['host']}/{r['service']}]"
        if r["level"]:
            prefix += f" {r['level'].upper()}"
        lines.append(f"{prefix}: {r['message']}")
    return "\n".join(lines) + f"\n\n({len(results)} entries)"


@mcp.tool
async def logs_search(
    query: str | None = None,
    host: str | None = None,
    source: str | None = None,
    level: str | None = None,
    service: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> str:
    """Search logs across all hosts and services.

    Args:
        query: Text to search for in log lines (substring match)
        host: Filter by host (nas, mum-hub, parish-server, mail, etc.)
        source: Filter by source type (journald, container, wordpress)
        level: Filter by log level (error, warning, info, debug)
        service: Filter by service/container name
        start: Start time (ISO 8601, default: 1 hour ago)
        end: End time (ISO 8601, default: now)
        limit: Maximum number of log lines to return (default 100, max 500)
    """
    limit = min(limit, 500)
    selector = _build_selector(host, source, level, service)
    logql = selector
    if query:
        logql += f' |= "{query}"'

    now = _now()
    s = _iso_or_default(start, now - timedelta(hours=1))
    e = _iso_or_default(end, now)

    results = await _query_range(logql, s, e, limit)
    return _format_logs(results)


@mcp.tool
async def logs_hosts() -> str:
    """List all hosts currently sending logs to Loki."""
    hosts = await _label_values("host")
    if not hosts:
        return "No hosts found."
    return "Hosts sending logs:\n" + "\n".join(f"  - {h}" for h in hosts)


@mcp.tool
async def logs_services(host: str | None = None) -> str:
    """List services/containers logging to Loki.

    Args:
        host: Optional host to filter by
    """
    selector = _build_selector(host=host) if host else ""
    services = await _label_values("service", selector)
    if not services:
        return "No services found."
    label = f" on {host}" if host else ""
    return f"Services{label}:\n" + "\n".join(f"  - {s}" for s in services)


@mcp.tool
async def logs_volume(
    host: str | None = None,
    service: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> str:
    """Get log volume over time, grouped by level. Useful for spotting error spikes.

    Args:
        host: Filter by host
        service: Filter by service
        start: Start time (ISO 8601, default: 6 hours ago)
        end: End time (ISO 8601, default: now)
    """
    selector = _build_selector(host=host, service=service)
    logql = f'sum(count_over_time({selector} [15m])) by (level)'

    now = _now()
    s = _iso_or_default(start, now - timedelta(hours=6))
    e = _iso_or_default(end, now)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": logql, "start": s, "end": e, "step": "15m"},
        )
        r.raise_for_status()
        data = r.json()

    results = data.get("data", {}).get("result", [])
    if not results:
        return "No log volume data found."

    lines = []
    scope = f"host={host}" if host else "all hosts"
    if service:
        scope += f", service={service}"
    lines.append(f"Log volume ({scope}):\n")

    for series in results:
        level = series.get("metric", {}).get("level", "unknown")
        values = series.get("values", [])
        total = sum(float(v[1]) for v in values)
        lines.append(f"  {level}: {int(total)} entries")

    return "\n".join(lines)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
