"""Curated ferry-route table — Channel + North Sea + Irish Sea.

Same static-data pattern as travel_eurostar.py / travel_eurotunnel.py.
Ferry operators don't expose public booking APIs; their websites can be
scraped but the legwork isn't worth it for trip-planning when the
durations are stable per season and pricing happens at click-through.

Maintenance: annual review against operator websites. New routes get a
single tuple in `_RAW`. Compiled into `ROUTES` at module load.

Output mirrors `travel_eurotunnel_check`:
  - terminal_to_terminal_minutes (crossing + terminal overhead)
  - access-leg constants (port → typical UK origins) where useful
  - booking_url constructed from a per-operator pattern
"""

from datetime import date as date_type, datetime
from typing import Any
from urllib.parse import urlencode


# Format:
# (origin_port, dest_port, country_to, operator, crossing_min,
#  daily_freq, terminal_overhead_min, vehicle_ok, seasonal, notes)
_RAW: list[tuple[str, str, str, str, int, str, int, bool, str | None, str]] = [
    # === English Channel — short crossings (Dover) ===
    ("Dover",       "Calais",         "FR", "DFDS",            90,  "very frequent", 60, True, None,
     "Most-used short Channel crossing"),
    ("Dover",       "Calais",         "FR", "P&O Ferries",     90,  "very frequent", 60, True, None,
     "Frequent rotation alongside DFDS"),
    ("Dover",       "Calais",         "FR", "Irish Ferries",   90,  "frequent",      60, True, None,
     "Newer Channel operator"),
    ("Dover",       "Dunkerque",      "FR", "DFDS",           120,  "frequent",      60, True, None,
     "Slightly longer crossing, often cheaper"),

    # === English Channel — Sussex ===
    ("Newhaven",    "Dieppe",         "FR", "DFDS",           240,  "3/day",         60, True, None,
     "Convenient for SE England → Normandy"),

    # === English Channel — Hampshire / Solent ===
    ("Portsmouth",  "Le Havre",       "FR", "Brittany Ferries", 300, "1-2/day",      60, True, None,
     "Day or overnight; Le Havre = good for Normandy/Paris"),
    ("Portsmouth",  "Caen (Ouistreham)","FR", "Brittany Ferries", 360, "2-3/day",    60, True, None,
     "Caen for Calvados/D-Day coast/Normandy"),
    ("Portsmouth",  "Cherbourg",      "FR", "Brittany Ferries", 180, "1-2/day",      60, True, None,
     "Fast craft — passenger + car (180 min); cruise ferry 600 min overnight"),
    ("Portsmouth",  "Saint-Malo",     "FR", "Brittany Ferries", 660, "1/day overnight", 60, True, None,
     "Overnight cruise to Brittany"),
    ("Poole",       "Cherbourg",      "FR", "Brittany Ferries", 270, "1/day fast",    60, True, "summer-mainly",
     "Fast craft from Poole; reduced winter service"),
    ("Plymouth",    "Roscoff",        "FR", "Brittany Ferries", 360, "1/day",         60, True, None,
     "Western entry into Brittany; some sailings overnight to 540 min"),

    # === Bay of Biscay (UK ↔ Spain) ===
    ("Portsmouth",  "Bilbao",         "ES", "Brittany Ferries", 1620, "2/week",       90, True, None,
     "27-hour cruise crossing"),
    ("Portsmouth",  "Santander",      "ES", "Brittany Ferries", 1380, "2/week",       90, True, None,
     "23-hour daytime; some sailings up to 40 h overnight"),
    ("Plymouth",    "Santander",      "ES", "Brittany Ferries", 1200, "1/week",       90, True, None,
     "20-hour cruise; weekly only in summer"),

    # === North Sea ===
    ("Hull",        "Rotterdam",      "NL", "P&O Ferries",     660, "1/day overnight", 90, True, None,
     "Hull → Rotterdam Europoort, overnight cruise"),
    ("Hull",        "Zeebrugge",      "BE", "P&O Ferries",     780, "1/day overnight", 90, True, None,
     "Hull → Zeebrugge for Belgium / N. Netherlands"),
    ("Newcastle",   "Amsterdam (IJmuiden)", "NL", "DFDS",       900, "1/day overnight", 90, True, None,
     "DFDS King Seaways / Princess Seaways — N. England → Amsterdam"),
    ("Harwich",     "Hook of Holland","NL", "Stena Line",      420, "2/day",          60, True, None,
     "Daytime + overnight"),

    # === Irish Sea — Wales ↔ Ireland ===
    ("Holyhead",    "Dublin",         "IE", "Irish Ferries",   210, "4/day",          60, True, None,
     "Standard Holyhead → Dublin Port; fast craft 120 min on some sailings"),
    ("Holyhead",    "Dublin",         "IE", "Stena Line",      200, "4/day",          60, True, None,
     "Stena Adventurer / Estrid — same route, different operator"),
    ("Pembroke Dock","Rosslare",      "IE", "Irish Ferries",   240, "2/day",          60, True, None,
     "S. Wales → SE Ireland, day + night sailings"),
    ("Fishguard",   "Rosslare",       "IE", "Stena Line",      210, "2/day",          60, True, None,
     "S. Wales → SE Ireland alternative"),

    # === Irish Sea — England ↔ Ireland ===
    ("Liverpool",   "Dublin",         "IE", "P&O Ferries",     480, "2/day",          60, True, None,
     "8h overnight cruise from Liverpool to Dublin Port"),

    # === Irish Sea — Scotland ↔ Northern Ireland ===
    ("Cairnryan",   "Larne",          "GB-NIR", "P&O Ferries", 120, "6/day",          45, True, None,
     "Shortest Scotland → NI crossing"),
    ("Cairnryan",   "Belfast",        "GB-NIR", "Stena Line",  135, "6/day",          45, True, None,
     "Stena Superfast / Spirit"),

    # === Irish Sea — England ↔ Northern Ireland ===
    ("Birkenhead",  "Belfast",        "GB-NIR", "Stena Line",  480, "2/day",          60, True, None,
     "Liverpool-area → Belfast overnight"),
    ("Heysham",     "Belfast",        "GB-NIR", "Stena Line",  480, "2/day",          60, True, None,
     "Lancashire → Belfast, mixed freight + passenger"),

    # === Irish Sea — Isle of Man ===
    ("Liverpool",   "Douglas",        "IM", "Steam Packet",    165, "1-2/day",        60, True, None,
     "Manannan fastcraft + Ben-my-Chree conventional"),
    ("Heysham",     "Douglas",        "IM", "Steam Packet",    210, "1-2/day",        60, True, None,
     "Year-round freight + passenger"),
    ("Belfast",     "Douglas",        "IM", "Steam Packet",    175, "summer only",    60, True, "summer-only",
     "Summer-only fastcraft NI ↔ IoM"),
    ("Dublin",      "Douglas",        "IM", "Steam Packet",    175, "summer only",    60, True, "summer-only",
     "Summer-only fastcraft IE ↔ IoM"),
]


