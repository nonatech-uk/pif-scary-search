"""MCP server for Swiss public transport via transport.opendata.ch (HAFAS)."""

import os
import re
from datetime import datetime, timedelta

import httpx
from fastmcp import FastMCP

API_URL = os.environ.get("SBB_API_URL", "https://transport.opendata.ch/v1").rstrip("/")
USER_AGENT = "mcp-sbb/1.0 (+https://mees.st)"
HTTP_TIMEOUT = 15.0

mcp = FastMCP("sbb")


# ---------- helpers ----------

def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts)


def _fmt_time(ts: str | None) -> str:
    dt = _parse_iso(ts)
    return dt.strftime("%H:%M") if dt else "—"


def _fmt_date_header(ts: str | None) -> str:
    dt = _parse_iso(ts)
    return dt.strftime("%a %d %b %H:%M") if dt else ""


def _minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def _parse_duration(s: str | None) -> str:
    """HAFAS returns '00d00:56:00'; format as '56m' or '1h 4m'."""
    if not s:
        return "—"
    try:
        days_part, time_part = s.split("d")
        h, m, _sec = time_part.split(":")
        total_min = int(days_part) * 24 * 60 + int(h) * 60 + int(m)
        if total_min < 60:
            return f"{total_min}m"
        return f"{total_min // 60}h {total_min % 60}m"
    except (ValueError, AttributeError):
        return s


def _delay_marker(endpoint: dict) -> str:
    """Extract delay / cancellation marker from a journey endpoint."""
    if endpoint.get("prognosis", {}).get("capacity") == "CANCELLED":
        return " [CANCELLED]"
    # HAFAS uses 'delay' on the endpoint or prognosis.departure/arrival
    delay = endpoint.get("delay")
    if delay is None:
        prog = endpoint.get("prognosis") or {}
        delay = prog.get("delay")
    if delay and isinstance(delay, (int, float)) and delay != 0:
        sign = "+" if delay > 0 else ""
        return f" [{sign}{int(delay)}']"
    return ""


def _platform(endpoint: dict) -> str:
    p = endpoint.get("platform") or (endpoint.get("prognosis") or {}).get("platform")
    return f"plat {p}" if p else ""


async def _get(path: str, params: dict) -> dict:
    """Send a GET request to the Transport API, dropping None values."""
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(f"{API_URL}{path}", params=clean)
        r.raise_for_status()
        return r.json()


def _transportations_list(transportations: list[str] | str | None) -> list[str] | None:
    """Accept either a list or comma-separated string."""
    if transportations is None:
        return None
    if isinstance(transportations, str):
        return [t.strip() for t in transportations.split(",") if t.strip()]
    return transportations


# ---------- tools ----------

@mcp.tool
async def sbb_find_station(
    query: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    type_: str = "station",
    limit: int = 5,
) -> str:
    """Find Swiss public transport stations by name or coordinates.

    Provide either `query` (e.g. "Zurich HB", "Bern", "Geneve") or a
    `lat`/`lon` pair (WGS84). Use `type_` to filter: "station", "poi",
    "address", or "all". Useful as a lookup step before calling other
    tools that need an unambiguous station name or ID.
    """
    if not query and (lat is None or lon is None):
        return "sbb_find_station: provide either query= or both lat= and lon="

    # Transport API uses x=latitude, y=longitude (per its docs)
    async def _fetch(q: str | None, t: str) -> list[dict]:
        d = await _get("/locations", {"query": q, "x": lat, "y": lon, "type": t})
        return d.get("stations", [])

    results = await _fetch(query, type_)
    # API's type filter is unreliable (it returns POIs in the stations array
    # even when type=station). Stations have a numeric HAFAS id; POIs/addresses
    # don't — use that as the real filter.
    if type_ == "station":
        filtered = [s for s in results if s.get("id")]
        # Queries like "Zurich HB" match many POIs with "HB" in the name and
        # crowd the actual station out of the results entirely. If the
        # filtered list is empty, retry with common station suffixes stripped.
        if not filtered and query:
            simplified = re.sub(
                r"\b(HB|Hbf|Hauptbahnhof|Bahnhof|Station|Gare|Stazione)\b",
                "",
                query,
                flags=re.IGNORECASE,
            ).strip()
            if simplified and simplified != query:
                filtered = [s for s in await _fetch(simplified, "all") if s.get("id")]
        results = filtered
    stations = results[:limit]
    if not stations:
        label = f"'{query}'" if query else f"{lat},{lon}"
        return f"No stations found for {label}."

    label = f"'{query}'" if query else f"near {lat},{lon}"
    lines = [f"{label} — {len(stations)} matches"]
    for s in stations:
        coord = s.get("coordinate") or {}
        x, y = coord.get("x"), coord.get("y")
        coord_str = f"({x:.5f}, {y:.5f})" if isinstance(x, (int, float)) and isinstance(y, (int, float)) else ""
        extra = []
        if s.get("score") is not None:
            extra.append(f"score {s['score']}")
        if s.get("distance") is not None:
            extra.append(f"{int(s['distance'])}m away")
        extras = f"  {', '.join(extra)}" if extra else ""
        sid = s.get("id") or "—"
        name = s.get("name") or "—"
        lines.append(f"  {sid:>8}  {name:<30}  {coord_str}{extras}")
    return "\n".join(lines)


