"""MCP server for Plex Media Server — library browsing, metadata, playlists, sessions, playback control."""

import json
import os

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan

_PLEX_URL = os.environ["PLEX_URL"]
_PLEX_TOKEN = os.environ["PLEX_TOKEN"]
_machine_id: str | None = None


@lifespan
async def plex_lifespan(server):
    client = httpx.AsyncClient(
        timeout=15.0,
        headers={"X-Plex-Token": _PLEX_TOKEN, "Accept": "application/json"},
    )
    yield {"client": client}
    await client.aclose()


mcp = FastMCP("plex", lifespan=plex_lifespan)


def _client() -> httpx.AsyncClient:
    return get_context().lifespan_context["client"]


async def _plex_get(path: str, params: dict | None = None) -> dict:
    resp = await _client().get(f"{_PLEX_URL}{path}", params=params)
    resp.raise_for_status()
    return resp.json()["MediaContainer"]


async def _plex_put(path: str, params: dict | None = None) -> dict:
    resp = await _client().put(f"{_PLEX_URL}{path}", params=params)
    resp.raise_for_status()
    return resp.json()["MediaContainer"]


async def _plex_post(path: str, params: dict | None = None) -> dict:
    resp = await _client().post(f"{_PLEX_URL}{path}", params=params)
    resp.raise_for_status()
    return resp.json()["MediaContainer"]


async def _plex_delete(path: str, params: dict | None = None) -> None:
    resp = await _client().delete(f"{_PLEX_URL}{path}", params=params)
    resp.raise_for_status()


async def _get_machine_id() -> str:
    global _machine_id
    if _machine_id is None:
        data = await _plex_get("/")
        _machine_id = data["machineIdentifier"]
    return _machine_id


def _item_uri(rating_key: str, machine_id: str) -> str:
    return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{rating_key}"


# --- Formatters ---

def _fmt_duration(ms: int) -> str:
    seconds = ms // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _fmt_item(m: dict, detailed: bool = False) -> dict:
    """Format a Plex metadata item based on its type."""
    t = m.get("type", "unknown")
    r: dict = {"ratingKey": m.get("ratingKey"), "type": t, "title": m.get("title", "")}

    if t == "movie":
        r["year"] = m.get("year")
        r["duration"] = _fmt_duration(m.get("duration", 0))
        r["rating"] = m.get("rating")
        r["contentRating"] = m.get("contentRating")
        if detailed:
            r["summary"] = m.get("summary")
            r["studio"] = m.get("studio")
            r["genres"] = [g["tag"] for g in m.get("Genre", [])]
            r["directors"] = [d["tag"] for d in m.get("Director", [])]
            r["cast"] = [a["tag"] for a in m.get("Role", [])][:10]
            media = m.get("Media", [{}])[0] if m.get("Media") else {}
            r["resolution"] = media.get("videoResolution")
            r["videoCodec"] = media.get("videoCodec")
            r["audioCodec"] = media.get("audioCodec")

    elif t == "show":
        r["year"] = m.get("year")
        r["rating"] = m.get("rating")
        r["childCount"] = m.get("childCount")  # seasons
        r["leafCount"] = m.get("leafCount")  # episodes
        if detailed:
            r["summary"] = m.get("summary")
            r["studio"] = m.get("studio")
            r["genres"] = [g["tag"] for g in m.get("Genre", [])]

    elif t == "season":
        r["parentTitle"] = m.get("parentTitle")
        r["index"] = m.get("index")
        r["leafCount"] = m.get("leafCount")

    elif t == "episode":
        r["grandparentTitle"] = m.get("grandparentTitle")
        r["parentIndex"] = m.get("parentIndex")
        r["index"] = m.get("index")
        if detailed:
            r["summary"] = m.get("summary")
            r["originallyAvailableAt"] = m.get("originallyAvailableAt")
            r["directors"] = [d["tag"] for d in m.get("Director", [])]
            r["writers"] = [w["tag"] for w in m.get("Writer", [])]
            r["duration"] = _fmt_duration(m.get("duration", 0))

    elif t == "artist":
        r["genres"] = [g["tag"] for g in m.get("Genre", [])] if m.get("Genre") else None
        if detailed:
            r["summary"] = m.get("summary")

    elif t == "album":
        r["parentTitle"] = m.get("parentTitle")
        r["year"] = m.get("year")
        r["leafCount"] = m.get("leafCount")

    elif t == "track":
        r["grandparentTitle"] = m.get("grandparentTitle")
        r["parentTitle"] = m.get("parentTitle")
        r["index"] = m.get("index")
        r["duration"] = _fmt_duration(m.get("duration", 0))

    elif t == "collection":
        r["childCount"] = m.get("childCount")
        r["subtype"] = m.get("subtype")
        if detailed:
            r["summary"] = m.get("summary")

    elif t == "playlist":
        r["playlistType"] = m.get("playlistType")
        r["smart"] = m.get("smart", False)
        r["leafCount"] = m.get("leafCount")
        r["duration"] = _fmt_duration(m.get("duration", 0))
        if detailed:
            r["summary"] = m.get("summary")

    return r


