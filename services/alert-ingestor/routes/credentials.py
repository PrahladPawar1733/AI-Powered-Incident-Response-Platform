"""
Credential Management API — FastAPI routes for tenants to register
their infrastructure endpoints (K8s, Prometheus, Loki, Database).

Mounted on the alert-ingestor service at /credentials/*.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException

from shared.auth import extract_tenant
from shared.models.credentials import (
    TenantCredentials,
    KubernetesCredential,
    PrometheusCredential,
    LokiCredential,
    DatabaseCredential,
)
from shared.credential_store import save_credentials, get_credentials, delete_credentials

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("/", summary="Get my tenant's credentials (redacted)")
async def get_my_credentials(tenant_id: str = Depends(extract_tenant)):
    """Returns the current tenant's registered credentials with secrets redacted."""
    creds = await get_credentials(tenant_id)
    if not creds:
        return {"tenant_id": tenant_id, "message": "No credentials registered yet."}

    # Redact sensitive fields before returning
    result = {"tenant_id": tenant_id}
    if creds.kubernetes:
        result["kubernetes"] = {
            "api_server_url": creds.kubernetes.api_server_url,
            "auth_type": creds.kubernetes.auth_type,
            "default_namespace": creds.kubernetes.default_namespace,
            "token": "***REDACTED***" if creds.kubernetes.token else None,
        }
    if creds.prometheus:
        result["prometheus"] = {
            "base_url": creds.prometheus.base_url,
            "auth_type": creds.prometheus.auth_type,
        }
    if creds.loki:
        result["loki"] = {
            "base_url": creds.loki.base_url,
            "auth_type": creds.loki.auth_type,
        }
    if creds.database:
        result["database"] = {
            "db_type": creds.database.db_type,
            "connection_url": "***REDACTED***",
        }
    return result


@router.put("/kubernetes", summary="Register Kubernetes cluster")
async def set_kubernetes(
    cred: KubernetesCredential,
    tenant_id: str = Depends(extract_tenant),
):
    """Register or update Kubernetes cluster credentials for this tenant."""
    existing = await get_credentials(tenant_id) or TenantCredentials(tenant_id=tenant_id)
    existing.kubernetes = cred
    await save_credentials(existing)
    return {"message": "Kubernetes credentials saved", "api_server_url": cred.api_server_url}


@router.put("/prometheus", summary="Register Prometheus endpoint")
async def set_prometheus(
    cred: PrometheusCredential,
    tenant_id: str = Depends(extract_tenant),
):
    """Register or update Prometheus/Mimir/Thanos endpoint for this tenant."""
    existing = await get_credentials(tenant_id) or TenantCredentials(tenant_id=tenant_id)
    existing.prometheus = cred
    await save_credentials(existing)
    return {"message": "Prometheus credentials saved", "base_url": cred.base_url}


@router.put("/loki", summary="Register Loki / log backend")
async def set_loki(
    cred: LokiCredential,
    tenant_id: str = Depends(extract_tenant),
):
    """Register or update Loki/Elasticsearch/log backend for this tenant."""
    existing = await get_credentials(tenant_id) or TenantCredentials(tenant_id=tenant_id)
    existing.loki = cred
    await save_credentials(existing)
    return {"message": "Loki credentials saved", "base_url": cred.base_url}


@router.put("/database", summary="Register diagnostic database")
async def set_database(
    cred: DatabaseCredential,
    tenant_id: str = Depends(extract_tenant),
):
    """Register or update an external database for read-only diagnostics."""
    existing = await get_credentials(tenant_id) or TenantCredentials(tenant_id=tenant_id)
    existing.database = cred
    await save_credentials(existing)
    return {"message": "Database credentials saved", "db_type": cred.db_type}


@router.delete("/", summary="Delete all my credentials")
async def remove_credentials(tenant_id: str = Depends(extract_tenant)):
    """Remove all registered infrastructure credentials for this tenant."""
    deleted = await delete_credentials(tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No credentials found for this tenant")
    return {"message": "All credentials deleted"}
