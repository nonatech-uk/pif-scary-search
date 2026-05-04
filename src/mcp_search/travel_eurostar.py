"""Eurostar — city-pair durations + booking deeplink.

Eurostar publishes a stable city-pair timetable: London ↔ Paris is always
~2h16, regardless of date or fare class. For trip planning we return the
travel time + check-in overhead at St Pancras, plus a booking URL that
takes the user to live availability.

Originally implemented as a Playwright scraper; replaced with static
durations 2026-05-03 after the Eurostar SPA proved hostile to scraping
and the brief was always about door-to-door comparison. Time data is
public; pricing comes from the operator at click-through time.
"""

from datetime import date as date_type, datetime
from typing import Any
from urllib.parse import urlencode

# UIC station codes — used for booking deeplinks (Eurostar accepts them
# in some flows; mostly here as a stable identifier for our data table).
STATIONS: dict[str, dict[str, Any]] = {
    "london":     {"code": "7015400", "name": "London St Pancras",         "country": "GB"},
    "ashford":    {"code": "7015430", "name": "Ashford International",     "country": "GB"},
    "ebbsfleet":  {"code": "7015415", "name": "Ebbsfleet International",   "country": "GB"},
    "paris":      {"code": "8727100", "name": "Paris Gare du Nord",        "country": "FR"},
    "lille":      {"code": "8722326", "name": "Lille Europe",              "country": "FR"},
    "brussels":   {"code": "8814001", "name": "Brussels Midi",             "country": "BE"},
    "amsterdam":  {"code": "8400058", "name": "Amsterdam Centraal",        "country": "NL"},
    "rotterdam":  {"code": "8400530", "name": "Rotterdam Centraal",        "country": "NL"},
    "disneyland": {"code": "8711184", "name": "Marne-la-Vallée Chessy",    "country": "FR"},
    "avignon":    {"code": "8775620", "name": "Avignon Centre",            "country": "FR"},  # seasonal direct
    "marseille":  {"code": "8775100", "name": "Marseille St Charles",      "country": "FR"},  # seasonal direct
    "bourg-saint-maurice": {"code": "8771000", "name": "Bourg-Saint-Maurice", "country": "FR"},  # winter ski
    "moutiers":   {"code": "8771100", "name": "Moutiers-Salins-Brides-les-Bains", "country": "FR"},  # winter ski
    "aime":       {"code": "8771200", "name": "Aime-La Plagne",            "country": "FR"},  # winter ski
}

# Direct journey durations in minutes. Keys MUST be alphabetically sorted
# tuples so the lookup (which sorts the input pair) hits regardless of
# direction — ("london","paris") and ("paris","london") both lookup
# ("london","paris").
_DIRECT_RAW: dict[tuple[str, str], dict[str, Any]] = {
    ("london","paris"):       {"minutes": 136, "frequency": "frequent (hourly+)", "seasonal": False},
    ("lille","london"):       {"minutes":  82, "frequency": "frequent",           "seasonal": False},
    ("brussels","london"):    {"minutes": 120, "frequency": "frequent",           "seasonal": False},
    ("amsterdam","london"):   {"minutes": 232, "frequency": "several daily",      "seasonal": False},
    ("london","rotterdam"):   {"minutes": 206, "frequency": "several daily",      "seasonal": False},
    ("disneyland","london"):  {"minutes": 167, "frequency": "1–2 daily",          "seasonal": False},
    ("avignon","london"):     {"minutes": 347, "frequency": "weekly",             "seasonal": "summer only (May–Sep)"},
    ("london","marseille"):   {"minutes": 387, "frequency": "weekly",             "seasonal": "summer only (May–Sep)"},
    ("bourg-saint-maurice","london"): {"minutes": 467, "frequency": "weekly",     "seasonal": "winter only (Dec–Apr)"},
    ("london","moutiers"):    {"minutes": 431, "frequency": "weekly",             "seasonal": "winter only (Dec–Apr)"},
    ("aime","london"):        {"minutes": 442, "frequency": "weekly",             "seasonal": "winter only (Dec–Apr)"},
    # Cross-Channel inland (rare but Eurostar do run some)
    ("ashford","paris"):      {"minutes": 116, "frequency": "limited",            "seasonal": "limited"},
    ("ebbsfleet","paris"):    {"minutes": 124, "frequency": "limited",            "seasonal": "limited"},
}
# Normalise keys to alphabetically-sorted tuples at module load (defensive).
DIRECT_MINUTES: dict[tuple[str, str], dict[str, Any]] = {
    tuple(sorted(k)): v for k, v in _DIRECT_RAW.items()
}

ST_PANCRAS_CHECKIN_MIN = 30   # min recommended check-in for security + UK-side immigration
DEFAULT_DRIVE_TO_ST_PANCRAS = 95   # Farley Green / GU5 0RW → St Pancras International by car (off-peak)


class EurostarError(RuntimeError):
    pass


def _resolve_station(query: str) -> dict[str, Any]:
    key = query.strip().lower()
    if key in STATIONS:
        return {"slug": key, **STATIONS[key]}
    for slug, v in STATIONS.items():
        if slug in key or v["name"].lower() in key:
            return {"slug": slug, **v}
    raise EurostarError(
        f"unknown Eurostar station {query!r}; known: {sorted(STATIONS)}"
    )


def _booking_url(o_code: str, d_code: str, date: str, adults: int) -> str:
    qs = urlencode(
        {
            "travelMode": "oneway",
            "trainOriginStation": o_code,
            "trainDestinationStation": d_code,
            "outbound": date,
            "adults": adults,
        }
    )
    return f"https://www.eurostar.com/uk-en/book?{qs}"


async def check(
    browser,            # kept for signature compatibility; unused
    origin_city: str,
    dest_city: str,
    date: str,
    adults: int = 2,
) -> dict[str, Any]:
    o = _resolve_station(origin_city)
    d = _resolve_station(dest_city)
    try:
        date_type.fromisoformat(date)
    except ValueError as e:
        raise EurostarError(f"invalid date {date!r}: {e}") from e

    pair = tuple(sorted([o["slug"], d["slug"]]))
    direct = DIRECT_MINUTES.get(pair)

    result: dict[str, Any] = {
        "ok": True,
        "mode": "eurostar",
        "from": o["name"],
        "from_code": o["code"],
        "to": d["name"],
        "to_code": d["code"],
        "date": date,
        "adults": adults,
        "source": "static-timetable",
        "checkin_minutes": ST_PANCRAS_CHECKIN_MIN if o["country"] == "GB" else 30,
        "default_drive_to_st_pancras_min": DEFAULT_DRIVE_TO_ST_PANCRAS,
        "booking_url": _booking_url(o["code"], d["code"], date, adults),
        "as_of": datetime.utcnow().isoformat() + "Z",
        "note": (
            "Time-only data; live availability and prices via booking_url. "
            "Seasonal routes only run in their stated window."
        ),
    }

    if direct:
        result["direct"] = True
        result["minutes"] = direct["minutes"]
        result["frequency"] = direct["frequency"]
        result["seasonal"] = direct["seasonal"]
    else:
        result["direct"] = False
        result["minutes"] = None
        result["note"] = (
            f"No direct Eurostar between {o['name']} and {d['name']}. "
            "Connect via Lille Europe or Paris Gare du Nord; consult "
            "sncf_journey for the onward TGV leg."
        )

    return result