def _booking_url(operator: str, orig: str, dest: str, date: str, vehicle: str, passengers: int) -> str:
    """Best-effort search-page URL for the operator (each does it differently)."""
    op_lower = operator.lower()
    if "dfds" in op_lower:
        return f"https://www.dfds.com/en/passenger-ferries?search=ferry&route={orig.replace(' ','+')}-{dest.replace(' ','+')}&outbound={date}"
    if "p&o" in op_lower or "p and o" in op_lower:
        return f"https://www.poferries.com/en/?destination={dest.replace(' ','-').lower()}&outbound={date}"
    if "brittany" in op_lower:
        return f"https://www.brittany-ferries.co.uk/?from={orig.replace(' ','-').lower()}&to={dest.replace(' ','-').lower()}&out={date}"
    if "irish" in op_lower:
        return f"https://www.irishferries.com/?from={orig.replace(' ','-').lower()}&to={dest.replace(' ','-').lower()}&date={date}"
    if "stena" in op_lower:
        return f"https://www.stenaline.co.uk/?from={orig.replace(' ','-').lower()}&to={dest.replace(' ','-').lower()}&out={date}"
    if "steam packet" in op_lower:
        return f"https://www.steam-packet.com/?route={orig.replace(' ','-').lower()}-{dest.replace(' ','-').lower()}&date={date}"
    return f"https://www.google.com/search?q={urlencode({'q': f'{operator} {orig} to {dest} {date}'})[2:]}"


