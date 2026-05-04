"""LeShuttle (Eurotunnel) — door-to-door durations + booking deeplink.

Eurotunnel is one route (Folkestone → Calais Coquelles, 35 min crossing).
For trip-planning purposes the operator's actual ticket flow is irrelevant —
what matters is the *time cost* of taking the tunnel vs flying or rail.
We return that time cost here; the booking_url goes to the live site.

Originally implemented as a Playwright scraper; replaced with static
durations 2026-05-03 after the LeShuttle SPA proved hostile to automation
(internal JS RangeError on Continue submit before any price API fires).
The brief was always about door-to-door comparison — durations are
sufficient for that. Pricing comes from the operator at click-through time.
"""

from datetime import date as date_type, datetime
from typing import Any
from urllib.parse import urlencode

VEHICLE_ALIASES: dict[str, str] = {
    "car": "car",
    "estate": "car",
    "suv": "car",
    "van": "high-vehicle",
    "high": "high-vehicle",
    "high-vehicle": "high-vehicle",
    "caravan": "caravan-trailer",
    "trailer": "caravan-trailer",
    "caravan-trailer": "caravan-trailer",
    "motorhome": "motorhome",
    "campervan": "motorhome",
    "motorcycle": "motorcycle",
    "bike": "motorcycle",
}

# Crossing time: 35 min on the train. Check-in at Folkestone is "arrive
# 30 min before departure"; vehicles roll on/off without unloading. So the
# *door-to-door* terminal experience is ~70 min (check-in + crossing +
# disembark + customs).
CROSSING_MINUTES = 35
TERMINAL_OVERHEAD_MINUTES = 35  # 30 check-in + 5 disembark/customs (typical)

# Drive times to/from terminals are user-overridable via plan_trip's
# access-leg constants; defaults from Farley Green here for the brief's
# canonical origin.
DEFAULT_DRIVE_TO_FOLKESTONE_MIN = 95   # Farley Green / GU5 0RW → Folkestone Terminal via M25/M20
DEFAULT_CALAIS_TERMINAL_MIN = 5        # roll-off to A16/A26 motorways


class EurotunnelError(RuntimeError):
    pass


def _resolve_vehicle(v: str) -> str:
    return VEHICLE_ALIASES.get(v.strip().lower(), "car")


def _booking_url(date: str, time: str, vehicle: str, passengers: int) -> str:
    qs = urlencode(
        {
            "journeyType": "oneway",
            "outboundDate": date,
            "outboundTime": time,
            "adults": passengers,
            "vehicle": vehicle,
        }
    )
    return f"https://www.leshuttle.com/booking/?{qs}"


async def check(
    browser,            # kept for signature compatibility; unused
    date: str,
    time: str = "10:00",
    vehicle: str = "car",
    passengers: int = 2,
) -> dict[str, Any]:
    veh = _resolve_vehicle(vehicle)
    try:
        date_type.fromisoformat(date)
    except ValueError as e:
        raise EurotunnelError(f"invalid date {date!r}: {e}") from e

    return {
        "ok": True,
        "mode": "eurotunnel",
        "from": "Folkestone",
        "to": "Calais Coquelles",
        "date": date,
        "time": time,
        "vehicle": veh,
        "passengers": passengers,
        "source": "static-timetable",
        "crossing_minutes": CROSSING_MINUTES,
        "terminal_overhead_minutes": TERMINAL_OVERHEAD_MINUTES,
        "terminal_to_terminal_minutes": CROSSING_MINUTES + TERMINAL_OVERHEAD_MINUTES,
        "default_drive_to_folkestone_min": DEFAULT_DRIVE_TO_FOLKESTONE_MIN,
        "default_calais_terminal_min": DEFAULT_CALAIS_TERMINAL_MIN,
        "note": (
            "Time-only data; LeShuttle does not expose live prices or "
            "live departure availability outside its booking flow. Use "
            "booking_url for the actual price and live timetable."
        ),
        "booking_url": _booking_url(date, time, veh, passengers),
        "as_of": datetime.utcnow().isoformat() + "Z",
    }
