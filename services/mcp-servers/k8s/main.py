"""
K8s MCP Server — Real Kubernetes API integration.

Loads the tenant's Kubernetes credentials from the credential store,
then uses the official `kubernetes` Python client to query their cluster.
If no credentials are registered, returns a clear error message.
"""
from fastmcp import FastMCP
from kubernetes import client as k8s_client
from kubernetes.client import Configuration, ApiClient
import tempfile
import os
import yaml

from shared.credential_store import get_credentials
from shared.logger import get_logger

log = get_logger("k8s-mcp")
mcp = FastMCP("k8s-mcp")


def _build_k8s_client(k8s_cred) -> ApiClient:
    """
    Build a Kubernetes API client from tenant credentials.
    Supports token-based auth, raw kubeconfig, and in-cluster config.
    """
    config = Configuration()
    config.host = k8s_cred.api_server_url
    config.verify_ssl = k8s_cred.verify_ssl

    if k8s_cred.ca_cert:
        # Write CA cert to a temp file for the k8s client
        ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        ca_file.write(k8s_cred.ca_cert.encode())
        ca_file.close()
        config.ssl_ca_cert = ca_file.name

    if k8s_cred.auth_type == "token" and k8s_cred.token:
        config.api_key = {"authorization": f"Bearer {k8s_cred.token}"}
    elif k8s_cred.auth_type == "kubeconfig" and k8s_cred.kubeconfig_yaml:
        # Parse kubeconfig and extract credentials
        kubeconfig = yaml.safe_load(k8s_cred.kubeconfig_yaml)
        # Use the first context's cluster and user
        cluster = kubeconfig.get("clusters", [{}])[0].get("cluster", {})
        user = kubeconfig.get("users", [{}])[0].get("user", {})
        config.host = cluster.get("server", config.host)
        if "token" in user:
            config.api_key = {"authorization": f"Bearer {user['token']}"}
        elif "client-certificate-data" in user:
            cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
            cert_file.write(user["client-certificate-data"].encode())
            cert_file.close()
            config.cert_file = cert_file.name

    return ApiClient(configuration=config)


@mcp.tool()
async def get_pod_status(tenant_id: str, namespace: str, service: str) -> str:
    """
    Get the status of pods for a specific service in a namespace.
    Uses the tenant's registered Kubernetes cluster credentials.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'. Register via PUT /credentials/kubernetes"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        pods = v1.list_namespaced_pod(
            namespace=ns,
            label_selector=f"app={service}"
        )

        if not pods.items:
            return f"No pods found with label app={service} in namespace {ns}"

        lines = []
        for pod in pods.items:
            name = pod.metadata.name
            phase = pod.status.phase
            restarts = sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            )
            age = pod.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M") if pod.metadata.creation_timestamp else "unknown"
            lines.append(f"{name}\t{phase}\tRestarts: {restarts}\tCreated: {age}")

        return "\n".join(lines)
    except Exception as e:
        log.error("k8s_get_pod_status_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR querying Kubernetes: {str(e)}"


@mcp.tool()
async def get_pod_logs(
    tenant_id: str, namespace: str, pod_name: str,
    tail: int = 100, previous: bool = False
) -> str:
    """
    Get the last N log lines for a specific pod.
    If previous=True, gets logs from a crashed container.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'."

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=ns,
            tail_lines=tail,
            previous=previous,
        )
        return logs if logs else "No logs available."
    except Exception as e:
        log.error("k8s_get_pod_logs_failed", tenant_id=tenant_id, pod=pod_name, error=str(e))
        return f"ERROR reading pod logs: {str(e)}"


@mcp.tool()
async def get_recent_events(
    tenant_id: str, namespace: str, service: str, minutes: int = 30
) -> str:
    """
    Get recent Kubernetes events filtered by service label.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'."

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        events = v1.list_namespaced_event(namespace=ns)

        relevant = []
        for event in events.items:
            obj_name = event.involved_object.name or ""
            if service.lower() in obj_name.lower():
                ts = event.last_timestamp or event.event_time or "unknown"
                relevant.append(
                    f"{ts}\t{event.type}\t{event.reason}\t"
                    f"{event.involved_object.kind}/{obj_name}\t{event.message}"
                )

        if not relevant:
            return f"No recent events found for service '{service}' in namespace '{ns}'."

        return "\n".join(relevant[-20:])  # Last 20 events
    except Exception as e:
        log.error("k8s_get_events_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR querying events: {str(e)}"


@mcp.tool()
async def get_deployment_history(tenant_id: str, namespace: str, deployment: str) -> str:
    """
    Get rollout history for a deployment — shows recent revisions.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'."

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        apps_v1 = k8s_client.AppsV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        dep = apps_v1.read_namespaced_deployment(name=deployment, namespace=ns)
        revision = dep.metadata.annotations.get("deployment.kubernetes.io/revision", "unknown")
        image = dep.spec.template.spec.containers[0].image if dep.spec.template.spec.containers else "unknown"

        return f"Deployment: {deployment}\nCurrent revision: {revision}\nImage: {image}\nReplicas: {dep.spec.replicas}"
    except Exception as e:
        log.error("k8s_deployment_history_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR reading deployment: {str(e)}"


@mcp.tool()
async def describe_service(tenant_id: str, namespace: str, service: str) -> str:
    """
    Get full details about a Kubernetes Service object (endpoints, ports, selectors).
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'."

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        svc = v1.read_namespaced_service(name=service, namespace=ns)
        ports = ", ".join(
            f"{p.port}/{p.protocol}" for p in (svc.spec.ports or [])
        )
        selector = ", ".join(
            f"{k}={v}" for k, v in (svc.spec.selector or {}).items()
        )
        return (
            f"Name: {svc.metadata.name}\n"
            f"Namespace: {ns}\n"
            f"Type: {svc.spec.type}\n"
            f"ClusterIP: {svc.spec.cluster_ip}\n"
            f"Ports: {ports}\n"
            f"Selector: {selector}"
        )
    except Exception as e:
        log.error("k8s_describe_service_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR describing service: {str(e)}"


@mcp.tool()
async def get_resource_usage(tenant_id: str, namespace: str, service: str) -> str:
    """
    Get CPU/memory requests, limits, and current status for pods of a service.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials registered for tenant '{tenant_id}'."

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        pods = v1.list_namespaced_pod(namespace=ns, label_selector=f"app={service}")
        if not pods.items:
            return f"No pods found for service '{service}'"

        lines = []
        for pod in pods.items:
            for container in pod.spec.containers:
                requests = container.resources.requests or {} if container.resources else {}
                limits = container.resources.limits or {} if container.resources else {}
                lines.append(
                    f"Pod: {pod.metadata.name} | Container: {container.name}\n"
                    f"  CPU request: {requests.get('cpu', 'none')} | limit: {limits.get('cpu', 'none')}\n"
                    f"  Mem request: {requests.get('memory', 'none')} | limit: {limits.get('memory', 'none')}"
                )
        return "\n".join(lines)
    except Exception as e:
        log.error("k8s_resource_usage_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8001)
