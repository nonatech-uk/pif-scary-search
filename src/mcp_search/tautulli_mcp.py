"""MCP server for read-only Tautulli (Plex stats) API access."""

import os

import httpx
from fastmcp import FastMCP

TAUTULLI_URL = os.environ.get("TAUTULLI_URL", "http://tautulli:8181")
TAUTULLI_API_KEY = os.environ["TAUTULLI_API_KEY"]

mcp = FastMCP("tautulli-search")


async def _api(cmd: str, **params) -> dict:
    params["apikey"] = TAUTULLI_API_KEY
    params["cmd"] = cmd
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{TAUTULLI_URL}/api/v2", params=params)
        r.raise_for_status()
        data = r.json()
    resp = data.get("response", {})
    if resp.get("result") != "success":
        raise ValueError(resp.get("message", "Unknown Tautulli error"))
    return resp.get("data", {})


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_table(rows: list[dict], keys: list[str]) -> str:
    if not rows:
        return "No results."
    str_rows = [[str(row.get(k, "")) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    return "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


@mcp.tool
async def tautulli_history(
    length: int = 25,
    user: str | None = None,
    media_type: str | None = None,
    search: str | None = None,
) -> str:
    """Get Plex watch history from Tautulli.

    Args:
        length: Number of records to return (default 25, max 100)
        user: Filter by username/friendly name
        media_type: Filter by type — 'movie', 'episode', 'track'
        search: Search for title
    """
    params = {"length": min(length, 100)}
    if user:
        params["user"] = user
    if media_type:
        params["media_type"] = media_type
    if search:
        params["search"] = search

    data = await _api("get_history", **params)
    rows = data.get("data", [])
    if not rows:
        return "No history found."

    from datetime import datetime, timezone

    result_rows = []
    for r in rows:
        ts = datetime.fromtimestamp(r["date"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        result_rows.append({
            "date": ts,
            "user": r.get("friendly_name", ""),
            "title": r.get("full_title", ""),
            "type": r.get("media_type", ""),
            "duration": _format_duration(r.get("play_duration", 0)),
            "platform": r.get("platform", ""),
            "decision": r.get("transcode_decision", ""),
        })

    total = data.get("recordsFiltered", len(rows))
    header = f"Watch history ({total} total records):\n\n"
    return header + _format_table(result_rows, ["date", "user", "title", "type", "duration", "platform", "decision"])


@mcp.tool
async def tautulli_most_watched(
    time_range: int = 30,
    media_type: str = "movie",
    stats_count: int = 25,
) -> str:
    """Get most-watched content from Plex over a time range.

    Args:
        time_range: Number of days to look back (default 30)
        media_type: 'movie', 'tv', or 'music'
        stats_count: Number of results (default 25)
    """
    stat_map = {"movie": "top_movies", "tv": "top_tv", "music": "top_music"}
    stat_id = stat_map.get(media_type, f"top_{media_type}")

    data = await _api(
        "get_home_stats",
        time_range=time_range,
        stats_type="duration",
        stats_count=stats_count,
    )

    target = None
    for section in data:
        if section.get("stat_id") == stat_id:
            target = section
            break

    if not target or not target.get("rows"):
        return f"No {media_type} stats found for the last {time_range} days."

    result_rows = []
    for r in target["rows"]:
        result_rows.append({
            "title": r.get("title", ""),
            "year": str(r.get("year", "")),
            "plays": str(r.get("total_plays", "")),
            "duration": _format_duration(r.get("total_duration", 0)),
            "users": str(r.get("users_watched", "")),
            "last_play": r.get("last_play", ""),
        })

    header = f"Most watched {media_type} (last {time_range} days):\n\n"
    return header + _format_table(result_rows, ["title", "year", "plays", "duration", "users", "last_play"])


@mcp.tool
async def tautulli_user_stats(
    user_id: int | None = None,
) -> str:
    """Get per-user watch statistics. If no user_id, lists all users with stats.

    Args:
        user_id: Optional Tautulli user_id. Omit to get summary for all users.
    """
    if user_id:
        data = await _api("get_user_watch_time_stats", user_id=user_id)
        if not data:
            return f"No stats found for user {user_id}."

        lines = [f"Watch stats for user {user_id}:\n"]
        for period in data:
            lines.append(
                f"  Last {period.get('query_days', '?')} days: "
                f"{period.get('total_plays', 0)} plays, "
                f"{_format_duration(period.get('total_time', 0))}"
            )
        return "\n".join(lines)

    # All users summary
    data = await _api("get_users")
    if not data:
        return "No users found."

    result_rows = []
    for u in data:
        result_rows.append({
            "user_id": str(u.get("user_id", "")),
            "name": u.get("friendly_name", ""),
            "last_seen": u.get("last_seen", "") or "never",
            "total_plays": str(u.get("plays", "")),
        })

    return _format_table(result_rows, ["user_id", "name", "last_seen", "total_plays"])


@mcp.tool
async def tautulli_watch_stats(
    time_range: int = 30,
    y_axis: str = "plays",
) -> str:
    """Get daily play count or duration stats over a time period.

    Args:
        time_range: Number of days to look back (default 30)
        y_axis: 'plays' for play count or 'duration' for total watch time
    """
    data = await _api("get_plays_by_date", time_range=time_range, y_axis=y_axis)
    if not data or not data.get("series"):
        return "No play data found."

    categories = data.get("categories", [])
    series = data.get("series", [])

    lines = [f"Daily {y_axis} (last {time_range} days):\n"]
    header_parts = ["date"]
    for s in series:
        header_parts.append(s.get("name", "?"))
    lines.append(" | ".join(header_parts))
    lines.append("-+-".join("-" * max(len(h), 10) for h in header_parts))

    for i, date in enumerate(categories):
        parts = [date]
        for s in series:
            val = s.get("data", [])[i] if i < len(s.get("data", [])) else 0
            if y_axis == "duration" and isinstance(val, (int, float)):
                parts.append(_format_duration(int(val)))
            else:
                parts.append(str(val))
        lines.append(" | ".join(parts))

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
