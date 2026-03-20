"""MCP server for read-only Healthchecks monitoring access."""

import os

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan

_HC_URL = os.environ.get("HEALTHCHECKS_URL", "https://hc.mees.st")
_HC_KEY = os.environ.get("HEALTHCHECKS_API_KEY", "")


@lifespan
async def hc_lifespan(server):
    client = httpx.AsyncClient(
        headers={"X-Api-Key": _HC_KEY},
        timeout=15.0,
    )
    yield {"client": client}
    await client.aclose()


mcp = FastMCP("healthchecks", lifespan=hc_lifespan)


def _client() -> httpx.AsyncClient:
    return get_context().lifespan_context["client"]


def _status_icon(status: str) -> str:
    return {"up": "UP", "down": "DOWN", "grace": "GRACE", "paused": "PAUSED", "new": "NEW"}.get(
        status, status.upper()
    )


@mcp.tool
async def hc_list_checks(
    tag: str | None = None,
    status: str | None = None,
) -> str:
    """List all healthcheck monitors with their current status.

    Args:
        tag: Filter by tag (case-insensitive substring)
        status: Filter by status: 'up', 'down', 'grace', 'paused'
    """
    client = _client()
    params = {}
    if tag:
        params["tag"] = tag

    resp = await client.get(f"{_HC_URL}/api/v3/checks/", params=params)
    resp.raise_for_status()
    checks = resp.json().get("checks", [])

    if status:
        checks = [c for c in checks if c.get("status") == status]

    if not checks:
        return "No checks found."

    # Sort: down first, then grace, then up
    order = {"down": 0, "grace": 1, "new": 2, "paused": 3, "up": 4}
    checks.sort(key=lambda c: (order.get(c.get("status", ""), 5), c.get("name", "")))

    lines = [f"Healthchecks ({len(checks)}):\n"]
    for c in checks:
        st = _status_icon(c.get("status", "?"))
        last = c.get("last_ping", "never")
        if last and last != "never":
            last = last[:19].replace("T", " ")
        tags = ", ".join(c.get("tags", "").split()) if c.get("tags") else ""
        dur = c.get("last_duration")
        dur_str = f" ({dur}s)" if dur else ""

        lines.append(
            f"  [{st:6s}] {c['name']}{dur_str}\n"
            f"           Last ping: {last} | Tags: {tags or '—'}"
        )

    return "\n".join(lines)


@mcp.tool
async def hc_check_status(name: str) -> str:
    """Get detailed status of a specific healthcheck by name.

    Args:
        name: Check name (case-insensitive substring match)
    """
    client = _client()
    resp = await client.get(f"{_HC_URL}/api/v3/checks/")
    resp.raise_for_status()
    checks = resp.json().get("checks", [])

    name_lower = name.lower()
    matches = [c for c in checks if name_lower in c.get("name", "").lower()]

    if not matches:
        return f"No check matching '{name}' found."

    lines = []
    for c in matches:
        st = _status_icon(c.get("status", "?"))
        lines.append(
            f"# {c['name']}\n\n"
            f"**Status:** {st}\n"
            f"**UUID:** {c.get('uuid', '—')}\n"
            f"**Tags:** {c.get('tags', '—')}\n"
            f"**Schedule:** {c.get('schedule', '—')} ({c.get('tz', 'UTC')})\n"
            f"**Grace period:** {c.get('grace', '—')}s\n"
            f"**Last ping:** {c.get('last_ping', 'never')}\n"
            f"**Next expected:** {c.get('next_ping', '—')}\n"
            f"**Last duration:** {c.get('last_duration', '—')}s\n"
            f"**Total pings:** {c.get('n_pings', '—')}\n"
            f"**Description:** {c.get('desc', '—') or '—'}"
        )

    return "\n\n---\n\n".join(lines)


@mcp.tool
async def hc_failing_checks() -> str:
    """Get all checks that are currently down or in grace period."""
    client = _client()
    resp = await client.get(f"{_HC_URL}/api/v3/checks/")
    resp.raise_for_status()
    checks = resp.json().get("checks", [])

    failing = [c for c in checks if c.get("status") in ("down", "grace")]

    if not failing:
        return "All checks are healthy."

    lines = [f"Failing Checks ({len(failing)}):\n"]
    for c in failing:
        st = _status_icon(c.get("status", "?"))
        last = c.get("last_ping", "never")
        if last and last != "never":
            last = last[:19].replace("T", " ")
        schedule = c.get("schedule", "—")

        lines.append(
            f"  [{st:6s}] {c['name']}\n"
            f"           Last ping: {last} | Schedule: {schedule}\n"
            f"           Expected: {c.get('next_ping', '—')}"
        )

    return "\n".join(lines)


@mcp.tool
async def hc_ping_history(name: str, limit: int = 10) -> str:
    """Get recent ping history for a specific check.

    Args:
        name: Check name (case-insensitive substring match)
        limit: Number of pings to show (default 10)
    """
    client = _client()
    # First find the check
    resp = await client.get(f"{_HC_URL}/api/v3/checks/")
    resp.raise_for_status()
    checks = resp.json().get("checks", [])

    name_lower = name.lower()
    matches = [c for c in checks if name_lower in c.get("name", "").lower()]

    if not matches:
        return f"No check matching '{name}' found."

    check = matches[0]
    uuid = check.get("uuid")

    # Fetch pings
    resp = await client.get(f"{_HC_URL}/api/v3/checks/{uuid}/pings/")
    resp.raise_for_status()
    pings = resp.json().get("pings", [])[:limit]

    if not pings:
        return f"No ping history for '{check['name']}'."

    lines = [f"Ping history for '{check['name']}' (last {len(pings)}):\n"]
    for p in pings:
        kind = p.get("type", "?")
        dt = p.get("date", "?")
        if dt and len(dt) > 19:
            dt = dt[:19].replace("T", " ")
        duration = p.get("duration")
        dur_str = f" ({duration}s)" if duration else ""
        lines.append(f"  {dt} | {kind}{dur_str}")

    return "\n".join(lines)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