ROUTES: list[dict[str, Any]] = [
    {
        "operator": op,
        "origin_port": orig,
        "dest_port": dest,
        "country_to": country,
        "crossing_minutes": cross,
        "frequency": freq,
        "terminal_overhead_minutes": term,
        "vehicle_ok": veh,
        "seasonal": seas,
        "notes": notes,
    }
    for (orig, dest, country, op, cross, freq, term, veh, seas, notes) in _RAW
]


# Country/region → list of relevant ISO/region codes for `routes_to` lookup
DEST_GROUPS: dict[str, list[str]] = {
    "FR": ["FR"], "France": ["FR"], "france": ["FR"],
    "ES": ["ES"], "Spain": ["ES"], "spain": ["ES"],
    "NL": ["NL"], "Netherlands": ["NL"], "netherlands": ["NL"], "holland": ["NL"],
    "BE": ["BE"], "Belgium": ["BE"], "belgium": ["BE"],
    "IE": ["IE"], "Ireland": ["IE"], "ireland": ["IE"], "Republic of Ireland": ["IE"],
    "GB-NIR": ["GB-NIR"], "Northern Ireland": ["GB-NIR"], "northern ireland": ["GB-NIR"], "NI": ["GB-NIR"],
    "IM": ["IM"], "Isle of Man": ["IM"], "isle of man": ["IM"], "iom": ["IM"],
    "channel": ["FR"], "Channel": ["FR"],
    "north sea": ["NL", "BE"], "North Sea": ["NL", "BE"],
    "irish sea": ["IE", "GB-NIR", "IM"], "Irish Sea": ["IE", "GB-NIR", "IM"],
    "british isles": ["IE", "GB-NIR", "IM"], "British Isles": ["IE", "GB-NIR", "IM"],
}


class FerryError(RuntimeError):
    pass


def routes_to(country_or_region: str) -> list[dict[str, Any]]:
    """List candidate ferry routes whose destination matches a country/region."""
    codes = DEST_GROUPS.get(country_or_region) or DEST_GROUPS.get(country_or_region.lower()) or [country_or_region.upper()]
    return [r for r in ROUTES if r["country_to"] in codes]


def find_route(origin_port: str, dest_port: str) -> list[dict[str, Any]]:
    """All routes (potentially multiple operators) between named ports.
    Case-insensitive substring match so `dover`/`calais` finds `Dover`/`Calais`.
    """
    o = origin_port.strip().lower()
    d = dest_port.strip().lower()
    return [
        r for r in ROUTES
        if o in r["origin_port"].lower() and d in r["dest_port"].lower()
    ]


async def check(
    browser,             # signature kept compatible w/ other static modules; unused
    origin_port: str,
    dest_port: str,
    date: str,
    vehicle: str = "car",
    passengers: int = 2,
) -> dict[str, Any]:
    matches = find_route(origin_port, dest_port)
    if not matches:
        raise FerryError(
            f"no curated ferry route between {origin_port!r} and {dest_port!r}; "
            "try travel_ferry_routes_to(<country>) to discover candidates"
        )
    try:
        date_type.fromisoformat(date)
    except ValueError as e:
        raise FerryError(f"invalid date {date!r}: {e}") from e

    options = []
    for r in matches:
        url = _booking_url(r["operator"], r["origin_port"], r["dest_port"], date, vehicle, passengers)
        options.append({
            **r,
            "terminal_to_terminal_minutes": r["crossing_minutes"] + r["terminal_overhead_minutes"],
            "booking_url": url,
        })

    # Sort by total time
    options.sort(key=lambda o: o["terminal_to_terminal_minutes"])

    return {
        "ok": True,
        "mode": "ferry",
        "from": matches[0]["origin_port"],
        "to": matches[0]["dest_port"],
        "country_to": matches[0]["country_to"],
        "date": date,
        "vehicle": vehicle,
        "passengers": passengers,
        "source": "static-timetable",
        "options": options,
        "note": (
            "Time-only data; ferry operators don't expose public price APIs. "
            "Click each option's booking_url for live availability and pricing."
        ),
        "as_of": datetime.utcnow().isoformat() + "Z",
    }
