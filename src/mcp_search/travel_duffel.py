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


async def search_offers(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    date: str,
    adults: int = 2,
    cabin: str = "economy",
    max_offers: int = 5,
) -> dict[str, Any]:
    """Run a one-way offer request. Returns a summary dict, not raw Duffel JSON."""
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
    offers.sort(key=lambda o: float(o.get("total_amount", "9999")))

    results = []
    for o in offers[:max_offers]:
        results.append(
            {
                "id": o.get("id"),
                "total_amount": float(o["total_amount"]) if o.get("total_amount") else None,
                "total_currency": o.get("total_currency"),
                "owner": (o.get("owner") or {}).get("name"),
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
        "offers": results,
        "booking_deeplink": _skyscanner_deeplink(origin, destination, date, adults, cabin),
    }
