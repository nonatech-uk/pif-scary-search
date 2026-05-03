"""MCP server for the Albury Hall Home Assistant instance."""

from mcp_search.homeassistant_base import create_homeassistant_server

mcp = create_homeassistant_server(
    "homeassistant-albury",
    "ha_albury",
    url_env="HA_ALBURY_URL",
    token_env="HA_ALBURY_TOKEN",
)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
