"""Duffel API client for flight offers.

Phase 1 scope: offer search via POST /air/offer_requests?return_offers=true.
Bookings are explicitly out of scope — this server deep-links instead.

Test mode: use a `duffel_test_*` API token. Live mode: switch DUFFEL_MODE=live
once weights stabilise.
"""

import os
from typing import Any

import httpx

DUFFEL_BASE = "https://api.duffel.com"
DUFFEL_VERSION = "v2"


class DuffelError(RuntimeError):
    pass


def _headers() -> dict:
    token = os.environ.get("DUFFEL_API_TOKEN")
    if not token:
        raise DuffelError("DUFFEL_API_TOKEN is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Duffel-Version": DUFFEL_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _skyscanner_deeplink(orig: str, dest: str, date: str, adults: int, cabin: str) -> str:
    yymmdd = date.replace("-", "")[2:]
    return (
        f"https://www.skyscanner.net/transport/flights/{orig.lower()}/{dest.lower()}/"
        f"{yymmdd}/?adults={adults}&cabinclass={cabin}"
    )


def _summarise_slice(slc: dict) -> dict:
    segments = slc.get("segments", [])
    return {
        "origin": slc.get("origin", {}).get("iata_code"),
        "destination": slc.get("destination", {}).get("iata_code"),
        "duration": slc.get("duration"),
        "depart": segments[0]["departing_at"] if segments else None,
        "arrive": segments[-1]["arriving_at"] if segments else None,
        "stops": max(len(segments) - 1, 0),
        "carriers": sorted(
            {
                seg.get("marketing_carrier", {}).get("name")
                for seg in segments
                if seg.get("marketing_carrier")
            }
        ),
    }


def _carrier_matches(offer: dict, patterns: list[str]) -> bool:
    """True if the offer's owner matches any pattern (IATA code OR name substring),
    case-insensitive. Also checks per-segment marketing carriers so e.g.
    'BA' matches a BA-codeshare flight even if owner is a partner."""
    if not patterns:
        return False
    owner = offer.get("owner") or {}
    name = (owner.get("name") or "").lower()
    iata = (owner.get("iata_code") or "").lower()
    seg_carriers: list[tuple[str, str]] = []
    for slc in offer.get("slices") or []:
        for seg in slc.get("segments") or []:
            mc = seg.get("marketing_carrier") or {}
            seg_carriers.append((
                (mc.get("iata_code") or "").lower(),
                (mc.get("name") or "").lower(),
            ))
    for p in patterns:
        pl = p.strip().lower()
        if not pl:
            continue
        if pl == iata or (name and pl in name):
            return True
        for (sc_iata, sc_name) in seg_carriers:
            if pl == sc_iata or (sc_name and pl in sc_name):
                return True
    return False


async def search_offers(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    date: str,
    adults: int = 2,
    cabin: str = "economy",
    max_offers: int = 5,
    prefer_carriers: list[str] | None = None,
    exclude_carriers: list[str] | None = None,
) -> dict[str, Any]:
    """Run a one-way offer request. Returns a summary dict, not raw Duffel JSON.

    Carrier filters (case-insensitive; match IATA code or name substring,
    checks both `owner` and per-segment `marketing_carrier`):
      - `exclude_carriers`: hard-drop matching offers (e.g. ["Ryanair","Wizz"])
      - `prefer_carriers`: soft preference — matching offers move to the
        top of the result, non-matching kept as fallback below
    """
    body = {
        "data": {
            "slices": [
                {"origin": origin, "destination": destination, "departure_date": date}
            ],
            "passengers": [{"type": "adult"}] * adults,
            "cabin_class": cabin,
        }
    }
    resp = await client.post(
        f"{DUFFEL_BASE}/air/offer_requests",
        headers=_headers(),
        params={"return_offers": "true"},
        json=body,
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise DuffelError(f"duffel {resp.status_code}: {resp.text[:500]}")
    payload = resp.json().get("data", {})
    offers = payload.get("offers") or []

    # Hard filter: drop excluded carriers
    if exclude_carriers:
        offers = [o for o in offers if not _carrier_matches(o, exclude_carriers)]

    # Sort by price first
    offers.sort(key=lambda o: float(o.get("total_amount", "9999")))

    # Soft preference: re-order so preferred carriers appear first (still
    # price-sorted within each group)
    if prefer_carriers:
        preferred = [o for o in offers if _carrier_matches(o, prefer_carriers)]
        others    = [o for o in offers if not _carrier_matches(o, prefer_carriers)]
        offers = preferred + others

    results = []
    for o in offers[:max_offers]:
        owner = o.get("owner") or {}
        results.append(
            {
                "id": o.get("id"),
                "total_amount": float(o["total_amount"]) if o.get("total_amount") else None,
                "total_currency": o.get("total_currency"),
                "owner": owner.get("name"),
                "owner_iata": owner.get("iata_code"),
                "preferred": _carrier_matches(o, prefer_carriers) if prefer_carriers else None,
                "cabin_class": cabin,
                "expires_at": o.get("expires_at"),
                "slices": [_summarise_slice(s) for s in o.get("slices", [])],
            }
        )

    return {
        "ok": True,
        "mode": "flight",
        "origin": origin,
        "destination": destination,
        "date": date,
        "adults": adults,
        "cabin": cabin,
        "live": os.environ.get("DUFFEL_MODE", "test") == "live",
        "prefer_carriers": prefer_carriers,
        "exclude_carriers": exclude_carriers,
        "offers": results,
        "booking_deeplink": _skyscanner_deeplink(origin, destination, date, adults, cabin),
    }
