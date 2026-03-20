"""MCP server for read-only Cronicle job scheduler access."""

import os
from datetime import datetime, timezone

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan

_CRONICLE_URL = os.environ.get("CRONICLE_URL", "http://localhost:3012")
_CRONICLE_KEY = os.environ.get("CRONICLE_API_KEY", "")


@lifespan
async def cronicle_lifespan(server):
    client = httpx.AsyncClient(timeout=15.0)
    # Cache categories at startup
    resp = await client.get(
        f"{_CRONICLE_URL}/api/app/get_categories/v1",
        params={"api_key": _CRONICLE_KEY},
    )
    resp.raise_for_status()
    cats = {c["id"]: c["title"] for c in resp.json().get("rows", [])}
    yield {"client": client, "categories": cats}
    await client.aclose()


mcp = FastMCP("cronicle", lifespan=cronicle_lifespan)


def _api(path: str, **params) -> tuple[httpx.AsyncClient, str, dict]:
    ctx = get_context()
    client = ctx.lifespan_context["client"]
    params["api_key"] = _CRONICLE_KEY
    return client, f"{_CRONICLE_URL}/api/app/{path}/v1", params


def _cats() -> dict:
    return get_context().lifespan_context["categories"]


def _ts(epoch) -> str:
    """Convert epoch to readable datetime."""
    if not epoch:
        return "—"
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return str(epoch)


@mcp.tool
async def cronicle_list_jobs(
    category: str | None = None,
    enabled_only: bool = False,
) -> str:
    """List all scheduled jobs in Cronicle.

    Args:
        category: Optional category name filter (case-insensitive substring)
        enabled_only: Only show enabled jobs
    """
    client, url, params = _api("get_schedule")
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])

    cats = _cats()

    if enabled_only:
        rows = [r for r in rows if r.get("enabled")]
    if category:
        cat_lower = category.lower()
        rows = [r for r in rows if cat_lower in cats.get(r.get("category", ""), "").lower()]

    if not rows:
        return "No jobs found."

    lines = [f"Scheduled Jobs ({len(rows)}):\n"]
    for r in rows:
        cat_name = cats.get(r.get("category", ""), "—")
        enabled = "enabled" if r.get("enabled") else "disabled"
        tz = r.get("timezone", "UTC")
        timing = r.get("timing", {})
        hours = timing.get("hours", [])
        hours_str = ", ".join(f"{h:02d}:00" for h in sorted(hours)) if hours else "—"

        lines.append(
            f"  [{r['id']}] {r['title']} ({enabled})\n"
            f"       Category: {cat_name} | Schedule: {hours_str} {tz}\n"
            f"       Timeout: {r.get('timeout', '—')}s"
        )

    return "\n".join(lines)


@mcp.tool
async def cronicle_job_history(
    title: str | None = None,
    event_id: str | None = None,
    limit: int = 25,
) -> str:
    """Get recent job run history from Cronicle.

    Args:
        title: Filter by job title (case-insensitive substring)
        event_id: Filter by specific event ID
        limit: Max results (default 25)
    """
    client, url, params = _api("get_history", limit=min(limit, 100))
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])

    if title:
        title_lower = title.lower()
        rows = [r for r in rows if title_lower in r.get("event_title", "").lower()]
    if event_id:
        rows = [r for r in rows if r.get("event") == event_id]

    if not rows:
        return "No history found."

    lines = [f"Job History ({len(rows)} runs):\n"]
    for r in rows:
        code = r.get("code", -1)
        status = "OK" if code == 0 else f"FAILED (code {code})"
        elapsed = r.get("elapsed", 0)
        if isinstance(elapsed, (int, float)):
            elapsed_str = f"{elapsed:.1f}s"
        else:
            elapsed_str = str(elapsed)

        lines.append(
            f"  {_ts(r.get('epoch'))} | {r.get('event_title', '?')} | {status} | {elapsed_str}"
        )

    return "\n".join(lines)


@mcp.tool
async def cronicle_failed_jobs(
    hours: int = 24,
    limit: int = 50,
) -> str:
    """Get recently failed jobs from Cronicle.

    Args:
        hours: Look back this many hours (default 24)
        limit: Max results (default 50)
    """
    client, url, params = _api("get_history", limit=min(limit, 200))
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])

    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    failed = [
        r for r in rows
        if r.get("code", 0) != 0 and float(r.get("epoch", 0)) >= cutoff
    ]

    if not failed:
        return f"No failed jobs in the last {hours} hours."

    lines = [f"Failed Jobs (last {hours}h): {len(failed)} failures\n"]
    for r in failed:
        elapsed = r.get("elapsed", 0)
        elapsed_str = f"{elapsed:.1f}s" if isinstance(elapsed, (int, float)) else str(elapsed)
        lines.append(
            f"  {_ts(r.get('epoch'))} | {r.get('event_title', '?')} | code {r.get('code')} | {elapsed_str}"
        )

    return "\n".join(lines)


@mcp.tool
async def cronicle_get_job(event_id: str) -> str:
    """Get full details of a specific scheduled job.

    Args:
        event_id: The event ID from cronicle_list_jobs
    """
    client, url, params = _api("get_event", id=event_id)
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    event = resp.json().get("event", {})

    if not event:
        return f"Event {event_id} not found."

    cats = _cats()
    cat_name = cats.get(event.get("category", ""), "—")
    enabled = "enabled" if event.get("enabled") else "disabled"
    timing = event.get("timing", {})
    hours = timing.get("hours", [])
    hours_str = ", ".join(f"{h:02d}:00" for h in sorted(hours)) if hours else "—"

    script = event.get("params", {}).get("script", "—")

    return (
        f"# {event.get('title', 'Untitled')}\n\n"
        f"**ID:** {event.get('id')}\n"
        f"**Status:** {enabled}\n"
        f"**Category:** {cat_name}\n"
        f"**Schedule:** {hours_str} {event.get('timezone', 'UTC')}\n"
        f"**Timeout:** {event.get('timeout', '—')}s\n"
        f"**Target:** {event.get('target', '—')}\n"
        f"**Created:** {_ts(event.get('created'))}\n"
        f"**Modified:** {_ts(event.get('modified'))}\n\n"
        f"**Script:**\n```\n{script}\n```"
    )


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
