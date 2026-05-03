"""Shared factory for Home Assistant MCP servers.

Allows multiple HA instances (e.g. main NAS, Albury Hall) to share one
implementation while exposing distinct tool prefixes.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan


def _format_table(rows: list[dict], keys: list[str]) -> str:
    if not rows:
        return "No results."
    str_rows = [[str(row.get(k, "")) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    return "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


def create_homeassistant_server(
    name: str,
    prefix: str,
    *,
    url_env: str,
    token_env: str,
) -> FastMCP:
    """Create a Home Assistant MCP server with the given name and tool prefix.

    Args:
        name: FastMCP server name (used by the gateway as the backend namespace).
        prefix: Tool name prefix (e.g. "ha", "ha_albury").
        url_env: Env var holding the HA base URL.
        token_env: Env var holding the long-lived access token.
    """
    ha_url = os.environ.get(url_env, "http://homeassistant:8123")
    ha_token = os.environ[token_env]

    @lifespan
    async def ha_lifespan(server):
        client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {ha_token}"},
            timeout=15.0,
        )
        yield {"client": client}
        await client.aclose()

    mcp = FastMCP(name, lifespan=ha_lifespan)

    def _client() -> httpx.AsyncClient:
        return get_context().lifespan_context["client"]

    @mcp.tool(name=f"{prefix}_entity_summary")
    async def entity_summary(
        domain: str | None = None,
        search: str | None = None,
    ) -> str:
        """List Home Assistant entities with their current state.

        Args:
            domain: Filter by entity domain (e.g. 'sensor', 'light', 'switch', 'climate', 'binary_sensor')
            search: Filter entity IDs or friendly names by substring (case-insensitive)
        """
        client = _client()
        resp = await client.get(f"{ha_url}/api/states")
        resp.raise_for_status()
        entities = resp.json()

        if domain:
            entities = [e for e in entities if e["entity_id"].startswith(f"{domain}.")]
        if search:
            s = search.lower()
            entities = [
                e for e in entities
                if s in e["entity_id"].lower()
                or s in e.get("attributes", {}).get("friendly_name", "").lower()
            ]

        if not entities:
            return "No entities found."

        entities.sort(key=lambda e: e["entity_id"])

        truncated = len(entities) > 100
        entities = entities[:100]

        rows = []
        for e in entities:
            attrs = e.get("attributes", {})
            unit = attrs.get("unit_of_measurement", "")
            rows.append({
                "entity_id": e["entity_id"],
                "state": e["state"],
                "unit": unit,
                "name": attrs.get("friendly_name", ""),
            })

        result = _format_table(rows, ["entity_id", "state", "unit", "name"])
        if truncated:
            result = f"Showing first 100 of {len(entities)} entities. Use domain/search to narrow.\n\n" + result
        return result

    @mcp.tool(name=f"{prefix}_get_state")
    async def get_state(entity_id: str) -> str:
        """Get the full current state and attributes of a specific entity.

        Args:
            entity_id: The entity ID (e.g. 'sensor.living_room_temperature', 'light.kitchen')
        """
        client = _client()
        resp = await client.get(f"{ha_url}/api/states/{entity_id}")
        if resp.status_code == 404:
            return f"Entity '{entity_id}' not found."
        resp.raise_for_status()
        e = resp.json()

        attrs = e.get("attributes", {})
        lines = [
            f"# {attrs.get('friendly_name', entity_id)}",
            f"",
            f"**Entity ID:** {e['entity_id']}",
            f"**State:** {e['state']}{' ' + attrs.get('unit_of_measurement', '') if attrs.get('unit_of_measurement') else ''}",
            f"**Last changed:** {e.get('last_changed', '—')}",
            f"**Last updated:** {e.get('last_updated', '—')}",
        ]

        if attrs:
            lines.append(f"\n**Attributes:**")
            for k, v in sorted(attrs.items()):
                if k in ("friendly_name", "unit_of_measurement"):
                    continue
                lines.append(f"  {k}: {v}")

        return "\n".join(lines)

    @mcp.tool(name=f"{prefix}_history")
    async def history(
        entity_id: str,
        hours: int = 24,
    ) -> str:
        """Get state history for an entity over a recent time period.

        Args:
            entity_id: The entity ID
            hours: Number of hours to look back (default 24, max 168)
        """
        hours = min(hours, 168)
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        client = _client()
        resp = await client.get(
            f"{ha_url}/api/history/period/{start}",
            params={
                "filter_entity_id": entity_id,
                "minimal_response": "true",
                "significant_changes_only": "true",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if not data or not data[0]:
            return f"No history for '{entity_id}' in the last {hours}h."

        states = data[0]
        rows = []
        for s in states[-100:]:
            ts = s.get("last_changed", "")[:19].replace("T", " ")
            rows.append({"time": ts, "state": s.get("state", "?")})

        total = len(states)
        header = f"History for {entity_id} (last {hours}h, {total} changes):\n\n"
        if total > 100:
            header += f"(showing last 100 of {total})\n\n"
        return header + _format_table(rows, ["time", "state"])

    @mcp.tool(name=f"{prefix}_call_service")
    async def call_service(
        domain: str,
        service: str,
        entity_id: str | None = None,
    ) -> str:
        """Call a Home Assistant service (e.g. turn on/off lights, switches).

        Args:
            domain: Service domain (e.g. 'light', 'switch', 'scene', 'automation')
            service: Service name (e.g. 'turn_on', 'turn_off', 'toggle', 'activate')
            entity_id: Target entity ID (required for most services)
        """
        client = _client()
        payload = {}
        if entity_id:
            payload["entity_id"] = entity_id

        resp = await client.post(
            f"{ha_url}/api/services/{domain}/{service}",
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json()

        if not result:
            return f"Service {domain}.{service} called successfully (no state changes)."

        changed = [e.get("entity_id", "?") for e in result]
        return f"Service {domain}.{service} called. Entities affected: {', '.join(changed)}"

    @mcp.tool(name=f"{prefix}_statistics")
    async def statistics(
        entity_id: str,
        days: int = 30,
    ) -> str:
        """Get long-term statistics for a sensor entity (energy, temperature, etc).

        Uses the HA statistics API for hourly/5-minute aggregated data.

        Args:
            entity_id: The entity statistic ID (e.g. 'sensor.energy_consumption')
            days: Number of days to look back (default 30, max 365)
        """
        days = min(days, 365)
        start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        end = datetime.now(timezone.utc).isoformat()

        client = _client()
        resp = await client.get(
            f"{ha_url}/api/history/period/{start}",
            params={
                "filter_entity_id": entity_id,
                "end_time": end,
                "minimal_response": "true",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if not data or not data[0]:
            return f"No statistics for '{entity_id}' in the last {days} days."

        states = data[0]

        daily: dict[str, list[float]] = {}
        for s in states:
            try:
                val = float(s["state"])
            except (ValueError, KeyError):
                continue
            day = s.get("last_changed", "")[:10]
            if day:
                daily.setdefault(day, []).append(val)

        if not daily:
            return f"No numeric data for '{entity_id}' in the last {days} days."

        rows = []
        for day in sorted(daily.keys()):
            vals = daily[day]
            rows.append({
                "date": day,
                "min": f"{min(vals):.2f}",
                "avg": f"{sum(vals) / len(vals):.2f}",
                "max": f"{max(vals):.2f}",
                "samples": str(len(vals)),
            })

        if len(rows) > 100:
            rows = rows[-100:]

        header = f"Daily statistics for {entity_id} (last {days} days):\n\n"
        return header + _format_table(rows, ["date", "min", "avg", "max", "samples"])

    return mcp
