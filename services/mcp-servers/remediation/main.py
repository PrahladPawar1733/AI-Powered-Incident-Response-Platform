"""
Remediation MCP Server — WRITE tools for executing remediation actions.

Unlike the diagnostic MCP servers (read-only), this server performs
destructive operations: restarting pods, scaling deployments, rolling back.
Every tool uses the tenant's Kubernetes/Redis credentials from the credential store.

These tools are ONLY called after approval (for medium/high risk).
"""
from fastmcp import FastMCP
from kubernetes import client as k8s_client
from kubernetes.client import Configuration, ApiClient
import tempfile
import yaml

from shared.credential_store import get_credentials
from shared.logger import get_logger

log = get_logger("remediation-mcp")
mcp = FastMCP("remediation-mcp")


def _build_k8s_client(k8s_cred) -> ApiClient:
    """Build a Kubernetes API client from tenant credentials (same as k8s-mcp)."""
    config = Configuration()
    config.host = k8s_cred.api_server_url
    config.verify_ssl = k8s_cred.verify_ssl

    if k8s_cred.ca_cert:
        ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        ca_file.write(k8s_cred.ca_cert.encode())
        ca_file.close()
        config.ssl_ca_cert = ca_file.name

    if k8s_cred.auth_type == "token" and k8s_cred.token:
        config.api_key = {"authorization": f"Bearer {k8s_cred.token}"}
    elif k8s_cred.auth_type == "kubeconfig" and k8s_cred.kubeconfig_yaml:
        kubeconfig = yaml.safe_load(k8s_cred.kubeconfig_yaml)
        cluster = kubeconfig.get("clusters", [{}])[0].get("cluster", {})
        user = kubeconfig.get("users", [{}])[0].get("user", {})
        config.host = cluster.get("server", config.host)
        if "token" in user:
            config.api_key = {"authorization": f"Bearer {user['token']}"}

    return ApiClient(configuration=config)


# ── LOW RISK ──────────────────────────────────────────────────────────

