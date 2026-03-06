# scary-search

A collection of read-only [MCP](https://modelcontextprotocol.io/) servers for querying personal infrastructure. Built with [FastMCP](https://github.com/jlowin/fastmcp) using stdio transport, designed to run as containerized tools for Claude Code (or any MCP client).

## Servers

| Module | Description |
|---|---|
| `postgres_mcp` | Query PostgreSQL databases (finance, location tracking) |
| `mariadb_mcp` | Query Home Assistant MariaDB (entity statistics, energy data) |
| `paperless_mcp` | Search and retrieve documents from Paperless-ngx |
| `meilisearch_mcp` | Hybrid keyword + semantic search over indexed documents |
| `tautulli_mcp` | Plex media watch history and statistics via Tautulli |
| `cronicle_mcp` | Job scheduler monitoring via Cronicle API |
| `healthchecks_mcp` | Uptime and cron monitoring via Healthchecks |

Plus a standalone **indexer** that syncs Paperless documents into Meilisearch with OpenAI embeddings.

## Setup

### Build

```bash
podman build -t mcp-search:latest .
```

### Run

Each server runs as a short-lived container with stdio transport. Pass the appropriate env file and network:

```bash
podman run --rm -i --network podman-backend \
  --env-file .env.postgres \
  mcp-search:latest mcp_search.postgres_mcp
```

### Claude Code integration

Add servers to `.mcp.json`:

```json
{
  "mcpServers": {
    "postgres-search": {
      "type": "stdio",
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "--network", "podman-backend",
        "--env-file", "/path/to/.env.postgres",
        "mcp-search:latest",
        "mcp_search.postgres_mcp"
      ]
    }
  }
}
```

### Environment variables

Copy `.env.example` and fill in credentials. Each server uses its own env file.

## Design

- All servers are **read-only** — DML queries are rejected
- Results are formatted as aligned text tables or CSV
- Row limits are enforced to keep responses manageable
- No dependencies between servers — each runs independently

## License

MIT — see [LICENSE](LICENSE).
