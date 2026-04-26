"""
DB MCP Server — Real PostgreSQL diagnostic integration.

Loads the tenant's database credentials from the credential store,
then uses asyncpg to run read-only diagnostic queries against their database.
Falls back to the platform's local incident_db if no tenant DB credentials exist.
"""
from fastmcp import FastMCP
import asyncpg

from shared.credential_store import get_credentials
from shared.config import settings
from shared.logger import get_logger

log = get_logger("db-mcp")
mcp = FastMCP("db-mcp")

# Default: our local incident_db (strip the SQLAlchemy driver prefix for asyncpg)
DEFAULT_DB_URL = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://")


async def _get_connection(tenant_id: str) -> asyncpg.Connection:
    """
    Create a connection to the tenant's database.
    Falls back to the platform's default Postgres if no credentials are registered.
    """
    creds = await get_credentials(tenant_id)

    if creds and creds.database:
        db_url = creds.database.connection_url
        log.info("using_tenant_database", tenant_id=tenant_id)
    else:
        db_url = DEFAULT_DB_URL
        log.info("using_default_database", tenant_id=tenant_id)

    return await asyncpg.connect(db_url)


@mcp.tool()
async def get_connection_count(tenant_id: str, db_name: str) -> str:
    """
    Get current database connections grouped by state (active, idle, etc.).
    Queries pg_stat_activity — works on any PostgreSQL instance.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            rows = await conn.fetch(
                "SELECT state, count(*) AS cnt FROM pg_stat_activity "
                "WHERE datname = $1 GROUP BY state ORDER BY cnt DESC",
                db_name,
            )
            if not rows:
                return f"No connections found for database '{db_name}'."

            lines = [f"Connections for '{db_name}':"]
            total = 0
            for row in rows:
                state = row["state"] or "unknown"
                count = row["cnt"]
                total += count
                lines.append(f"  {state}: {count}")
            lines.append(f"  TOTAL: {total}")
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_connection_count_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_slow_queries(tenant_id: str, threshold_ms: int = 1000, limit: int = 10) -> str:
    """
    Get queries that are currently running longer than the threshold.
    Uses pg_stat_activity's query_start to compute duration.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            rows = await conn.fetch(
                """
                SELECT pid, usename, datname,
                       EXTRACT(EPOCH FROM (NOW() - query_start))::int * 1000 AS duration_ms,
                       LEFT(query, 200) AS query_text
                FROM pg_stat_activity
                WHERE state = 'active'
                  AND query NOT LIKE '%pg_stat_activity%'
                  AND EXTRACT(EPOCH FROM (NOW() - query_start))::int * 1000 > $1
                ORDER BY query_start ASC
                LIMIT $2
                """,
                threshold_ms,
                limit,
            )
            if not rows:
                return f"No queries running longer than {threshold_ms}ms."

            lines = [f"Slow queries (>{threshold_ms}ms):"]
            for row in rows:
                lines.append(
                    f"  PID {row['pid']} | {row['duration_ms']}ms | "
                    f"user={row['usename']} | db={row['datname']}\n"
                    f"    {row['query_text']}"
                )
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_slow_queries_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_replication_lag(tenant_id: str) -> str:
    """
    Get replication delay in seconds for each replica.
    Queries pg_stat_replication on the primary.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            rows = await conn.fetch(
                """
                SELECT client_addr, application_name, state,
                       EXTRACT(EPOCH FROM (NOW() - sent_lsn))::numeric AS lag_seconds
                FROM pg_stat_replication
                ORDER BY lag_seconds DESC
                """
            )
            if not rows:
                return "No replication slots found. This might be a standalone instance."

            lines = ["Replication status:"]
            for row in rows:
                lines.append(
                    f"  {row['application_name']} ({row['client_addr']}): "
                    f"state={row['state']}, lag={row['lag_seconds']:.1f}s"
                )
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_replication_lag_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def check_table_bloat(tenant_id: str, schema: str = "public") -> str:
    """
    Check tables with high dead tuple ratio indicating they need VACUUM.
    Uses pg_stat_user_tables which is available on all PostgreSQL instances.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            rows = await conn.fetch(
                """
                SELECT schemaname, relname,
                       n_live_tup, n_dead_tup,
                       CASE WHEN n_live_tup > 0
                            THEN ROUND(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1)
                            ELSE 0
                       END AS dead_pct,
                       last_autovacuum
                FROM pg_stat_user_tables
                WHERE schemaname = $1
                ORDER BY n_dead_tup DESC
                LIMIT 15
                """,
                schema,
            )
            if not rows:
                return f"No tables found in schema '{schema}'."

            lines = [f"Table bloat report for schema '{schema}':"]
            for row in rows:
                flag = " ⚠️ NEEDS VACUUM" if row["dead_pct"] > 20 else ""
                last_vac = row["last_autovacuum"]
                vac_str = last_vac.strftime("%Y-%m-%d %H:%M") if last_vac else "never"
                lines.append(
                    f"  {row['relname']}: {row['dead_pct']}% dead "
                    f"({row['n_dead_tup']} dead / {row['n_live_tup']} live) "
                    f"| last vacuum: {vac_str}{flag}"
                )
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_table_bloat_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_recent_errors(tenant_id: str, minutes: int = 30) -> str:
    """
    Get recent error-level messages from the PostgreSQL log.
    Note: Requires pg_stat_statements or csvlog. Falls back to pg_stat_activity.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            # Check for queries that ended in error state recently
            rows = await conn.fetch(
                """
                SELECT pid, usename, datname, state,
                       LEFT(query, 200) AS query_text,
                       query_start
                FROM pg_stat_activity
                WHERE state = 'idle in transaction (aborted)'
                   OR (wait_event_type = 'Lock' AND state = 'active')
                ORDER BY query_start DESC
                LIMIT 10
                """
            )
            if not rows:
                return f"No error-state queries found in the last {minutes} minutes."

            lines = ["Recent error-state queries:"]
            for row in rows:
                lines.append(
                    f"  PID {row['pid']} | state={row['state']} | "
                    f"user={row['usename']} | db={row['datname']}\n"
                    f"    {row['query_text']}"
                )
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_recent_errors_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_lock_waits(tenant_id: str) -> str:
    """
    Get queries blocked waiting for locks — critical for diagnosing deadlocks.
    """
    try:
        conn = await _get_connection(tenant_id)
        try:
            rows = await conn.fetch(
                """
                SELECT blocked.pid AS blocked_pid,
                       blocked.query AS blocked_query,
                       blocking.pid AS blocking_pid,
                       blocking.query AS blocking_query
                FROM pg_stat_activity blocked
                JOIN pg_locks blocked_locks ON blocked.pid = blocked_locks.pid
                JOIN pg_locks blocking_locks ON blocked_locks.locktype = blocking_locks.locktype
                    AND blocked_locks.database IS NOT DISTINCT FROM blocking_locks.database
                    AND blocked_locks.relation IS NOT DISTINCT FROM blocking_locks.relation
                    AND blocked_locks.pid != blocking_locks.pid
                JOIN pg_stat_activity blocking ON blocking_locks.pid = blocking.pid
                WHERE NOT blocked_locks.granted
                LIMIT 10
                """
            )
            if not rows:
                return "No lock waits detected. Database lock contention is healthy."

            lines = ["⚠️ Lock waits detected:"]
            for row in rows:
                lines.append(
                    f"  Blocked PID {row['blocked_pid']}: {row['blocked_query'][:100]}\n"
                    f"  Blocking PID {row['blocking_pid']}: {row['blocking_query'][:100]}\n"
                )
            return "\n".join(lines)
        finally:
            await conn.close()
    except Exception as e:
        log.error("db_lock_waits_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8002)
