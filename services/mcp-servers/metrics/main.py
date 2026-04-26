"""
Metrics MCP Server — Real Prometheus HTTP API integration.

Loads the tenant's Prometheus credentials from the credential store,
then uses httpx to execute real PromQL queries against their Prometheus instance.
Falls back to the platform's local Prometheus if no tenant credentials exist.
"""
from fastmcp import FastMCP
import httpx
import json

from shared.credential_store import get_credentials
from shared.config import settings
from shared.logger import get_logger

log = get_logger("metrics-mcp")
mcp = FastMCP("metrics-mcp")

# Default fallback: our local Prometheus from docker-compose
DEFAULT_PROMETHEUS_URL = "http://localhost:9090"


async def _get_prometheus_client(tenant_id: str) -> tuple[str, dict]:
    """
    Returns (base_url, headers) for the tenant's Prometheus.
    Falls back to the local Prometheus if no credentials are registered.
    """
    creds = await get_credentials(tenant_id)
    headers = {}

    if creds and creds.prometheus:
        base_url = creds.prometheus.base_url.rstrip("/")
        if creds.prometheus.auth_type == "bearer" and creds.prometheus.bearer_token:
            headers["Authorization"] = f"Bearer {creds.prometheus.bearer_token}"
        elif creds.prometheus.auth_type == "basic":
            # httpx handles basic auth via the auth parameter, but we set header for simplicity
            import base64
            token = base64.b64encode(
                f"{creds.prometheus.username}:{creds.prometheus.password}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {token}"
        log.info("using_tenant_prometheus", tenant_id=tenant_id, url=base_url)
    else:
        base_url = DEFAULT_PROMETHEUS_URL
        log.info("using_default_prometheus", tenant_id=tenant_id, url=base_url)

    return base_url, headers


@mcp.tool()
async def query_prometheus(tenant_id: str, promql: str, time_range_minutes: int = 30) -> str:
    """
    Execute a raw PromQL query against the tenant's Prometheus instance.
    Returns the JSON result directly.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return json.dumps(data, indent=2)
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to Prometheus at {base_url}. Is it running?"
    except Exception as e:
        log.error("prometheus_query_failed", tenant_id=tenant_id, promql=promql, error=str(e))
        return f"ERROR querying Prometheus: {str(e)}"


@mcp.tool()
async def query_prometheus_range(
    tenant_id: str, promql: str, start: str, end: str, step: str = "60s"
) -> str:
    """
    Execute a range query against Prometheus.
    start/end should be RFC3339 or unix timestamps. step is the resolution.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return json.dumps(data, indent=2)
    except Exception as e:
        log.error("prometheus_range_query_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_service_latency(tenant_id: str, service: str, percentile: int = 99) -> str:
    """
    Get p50/p95/p99 latency for a service using standard histogram metrics.
    Constructs a PromQL histogram_quantile query for the given percentile.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)
    quantile = percentile / 100.0

    promql = (
        f'histogram_quantile({quantile}, '
        f'sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"No latency data found for service '{service}'. Ensure http_request_duration_seconds_bucket metric exists."

            value = results[0].get("value", [None, "N/A"])[1]
            return f"p{percentile} latency for {service}: {float(value)*1000:.1f}ms"
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to Prometheus at {base_url}"
    except Exception as e:
        log.error("latency_query_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_error_rate_history(tenant_id: str, service: str, minutes: int = 60) -> str:
    """
    Get the HTTP error rate percentage for a service over time.
    Uses rate of 5xx responses divided by total requests.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)

    promql = (
        f'100 * sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m])) '
        f'/ sum(rate(http_requests_total{{service="{service}"}}[5m]))'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"No error rate data found for service '{service}'."

            value = results[0].get("value", [None, "0"])[1]
            return f"Current error rate for {service}: {float(value):.2f}%"
    except Exception as e:
        log.error("error_rate_query_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_saturation_metrics(tenant_id: str, service: str) -> str:
    """
    Get CPU usage, memory usage, and container restarts for USE method analysis.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)

    queries = {
        "CPU Usage": f'sum(rate(container_cpu_usage_seconds_total{{pod=~"{service}.*"}}[5m])) * 100',
        "Memory Usage MB": f'sum(container_memory_working_set_bytes{{pod=~"{service}.*"}}) / 1024 / 1024',
        "Restarts": f'sum(kube_pod_container_status_restarts_total{{pod=~"{service}.*"}})',
    }

    results = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for label, promql in queries.items():
                response = await client.get(
                    f"{base_url}/api/v1/query",
                    params={"query": promql},
                    headers=headers,
                )
                if response.status_code == 200:
                    data = response.json()
                    query_results = data.get("data", {}).get("result", [])
                    if query_results:
                        value = query_results[0].get("value", [None, "N/A"])[1]
                        results.append(f"{label}: {value}")
                    else:
                        results.append(f"{label}: no data")
                else:
                    results.append(f"{label}: query failed ({response.status_code})")

        return "\n".join(results) if results else f"No saturation metrics found for '{service}'."
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to Prometheus at {base_url}"
    except Exception as e:
        log.error("saturation_query_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def get_sli_status(tenant_id: str, service: str) -> str:
    """
    Get current SLI (availability) vs SLO target.
    Uses success rate of HTTP requests as the availability SLI.
    """
    base_url, headers = await _get_prometheus_client(tenant_id)

    promql = (
        f'100 * sum(rate(http_requests_total{{service="{service}",status!~"5.."}}[1h])) '
        f'/ sum(rate(http_requests_total{{service="{service}"}}[1h]))'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/query",
                params={"query": promql},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"No SLI data found for service '{service}'."

            availability = float(results[0].get("value", [None, "0"])[1])
            slo_target = 99.9  # Standard SLO target
            status = "HEALTHY" if availability >= slo_target else "VIOLATION"
            return f"SLI: {availability:.2f}% availability | SLO Target: {slo_target}% | Status: {status}"
    except Exception as e:
        log.error("sli_query_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8004)
