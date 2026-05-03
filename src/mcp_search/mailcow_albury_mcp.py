"""MCP server for the Albury Parish Council Mailcow instance."""

from mcp_search.mailcow_base import create_mailcow_server

mcp = create_mailcow_server(
    "mailcow-albury",
    "mailcow_albury",
    url_env="MAILCOW_ALBURY_URL",
    key_env="MAILCOW_ALBURY_API_KEY",
)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
