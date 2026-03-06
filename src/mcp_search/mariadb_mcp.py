"""MCP server for read-only MariaDB access to Home Assistant database."""

import os
import re

import aiomysql
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.server.lifespan import lifespan

DATABASES = ("homeassistant",)
DML_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|LOAD)\b",
    re.IGNORECASE,
)
MAX_ROWS = 500


async def _create_pool(database: str) -> aiomysql.Pool:
    return await aiomysql.create_pool(
        host=os.environ.get("MARIADB_HOST", "mariadb"),
        port=int(os.environ.get("MARIADB_PORT", "3306")),
        user=os.environ.get("MARIADB_USER", "mcp_readonly"),
        password=os.environ["MARIADB_PASSWORD"],
        db=database,
        minsize=1,
        maxsize=5,
        autocommit=True,
    )


@lifespan
async def maria_lifespan(server):
    pools = {}
    for db in DATABASES:
        pools[db] = await _create_pool(db)
    yield {"pools": pools}
    for pool in pools.values():
        pool.close()
        await pool.wait_closed()


mcp = FastMCP("mariadb-search", lifespan=maria_lifespan)


def _get_pool(database: str) -> aiomysql.Pool:
    ctx = get_context()
    pools = ctx.lifespan_context["pools"]
    if database not in pools:
        raise ValueError(f"Unknown database: {database}. Must be one of: {', '.join(DATABASES)}")
    return pools[database]


@mcp.tool
async def maria_discover_schema(
    database: str,
    table_filter: str | None = None,
) -> str:
    """List tables and columns in a MariaDB database.

    Args:
        database: Database name — currently only 'homeassistant'
        table_filter: Optional substring to filter table names
    """
    pool = _get_pool(database)
    query = """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
    """
    params = [database]
    if table_filter:
        query += " AND table_name LIKE %s"
        params.append(f"%{table_filter}%")
    query += " ORDER BY table_name, ordinal_position"

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()

    if not rows:
        return "No tables found."

    current_table = None
    lines = []
    for row in rows:
        if row["table_name"] != current_table:
            current_table = row["table_name"]
            lines.append(f"\n## {current_table}")
        nullable = " (nullable)" if row["is_nullable"] == "YES" else ""
        lines.append(f"  - {row['column_name']}: {row['data_type']}{nullable}")

    return "\n".join(lines)


@mcp.tool
async def maria_query(
    database: str,
    query: str,
    params: list[str] | None = None,
    response_format: str = "text",
) -> str:
    """Execute a read-only SQL query against MariaDB.

    Args:
        database: Database name — currently only 'homeassistant'
        query: SQL SELECT query using %s for parameters
        params: Optional list of parameter values
        response_format: 'text' for aligned columns, 'csv' for CSV format
    """
    if DML_PATTERN.search(query):
        return "Error: Only SELECT queries are allowed."

    stripped = query.strip().rstrip(";")
    if not re.match(r"(?i)^(SELECT|WITH)\b", stripped):
        return "Error: Query must start with SELECT or WITH."

    if not re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        stripped += f" LIMIT {MAX_ROWS}"

    pool = _get_pool(database)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(stripped, tuple(params or []))
            rows = await cur.fetchall()

    if not rows:
        return "No results."

    keys = list(rows[0].keys())
    if response_format == "csv":
        lines = [",".join(keys)]
        for row in rows:
            lines.append(",".join(str(row[k]) for k in keys))
        return "\n".join(lines)

    str_rows = [[str(row[k]) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    return "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


@mcp.tool
async def ha_entity_summary(
    entity_filter: str | None = None,
) -> str:
    """List Home Assistant entities that have statistics, with their metadata.

    Args:
        entity_filter: Optional substring to filter entity IDs (e.g. 'energy', 'solar', 'temperature')
    """
    pool = _get_pool("homeassistant")
    query = """
        SELECT sm.statistic_id, sm.unit_of_measurement, sm.source,
               COUNT(*) AS data_points,
               MIN(s.start_ts) AS earliest_ts,
               MAX(s.start_ts) AS latest_ts
        FROM statistics_meta sm
        JOIN statistics s ON s.metadata_id = sm.id
        WHERE 1=1
    """
    params = []
    if entity_filter:
        query += " AND sm.statistic_id LIKE %s"
        params.append(f"%{entity_filter}%")
    query += " GROUP BY sm.id ORDER BY data_points DESC LIMIT 50"

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()

    if not rows:
        return "No entities found."

    from datetime import datetime, timezone

    lines = []
    for row in rows:
        earliest = datetime.fromtimestamp(row["earliest_ts"], tz=timezone.utc).strftime("%Y-%m-%d") if row["earliest_ts"] else "?"
        latest = datetime.fromtimestamp(row["latest_ts"], tz=timezone.utc).strftime("%Y-%m-%d") if row["latest_ts"] else "?"
        unit = row["unit_of_measurement"] or ""
        lines.append(
            f"  {row['statistic_id']} ({unit}) — {row['data_points']:,} points, {earliest} to {latest}"
        )

    return f"Found {len(rows)} entities:\n\n" + "\n".join(lines)


@mcp.tool
async def ha_statistics(
    entity_id: str,
    days: int = 30,
    resolution: str = "daily",
) -> str:
    """Get Home Assistant statistics for an entity over a time period.

    Args:
        entity_id: The entity statistic_id (e.g. 'sensor.energy_consumption')
        days: Number of days to look back (default 30)
        resolution: 'hourly' or 'daily' aggregation
    """
    pool = _get_pool("homeassistant")

    # Get metadata_id
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, unit_of_measurement FROM statistics_meta WHERE statistic_id = %s",
                (entity_id,),
            )
            meta = await cur.fetchone()

    if not meta:
        return f"Entity '{entity_id}' not found in statistics_meta."

    import time

    cutoff_ts = time.time() - (days * 86400)

    if resolution == "hourly":
        query = """
            SELECT FROM_UNIXTIME(start_ts) AS ts, mean, min, max, state, sum
            FROM statistics
            WHERE metadata_id = %s AND start_ts >= %s
            ORDER BY start_ts
            LIMIT 500
        """
    else:
        query = """
            SELECT DATE(FROM_UNIXTIME(start_ts)) AS day,
                   ROUND(AVG(mean), 3) AS avg_val,
                   ROUND(MIN(min), 3) AS min_val,
                   ROUND(MAX(max), 3) AS max_val,
                   ROUND(MAX(state), 3) AS last_state,
                   ROUND(MAX(sum), 3) AS cumulative_sum,
                   COUNT(*) AS samples
            FROM statistics
            WHERE metadata_id = %s AND start_ts >= %s
            GROUP BY day
            ORDER BY day
            LIMIT 500
        """

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, (meta["id"], cutoff_ts))
            rows = await cur.fetchall()

    if not rows:
        return f"No statistics found for '{entity_id}' in the last {days} days."

    unit = meta["unit_of_measurement"] or ""
    keys = list(rows[0].keys())
    str_rows = [[str(row[k]) for k in keys] for row in rows]
    widths = [max(len(k), *(len(r[i]) for r in str_rows)) for i, k in enumerate(keys)]
    header = " | ".join(k.ljust(w) for k, w in zip(keys, widths))
    separator = "-+-".join("-" * w for w in widths)
    data_lines = [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in str_rows]
    title = f"Statistics for {entity_id} ({unit}) — last {days} days, {resolution}:\n\n"
    return title + "\n".join([header, separator, *data_lines, f"\n({len(rows)} rows)"])


if __name__ == "__main__":
    mcp.run()
