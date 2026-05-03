"""MCP server for the main (mees.st) Mailcow instance."""

from mcp_search.mailcow_base import create_mailcow_server

mcp = create_mailcow_server(
    "mailcow",
    "mailcow",
    url_env="MAILCOW_URL",
    key_env="MAILCOW_API_KEY",
)


if __name__ == "__main__":
    from mcp_search.run import serve
    serve(mcp)