def _fmt_session(s: dict) -> dict:
    """Format an active session."""
    user = s.get("User", {})
    player = s.get("Player", {})
    session = s.get("Session", {})
    transcode = s.get("TranscodeSession", {})

    r = {
        "user": user.get("title", "unknown"),
        "title": s.get("grandparentTitle", s.get("title", "")),
        "type": s.get("type"),
        "player": player.get("title", "unknown"),
        "device": player.get("device"),
        "clientIdentifier": player.get("machineIdentifier"),
        "state": player.get("state", "unknown"),
    }

    if s.get("grandparentTitle"):
        r["title"] = f"{s['grandparentTitle']} - {s.get('title', '')}"

    duration = s.get("duration", 0)
    view_offset = s.get("viewOffset", 0)
    if duration:
        r["progress"] = f"{round(view_offset / duration * 100)}%"
        r["remaining"] = _fmt_duration(duration - view_offset)

    if transcode:
        r["decision"] = transcode.get("transcodeHwDecoding", "unknown")
        r["videoDecision"] = transcode.get("videoDecision", "direct play")
        r["audioDecision"] = transcode.get("audioDecision", "direct play")
    else:
        r["decision"] = "direct play"

    bandwidth = session.get("bandwidth")
    if bandwidth:
        r["bandwidth"] = f"{bandwidth} kbps"

    return r


# --- Tools ---

@mcp.tool()
async def plex_libraries(
    action: str = "list",
    section_id: str | None = None,
    limit: int = 50,
    genre: str | None = None,
    year: int | None = None,
) -> str:
    """Browse Plex library sections.

    Args:
        action: 'list' all sections, 'browse' a section's contents, 'recent' for recently added, 'ondeck' for continue watching.
        section_id: Library section ID (required for 'browse' and 'recent').
        limit: Max items to return (default 50).
        genre: Filter by genre name (for 'browse').
        year: Filter by year (for 'browse').
    """
    match action:
        case "list":
            data = await _plex_get("/library/sections")
            sections = []
            for d in data.get("Directory", []):
                sections.append({
                    "key": d["key"],
                    "title": d["title"],
                    "type": d["type"],
                })
            return json.dumps(sections, indent=2)

        case "browse":
            if not section_id:
                return "section_id is required for 'browse' action."
            params: dict = {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit}
            if genre:
                params["genre"] = genre
            if year:
                params["year"] = year
            data = await _plex_get(f"/library/sections/{section_id}/all", params=params)
            total = data.get("totalSize", data.get("size", 0))
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            header = f"Showing {len(items)} of {total} items:\n\n"
            return header + json.dumps(items, indent=2)

        case "recent":
            if not section_id:
                return "section_id is required for 'recent' action."
            data = await _plex_get(
                f"/library/sections/{section_id}/recentlyAdded",
                params={"X-Plex-Container-Size": limit},
            )
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2)

        case "ondeck":
            data = await _plex_get("/library/onDeck", params={"X-Plex-Container-Size": limit})
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2) if items else "Nothing on deck."

        case _:
            return f"Unknown action: {action}. Use 'list', 'browse', 'recent', or 'ondeck'."


@mcp.tool()
async def plex_search(query: str, media_type: str | None = None, limit: int = 20) -> str:
    """Search across all Plex libraries.

    Args:
        query: Search query.
        media_type: Optional filter — 'movie', 'show', 'episode', 'artist', 'album', 'track'.
        limit: Max results (default 20).
    """
    params: dict = {"query": query}
    if media_type:
        type_map = {"movie": 1, "show": 2, "season": 3, "episode": 4, "artist": 8, "album": 9, "track": 10}
        if media_type in type_map:
            params["type"] = type_map[media_type]
    data = await _plex_get("/search", params=params)
    items = [_fmt_item(m) for m in data.get("Metadata", [])][:limit]
    return json.dumps(items, indent=2) if items else "No results found."


