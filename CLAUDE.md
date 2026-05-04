# mcp-search

Multi-server FastMCP project. One container image (`localhost/mcp-search:latest`) bundles many
servers; per-server quadlets pick which module runs via `Exec=mcp_search.<module>`.

## Servers (23, all FastMCP, streamable-http behind the gateway)

Read-only / search:
- `postgres_mcp` · `paperless_mcp` · `meilisearch_mcp` · `loki_mcp` · `mariadb_mcp`
- `calibre_mcp` · `jelu_mcp` · `joplin_mcp` · `imap_mcp` · `tautulli_mcp` · `plex_mcp` · `immich_mcp`

Read + write / control-plane:
- `cronicle_mcp` · `healthchecks_mcp` · `parish_healthchecks_mcp`
- `homeassistant_mcp` · `homeassistant_albury_mcp`
- `mailcow_mcp` · `mailcow_albury_mcp` · `spotify_mcp`

Domain integrations:
- `sbb_mcp` (Swiss rail) · `uk_trains_mcp` (RTT/TAPI) · `travel_mcp` (door-to-door trip planner)

The **travel** suite spans `travel_mcp.py` (FastMCP entrypoint) plus `travel_*.py` helpers:
`travel_cache`, `travel_geocode`, `travel_drive` (Google Maps Routes), `travel_duffel`
(flights), `travel_sncf` (Navitia), `travel_eurostar` / `travel_eurotunnel` (static
durations), `travel_liteapi` + `travel_hotels` (LiteAPI hotel search), `travel_rank`
(region classifier + scoring), `travel_plan` (`plan_trip` orchestrator).

## Build

```bash
podman build -t mcp-search:latest .
```

The travel suite uses a second-stage image:

```bash
podman build -t mcp-search-travel:latest -f Containerfile.travel .
```

(currently a thin layer over the base; held open in case Playwright comes back)

## Quadlets

Canonical at `/zfs/Apps/quadlets/mcp-<service>.container`, materialized into
`/etc/containers/systemd/` via `/usr/local/bin/sync-quadlets.sh`. Each server's env
file at `/zfs/Apps/AppData/mcp-search/.env.<service>` (0600 root:root).

## Gateway

Both `mcp-local` (port 8091, LAN no-auth) and `mcp-gateway` (Cloudflare Tunnel →
`query.mees.st`, Keycloak OIDC) multiplex these servers. To register a new server:
add to `MCP_BACKENDS` in `/zfs/Apps/AppData/mcp-gateway/.env.local` and
`.env.gateway`, plus `After=mcp-<service>.service` in both gateway quadlets.

## Postgres readonly

`mcp_readonly` user has SELECT across many app DBs (finance, mylocation, scrobble,
pipeline, wine, homeassistant, journal, joplin, paperless, linkwarden, splitwise,
obligations, usage, stuff, pif, travel). Grants live in
`infra/create-readonly-user.sql`.

## Git

Identity: `Stu Bevan <stu.bevan@nonatech.co.uk>`
