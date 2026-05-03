"""MCP server for read-only Healthchecks monitoring access."""

from mcp_search.healthchecks_base import create_healthchecks_server

mcp = create_healthchecks_server("healthchecks", "hc_mees")

if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
