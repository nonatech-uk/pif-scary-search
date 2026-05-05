# mcp-search

Multi-server FastMCP project. One container image (`localhost/mcp-search:latest`) bundles many
servers; per-server quadlets pick which module runs via `Exec=mcp_search.<module>`.

## Servers (20, all FastMCP, streamable-http behind the gateway)

Read-only / search:
- `postgres_mcp` · `paperless_mcp` · `meilisearch_mcp` · `loki_mcp` · `mariadb_mcp`
- `calibre_mcp` · `jelu_mcp` · `joplin_mcp` · `imap_mcp` · `tautulli_mcp` · `plex_mcp` · `immich_mcp`

Read + write / control-plane:
- `cronicle_mcp` · `healthchecks_mcp` · `parish_healthchecks_mcp`
- `homeassistant_mcp` · `homeassistant_albury_mcp`
- `mailcow_mcp` · `mailcow_albury_mcp` · `spotify_mcp`

Domain integrations:
- (none — `travel_mcp`, `sbb_mcp`, `uk_trains_mcp` were extracted to
  [github.com/nonatech-uk/mcp-travel](https://github.com/nonatech-uk/mcp-travel) on 2026-05-05)

## Build

```bash
podman build -t mcp-search:latest .
```

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
