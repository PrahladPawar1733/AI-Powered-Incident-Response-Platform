"""
Logs MCP Server — Real Grafana Loki HTTP API integration.

Loads the tenant's Loki credentials from the credential store,
then uses httpx to execute real LogQL queries against their Loki instance.
Falls back to the platform's local Loki if no tenant credentials exist.
"""
from fastmcp import FastMCP
import httpx
import json
import time

from shared.credential_store import get_credentials
from shared.logger import get_logger

log = get_logger("logs-mcp")
mcp = FastMCP("logs-mcp")

# Default fallback: our local Loki from docker-compose
DEFAULT_LOKI_URL = "http://localhost:3100"


async def _get_loki_client(tenant_id: str) -> tuple[str, dict]:
    """
    Returns (base_url, headers) for the tenant's Loki instance.
    Falls back to local Loki if no credentials are registered.
    """
    creds = await get_credentials(tenant_id)
    headers = {}

    if creds and creds.loki:
        base_url = creds.loki.base_url.rstrip("/")
        if creds.loki.auth_type == "bearer" and creds.loki.bearer_token:
            headers["Authorization"] = f"Bearer {creds.loki.bearer_token}"
        elif creds.loki.auth_type == "basic" and creds.loki.username:
            import base64
            token = base64.b64encode(
                f"{creds.loki.username}:{creds.loki.password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {token}"
        elif creds.loki.auth_type == "x-scope-orgid" and creds.loki.org_id:
            # Loki multi-tenant header
            headers["X-Scope-OrgID"] = creds.loki.org_id
        log.info("using_tenant_loki", tenant_id=tenant_id, url=base_url)
    else:
        base_url = DEFAULT_LOKI_URL
        log.info("using_default_loki", tenant_id=tenant_id, url=base_url)

    return base_url, headers


def _ns_to_rfc3339(ns: str) -> str:
    """Convert Loki nanosecond timestamp to human-readable format."""
    try:
        ts = int(ns) / 1e9
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (ValueError, OSError):
        return ns


@mcp.tool()
async def search_logs(
    tenant_id: str, service: str, query: str,
    minutes: int = 30, limit: int = 100
) -> str:
    """
    Search log lines matching a query string for a specific service.
    Uses Loki's /loki/api/v1/query_range endpoint with LogQL.
    """
    base_url, headers = await _get_loki_client(tenant_id)
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (minutes * 60 * int(1e9))

    # LogQL: filter logs by app label and search for the query term
    logql = f'{{app="{service}"}} |= `{query}`'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": limit,
                },
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            streams = data.get("data", {}).get("result", [])
            if not streams:
                return f"No log lines found matching '{query}' for service '{service}' in the last {minutes} minutes."

            lines = []
            for stream in streams:
                labels = stream.get("stream", {})
                for ts, line in stream.get("values", []):
                    readable_ts = _ns_to_rfc3339(ts)
                    lines.append(f"[{readable_ts}] {line}")

            return "\n".join(lines[-limit:])
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to Loki at {base_url}. Is it running? Register your Loki endpoint via PUT /credentials/loki"
    except Exception as e:
        log.error("loki_search_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR searching logs: {str(e)}"


@mcp.tool()
async def get_error_rate(tenant_id: str, service: str, minutes: int = 30) -> str:
    """
    Get the error log lines per minute for a service.
    Uses Loki metric queries to compute rates from log streams.
    """
    base_url, headers = await _get_loki_client(tenant_id)

    # LogQL metric query: count error lines per minute
    logql = f'sum(count_over_time({{app="{service}"}} |= `ERROR` [{minutes}m]))'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query",
                params={"query": logql},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"No error logs found for service '{service}' in the last {minutes} minutes."

            count = results[0].get("value", [None, "0"])[1]
            rate_per_min = int(count) / minutes
            return f"Service '{service}': {count} errors in {minutes}m ({rate_per_min:.1f}/min)"
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to Loki at {base_url}"
    except Exception as e:
        log.error("loki_error_rate_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_stack_traces(tenant_id: str, service: str, minutes: int = 30) -> str:
    """
    Get exception stack traces from logs, filtered for the given service.
    Searches for common patterns: 'Traceback', 'Exception', 'Error'.
    """
    base_url, headers = await _get_loki_client(tenant_id)
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (minutes * 60 * int(1e9))

    logql = f'{{app="{service}"}} |~ `(?i)(traceback|exception|error|panic|fatal)`'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": 50,
                },
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            streams = data.get("data", {}).get("result", [])
            if not streams:
                return f"No stack traces found for service '{service}' in the last {minutes} minutes."

            lines = []
            for stream in streams:
                for ts, line in stream.get("values", []):
                    lines.append(f"[{_ns_to_rfc3339(ts)}] {line}")

            return "\n".join(lines[:50])
    except Exception as e:
        log.error("loki_stack_traces_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def correlate_by_trace_id(tenant_id: str, trace_id: str) -> str:
    """
    Get all log lines across ALL services that share the given trace_id.
    Extremely useful for distributed tracing across microservices.
    """
    base_url, headers = await _get_loki_client(tenant_id)
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (60 * 60 * int(1e9))  # Last 1 hour

    # Search all services for this trace_id
    logql = f'{{}} |= `{trace_id}`'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": 200,
                },
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            streams = data.get("data", {}).get("result", [])
            if not streams:
                return f"No log lines found for trace_id '{trace_id}'."

            lines = []
            for stream in streams:
                app = stream.get("stream", {}).get("app", "unknown")
                for ts, line in stream.get("values", []):
                    lines.append(f"[{_ns_to_rfc3339(ts)}] [{app}] {line}")

            lines.sort()  # Sort by timestamp
            return "\n".join(lines)
    except Exception as e:
        log.error("loki_correlate_failed", tenant_id=tenant_id, trace_id=trace_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_log_volume(tenant_id: str, service: str, minutes: int = 60) -> str:
    """
    Get log lines per minute to detect spikes — useful for identifying
    sudden bursts of activity or logging storms.
    """
    base_url, headers = await _get_loki_client(tenant_id)

    logql = f'sum(count_over_time({{app="{service}"}}[1m]))'
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (minutes * 60 * int(1e9))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "step": "60",
                },
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"No log volume data for service '{service}'."

            values = results[0].get("values", [])
            lines = []
            for ts, count in values:
                readable = _ns_to_rfc3339(str(int(float(ts) * 1e9)))
                lines.append(f"{readable}: {count} lines/min")

            return "\n".join(lines[-20:])  # Last 20 data points
    except Exception as e:
        log.error("loki_log_volume_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8003)
