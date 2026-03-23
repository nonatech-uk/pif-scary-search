"""MCP server for read-only Immich photo library access."""

import os

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan

_IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server:2283")
_IMMICH_KEY = os.environ["IMMICH_API_KEY"]


@lifespan
async def immich_lifespan(server):
    client = httpx.AsyncClient(
        headers={"x-api-key": _IMMICH_KEY},
        timeout=30.0,
    )
    yield {"client": client}
    await client.aclose()


mcp = FastMCP("immich", lifespan=immich_lifespan)


def _client() -> httpx.AsyncClient:
    return get_context().lifespan_context["client"]


def _format_table(rows: list[dict], keys: list[str]) -> str:
    if not rows:
        return "No results."
    str_rows = [[str(row.get(k, "")) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    return "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


def _format_asset(a: dict) -> dict:
    info = a.get("exifInfo") or {}
    taken = a.get("localDateTime") or a.get("createdAt") or ""
    if taken and len(taken) > 19:
        taken = taken[:19].replace("T", " ")
    city = info.get("city") or ""
    country = info.get("country") or ""
    location = ", ".join(filter(None, [city, country])) or "—"
    w = info.get("exifImageWidth") or ""
    h = info.get("exifImageHeight") or ""
    dims = f"{w}x{h}" if w and h else "—"
    return {
        "filename": a.get("originalFileName") or "—",
        "date": taken,
        "dims": dims,
        "location": location,
        "type": a.get("type") or "—",
        "id": a.get("id") or "",
    }


def _format_assets(assets: list[dict], header: str) -> str:
    rows = [_format_asset(a) for a in assets]
    table = _format_table(rows, ["filename", "date", "dims", "location", "type"])
    ids = [r["id"] for r in rows if r["id"]]
    footer = f"\nAsset IDs: {', '.join(ids)}" if ids else ""
    return header + table + footer


@mcp.tool
async def immich_search(
    query: str,
    page: int = 1,
    size: int = 25,
) -> str:
    """Search photos using natural language (CLIP embeddings).

    Finds photos matching descriptive queries like "sunset on the beach",
    "birthday cake", or "cat sleeping on sofa".

    Args:
        query: Natural language search text
        page: Page number for pagination (default 1)
        size: Results per page (default 25, max 100)
    """
    client = _client()
    resp = await client.post(
        f"{_IMMICH_URL}/api/search/smart",
        json={"query": query, "page": page, "size": min(size, 100)},
    )
    if resp.status_code == 500:
        return "Smart search unavailable (Immich machine-learning service may not be running). Use immich_search_metadata instead."
    resp.raise_for_status()
    data = resp.json()

    items = data.get("assets", {}).get("items", [])
    total = data.get("assets", {}).get("total", 0)
    count = len(items)

    if not items:
        return f"No photos found matching '{query}'."

    header = f"Smart search for '{query}' (page {page}, {count} of {total}):\n\n"
    return _format_assets(items, header)


@mcp.tool
async def immich_search_metadata(
    original_file_name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    make: str | None = None,
    model: str | None = None,
    taken_after: str | None = None,
    taken_before: str | None = None,
    type: str | None = None,
    page: int = 1,
    size: int = 25,
) -> str:
    """Search photos by metadata filters (date, location, camera, filename).

    Args:
        original_file_name: Filter by original filename (substring match)
        city: Filter by city name from EXIF GPS data
        state: Filter by state/region from EXIF GPS data
        country: Filter by country from EXIF GPS data
        make: Filter by camera manufacturer (e.g. "Apple", "Sony")
        model: Filter by camera model (e.g. "iPhone 15 Pro")
        taken_after: Only photos taken after this date (ISO format, e.g. "2024-01-01")
        taken_before: Only photos taken before this date (ISO format, e.g. "2024-12-31")
        type: Asset type — "IMAGE" or "VIDEO"
        page: Page number for pagination (default 1)
        size: Results per page (default 25, max 100)
    """
    field_map = {
        "original_file_name": "originalFileName",
        "city": "city",
        "state": "state",
        "country": "country",
        "make": "make",
        "model": "model",
        "taken_after": "takenAfter",
        "taken_before": "takenBefore",
        "type": "type",
    }
    params = locals()
    body: dict = {"page": page, "size": min(size, 100)}
    for param, api_field in field_map.items():
        val = params[param]
        if val is not None:
            body[api_field] = val

    client = _client()
    resp = await client.post(
        f"{_IMMICH_URL}/api/search/metadata",
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("assets", {}).get("items", [])
    total = data.get("assets", {}).get("total", 0)
    count = len(items)

    if not items:
        return "No photos found matching the given filters."

    header = f"Metadata search (page {page}, {count} of {total}):\n\n"
    return _format_assets(items, header)


@mcp.tool
async def immich_albums(
    album_id: str | None = None,
) -> str:
    """List all albums or get details of a specific album.

    Args:
        album_id: Optional album UUID. Omit to list all albums; provide to get album details with assets.
    """
    client = _client()

    if album_id:
        resp = await client.get(f"{_IMMICH_URL}/api/albums/{album_id}")
        resp.raise_for_status()
        album = resp.json()

        created = (album.get("createdAt") or "")[:19].replace("T", " ")
        updated = (album.get("updatedAt") or "")[:19].replace("T", " ")
        header = (
            f"# {album.get('albumName', '—')}\n\n"
            f"**Assets:** {album.get('assetCount', 0)} | "
            f"**Created:** {created} | **Updated:** {updated}\n\n"
        )

        assets = album.get("assets", [])
        if not assets:
            return header + "No assets in this album."
        return _format_assets(assets, header)

    # List all albums
    resp = await client.get(f"{_IMMICH_URL}/api/albums")
    resp.raise_for_status()
    albums = resp.json()

    if not albums:
        return "No albums found."

    rows = []
    for a in albums:
        created = (a.get("createdAt") or "")[:19].replace("T", " ")
        updated = (a.get("updatedAt") or "")[:19].replace("T", " ")
        rows.append({
            "name": a.get("albumName") or "—",
            "assets": str(a.get("assetCount", 0)),
            "created": created,
            "updated": updated,
            "id": a.get("id") or "",
        })

    table = _format_table(rows, ["name", "assets", "created", "updated"])
    ids = "\n".join(f"  {r['name']}: {r['id']}" for r in rows if r["id"])
    return f"Albums ({len(rows)}):\n\n{table}\n\nAlbum IDs:\n{ids}"


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