@mcp.tool
async def sbb_journey(
    origin: str,
    destination: str,
    via: list[str] | str | None = None,
    date: str | None = None,
    time: str | None = None,
    is_arrival_time: bool = False,
    transportations: list[str] | str | None = None,
    direct: bool = False,
    limit: int = 4,
) -> str:
    """Plan a journey between two Swiss stations.

    `origin` and `destination` can be station names ("Zurich HB") or IDs.
    Optional: `via` (1-5 intermediate stops), `date` (YYYY-MM-DD), `time`
    (HH:MM), `is_arrival_time` (treat time as arrival instead of
    departure), `transportations` (filter — any of: ice_tgv_rj, ec_ic,
    ir, re_d, ship, bus, cableway, arz_ext, tramway_underground),
    `direct` (no changes), `limit` (1-16).
    """
    via_list = _transportations_list(via)
    params: dict = {
        "from": origin,
        "to": destination,
        "date": date,
        "time": time,
        "isArrivalTime": 1 if is_arrival_time else 0,
        "direct": 1 if direct else 0,
        "limit": limit,
    }
    if via_list:
        params["via[]"] = via_list
    tr = _transportations_list(transportations)
    if tr:
        params["transportations[]"] = tr

    data = await _get("/connections", params)
    connections = data.get("connections", [])
    if not connections:
        return f"No connections found from {origin} to {destination}."

    first = connections[0]
    header = f"{first['from']['station']['name']} → {first['to']['station']['name']}"
    header += f"   {_fmt_date_header(first['from'].get('departure'))}"
    lines = [header, ""]

    for i, c in enumerate(connections, 1):
        dep = c["from"]
        arr = c["to"]
        dep_time = _fmt_time(dep.get("departure"))
        arr_time = _fmt_time(arr.get("arrival"))
        dep_plat = _platform(dep)
        arr_plat = _platform(arr)
        duration = _parse_duration(c.get("duration"))
        transfers = c.get("transfers", 0)
        change_str = "direct" if transfers == 0 else f"{transfers} change{'s' if transfers != 1 else ''}"

        products = c.get("products") or []
        products_str = " + ".join(products) if products else ""

        markers = _delay_marker(dep) + _delay_marker(arr)

        lines.append(
            f"{i}. {dep_time} {dep['station']['name']} {dep_plat}".rstrip()
            + f"  →  {arr_time} {arr['station']['name']} {arr_plat}".rstrip()
            + f"   ({duration}, {change_str}){markers}"
        )
        if products_str:
            lines.append(f"   {products_str}")
        lines.append("")

    return "\n".join(lines).rstrip()


@mcp.tool
async def sbb_stationboard(
    station: str,
    limit: int = 10,
    kind: str = "departure",
    datetime_: str | None = None,
    transportations: list[str] | str | None = None,
) -> str:
    """Get the next departures or arrivals at a Swiss station.

    `station` is a name ("Zurich HB") or station ID. `kind` is
    "departure" (default) or "arrival". `limit` up to ~300.
    `datetime_` (YYYY-MM-DD HH:MM) to query a specific time.
    `transportations` filter as in sbb_journey.
    """
    params: dict = {
        "station": station,
        "limit": limit,
        "type": kind,
        "datetime": datetime_,
    }
    tr = _transportations_list(transportations)
    if tr:
        params["transportations[]"] = tr

    data = await _get("/stationboard", params)
    board = data.get("stationboard", [])
    st_name = (data.get("station") or {}).get("name") or station
    if not board:
        return f"{st_name}: no {kind}s found."

    verb = "departures" if kind == "departure" else "arrivals"
    lines = [f"{st_name} — next {len(board)} {verb}"]

    for entry in board:
        stop = entry.get("stop") or {}
        ts = stop.get(kind) or stop.get("departure") or stop.get("arrival")
        time_str = _fmt_time(ts)
        category = entry.get("category") or ""
        number = entry.get("number") or ""
        name = f"{category}{number}".strip() or entry.get("name") or ""
        to = entry.get("to") or ""
        plat = _platform(stop)
        markers = _delay_marker(stop)
        lines.append(
            f"  {time_str}  {name:<8} → {to:<30} {plat}{markers}".rstrip()
        )
    return "\n".join(lines)


@mcp.tool
async def sbb_disruptions(
    station: str,
    window_minutes: int = 60,
    kind: str = "departure",
) -> str:
    """Report delays and cancellations at a station in the next N minutes.

    Convenience wrapper over the station board that filters to entries
    with a non-zero delay or a cancellation flag. `kind` is "departure"
    or "arrival". Returns a short summary; empty when everything is
    running on time.
    """
    # Pull a generous board so we can filter to the time window
    data = await _get("/stationboard", {
        "station": station,
        "limit": 40,
        "type": kind,
    })
    board = data.get("stationboard", [])
    st_name = (data.get("station") or {}).get("name") or station
    if not board:
        return f"{st_name}: no {kind}s found."

    cutoff = datetime.now().astimezone() + timedelta(minutes=window_minutes)
    disrupted = []
    for entry in board:
        stop = entry.get("stop") or {}
        ts = _parse_iso(stop.get(kind) or stop.get("departure") or stop.get("arrival"))
        if ts and ts > cutoff:
            continue
        marker = _delay_marker(stop)
        if marker:
            disrupted.append((stop, entry, marker, ts))

    if not disrupted:
        return f"{st_name}: no disruptions in next {window_minutes}min."

    lines = [f"{st_name} — {len(disrupted)} disruption{'s' if len(disrupted) != 1 else ''} in next {window_minutes}min"]
    for stop, entry, marker, _ts in disrupted:
        ts = stop.get(kind) or stop.get("departure") or stop.get("arrival")
        time_str = _fmt_time(ts)
        category = entry.get("category") or ""
        number = entry.get("number") or ""
        name = f"{category}{number}".strip() or entry.get("name") or ""
        to = entry.get("to") or ""
        plat = _platform(stop)
        lines.append(f"  {time_str}  {name:<8} → {to:<30} {plat}{marker}".rstrip())
    return "\n".join(lines)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