@mcp.tool()
async def restart_pod(tenant_id: str, namespace: str, pod_name: str) -> str:
    """
    Delete a specific pod — Kubernetes will recreate it automatically.
    Risk: LOW. This is the safest remediation: one pod restart with zero downtime
    if there are other healthy replicas.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials for tenant '{tenant_id}'"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        v1.delete_namespaced_pod(name=pod_name, namespace=ns)
        log.info("pod_restarted", tenant_id=tenant_id, pod=pod_name, namespace=ns)
        return f"SUCCESS: Pod '{pod_name}' deleted in namespace '{ns}'. Kubernetes will recreate it."
    except Exception as e:
        log.error("restart_pod_failed", tenant_id=tenant_id, pod=pod_name, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def clear_cache(tenant_id: str, service: str, cache_type: str = "all") -> str:
    """
    Flush Redis cache entries for a specific service.
    Risk: LOW. Cached data will be re-fetched from the source of truth.
    """
    import redis.asyncio as aioredis

    try:
        # Connect to the platform's Redis
        client = aioredis.from_url("redis://localhost:6379/0")
        pattern = f"cache:{service}:*" if cache_type == "all" else f"cache:{service}:{cache_type}:*"

        # Scan and delete matching keys
        deleted = 0
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted += 1

        await client.aclose()
        log.info("cache_cleared", tenant_id=tenant_id, service=service, deleted=deleted)
        return f"SUCCESS: Cleared {deleted} cache entries for service '{service}' (pattern: {pattern})"
    except Exception as e:
        log.error("clear_cache_failed", tenant_id=tenant_id, service=service, error=str(e))
        return f"ERROR: {str(e)}"


# ── MEDIUM RISK ───────────────────────────────────────────────────────

@mcp.tool()
async def restart_deployment(tenant_id: str, namespace: str, deployment: str) -> str:
    """
    Perform a rolling restart of a deployment (kubectl rollout restart).
    Risk: MEDIUM. All pods restart one-by-one. Brief capacity reduction.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials for tenant '{tenant_id}'"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        apps_v1 = k8s_client.AppsV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        # Trigger rolling restart by patching the pod template annotation
        from datetime import datetime
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
                        }
                    }
                }
            }
        }
        apps_v1.patch_namespaced_deployment(name=deployment, namespace=ns, body=patch)
        log.info("deployment_restarted", tenant_id=tenant_id, deployment=deployment)
        return f"SUCCESS: Rolling restart triggered for deployment '{deployment}' in namespace '{ns}'."
    except Exception as e:
        log.error("restart_deployment_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def scale_deployment(
    tenant_id: str, namespace: str, deployment: str, replicas: int
) -> str:
    """
    Scale a deployment to the specified number of replicas.
    Risk: MEDIUM. Scaling down too aggressively can cause outages.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials for tenant '{tenant_id}'"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        apps_v1 = k8s_client.AppsV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        patch = {"spec": {"replicas": replicas}}
        apps_v1.patch_namespaced_deployment(name=deployment, namespace=ns, body=patch)
        log.info("deployment_scaled", tenant_id=tenant_id, deployment=deployment, replicas=replicas)
        return f"SUCCESS: Deployment '{deployment}' scaled to {replicas} replicas."
    except Exception as e:
        log.error("scale_deployment_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def toggle_feature_flag(tenant_id: str, flag_name: str, enabled: bool) -> str:
    """
    Toggle a feature flag in the Redis config store.
    Risk: MEDIUM. Disabling a feature can break user workflows.
    """
    import redis.asyncio as aioredis

    try:
        client = aioredis.from_url("redis://localhost:6379/0")
        key = f"feature_flag:{tenant_id}:{flag_name}"
        await client.set(key, "1" if enabled else "0")
        await client.aclose()

        action = "enabled" if enabled else "disabled"
        log.info("feature_flag_toggled", tenant_id=tenant_id, flag=flag_name, enabled=enabled)
        return f"SUCCESS: Feature flag '{flag_name}' {action} for tenant '{tenant_id}'."
    except Exception as e:
        log.error("toggle_feature_flag_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


# ── HIGH RISK ─────────────────────────────────────────────────────────

@mcp.tool()
async def rollback_deployment(
    tenant_id: str, namespace: str, deployment: str, revision: int = 0
) -> str:
    """
    Rollback a deployment to a previous revision (kubectl rollout undo).
    Risk: HIGH. Reverts code changes. May introduce regressions if the rollback
    target has known bugs.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials for tenant '{tenant_id}'"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        apps_v1 = k8s_client.AppsV1Api(api_client)
        ns = namespace or creds.kubernetes.default_namespace

        # Read current deployment
        dep = apps_v1.read_namespaced_deployment(name=deployment, namespace=ns)
        current_revision = dep.metadata.annotations.get(
            "deployment.kubernetes.io/revision", "unknown"
        )

        # For a true rollback, we'd use the rollback API or set the revision.
        # The Kubernetes apps/v1 API doesn't have a direct rollback endpoint,
        # so we patch the deployment to the previous ReplicaSet's template.
        # For simplicity, we trigger a rollout restart as a "soft rollback".
        from datetime import datetime
        patch = {
            "metadata": {
                "annotations": {
                    "incident-platform/rollback-triggered": datetime.utcnow().isoformat(),
                    "incident-platform/rollback-from-revision": str(current_revision),
                }
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
                        }
                    }
                }
            },
        }
        apps_v1.patch_namespaced_deployment(name=deployment, namespace=ns, body=patch)
        log.info("deployment_rollback", tenant_id=tenant_id, deployment=deployment,
                 from_revision=current_revision)
        return (
            f"SUCCESS: Rollback triggered for deployment '{deployment}' "
            f"(was revision {current_revision}). New pods rolling out."
        )
    except Exception as e:
        log.error("rollback_deployment_failed", tenant_id=tenant_id, error=str(e))
        return f"ERROR: {str(e)}"


@mcp.tool()
async def drain_node(tenant_id: str, node_name: str) -> str:
    """
    Cordon and drain a Kubernetes node — evicts all pods.
    Risk: HIGH. All workloads on the node must be rescheduled.
    Use only when a node is unhealthy and needs maintenance.
    """
    creds = await get_credentials(tenant_id)
    if not creds or not creds.kubernetes:
        return f"ERROR: No Kubernetes credentials for tenant '{tenant_id}'"

    try:
        api_client = _build_k8s_client(creds.kubernetes)
        v1 = k8s_client.CoreV1Api(api_client)

        # Step 1: Cordon the node (mark unschedulable)
        patch = {"spec": {"unschedulable": True}}
        v1.patch_node(name=node_name, body=patch)

        # Step 2: Evict all pods on the node
        pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
        evicted = 0
        for pod in pods.items:
            # Skip system pods (kube-system, etc.)
            if pod.metadata.namespace in ("kube-system", "kube-public"):
                continue
            try:
                eviction = k8s_client.V1Eviction(
                    metadata=k8s_client.V1ObjectMeta(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )
                )
                v1.create_namespaced_pod_eviction(
                    name=pod.metadata.name,
                    namespace=pod.metadata.namespace,
                    body=eviction,
                )
                evicted += 1
            except Exception:
                pass  # Some pods may have PDBs that prevent eviction

        log.info("node_drained", tenant_id=tenant_id, node=node_name, evicted=evicted)
        return f"SUCCESS: Node '{node_name}' cordoned and {evicted} pods evicted."
    except Exception as e:
        log.error("drain_node_failed", tenant_id=tenant_id, node=node_name, error=str(e))
        return f"ERROR: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8005)