@mcp.tool()
async def plex_get_info(rating_key: str) -> str:
    """Get detailed metadata for a Plex item by its rating key.

    Args:
        rating_key: The Plex rating key (item ID).
    """
    data = await _plex_get(f"/library/metadata/{rating_key}")
    items = data.get("Metadata", [])
    if not items:
        return f"No item found with rating key {rating_key}."
    item = _fmt_item(items[0], detailed=True)

    # For shows, also fetch seasons
    if items[0].get("type") == "show":
        children = await _plex_get(f"/library/metadata/{rating_key}/children")
        item["seasons"] = [_fmt_item(s) for s in children.get("Metadata", [])]

    return json.dumps(item, indent=2)


@mcp.tool()
async def plex_collections(
    action: str = "list",
    section_id: str | None = None,
    collection_id: str | None = None,
) -> str:
    """Browse Plex collections.

    Args:
        action: 'list' collections in a section, 'items' to view collection members.
        section_id: Library section ID (required for 'list').
        collection_id: Collection rating key (required for 'items').
    """
    match action:
        case "list":
            if not section_id:
                return "section_id is required for 'list' action."
            data = await _plex_get(f"/library/sections/{section_id}/collections")
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2) if items else "No collections in this section."

        case "items":
            if not collection_id:
                return "collection_id is required for 'items' action."
            data = await _plex_get(f"/library/collections/{collection_id}/children")
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2) if items else "Collection is empty."

        case _:
            return f"Unknown action: {action}. Use 'list' or 'items'."


@mcp.tool()
async def plex_sessions() -> str:
    """Get current active Plex streams. Shows who is watching what, on which device, with transcode info."""
    data = await _plex_get("/status/sessions")
    sessions = [_fmt_session(s) for s in data.get("Metadata", [])]
    return json.dumps(sessions, indent=2) if sessions else "No active sessions."


@mcp.tool()
async def plex_playlists(
    action: str = "list",
    playlist_id: str | None = None,
    title: str | None = None,
    playlist_type: str = "video",
    rating_keys: list[str] | None = None,
) -> str:
    """Manage Plex playlists.

    Args:
        action: 'list', 'view', 'create', 'add_items', 'remove_items'.
        playlist_id: Playlist rating key (required for view, add_items, remove_items).
        title: Playlist title (required for create).
        playlist_type: Type for create — 'video' or 'audio' (default 'video').
        rating_keys: List of item rating keys (for create, add_items, remove_items).
    """
    match action:
        case "list":
            data = await _plex_get("/playlists")
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2) if items else "No playlists."

        case "view":
            if not playlist_id:
                return "playlist_id is required for 'view' action."
            data = await _plex_get(f"/playlists/{playlist_id}/items")
            items = [_fmt_item(m) for m in data.get("Metadata", [])]
            return json.dumps(items, indent=2) if items else "Playlist is empty."

        case "create":
            if not title:
                return "title is required for 'create' action."
            machine_id = await _get_machine_id()
            params: dict = {"type": playlist_type, "title": title}
            if rating_keys:
                uris = ",".join(_item_uri(rk, machine_id) for rk in rating_keys)
                params["uri"] = uris
            data = await _plex_post("/playlists", params=params)
            items = data.get("Metadata", [])
            if items:
                return json.dumps(_fmt_item(items[0]), indent=2)
            return "Playlist created."

        case "add_items":
            if not playlist_id:
                return "playlist_id is required for 'add_items' action."
            if not rating_keys:
                return "rating_keys is required for 'add_items' action."
            machine_id = await _get_machine_id()
            uris = ",".join(_item_uri(rk, machine_id) for rk in rating_keys)
            await _plex_put(f"/playlists/{playlist_id}/items", params={"uri": uris})
            return f"Added {len(rating_keys)} item(s) to playlist."

        case "remove_items":
            if not playlist_id:
                return "playlist_id is required for 'remove_items' action."
            if not rating_keys:
                return "rating_keys is required for 'remove_items' action."
            for rk in rating_keys:
                # Plex removes by the playlist item's own key, which is the rating key
                await _plex_delete(f"/playlists/{playlist_id}/items/{rk}")
            return f"Removed {len(rating_keys)} item(s) from playlist."

        case _:
            return f"Unknown action: {action}. Use 'list', 'view', 'create', 'add_items', or 'remove_items'."


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
