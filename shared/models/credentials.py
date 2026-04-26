"""
Tenant Infrastructure Credentials — Pydantic Models

Each tenant can register their own Kubernetes cluster, Prometheus instance,
Loki endpoint, and Database connection. The MCP servers look up these
credentials by tenant_id before making any API calls.

Security note: In production, secrets (tokens, passwords) should be stored
in a vault (HashiCorp Vault, AWS Secrets Manager) and referenced by ARN.
For this implementation, we store them encrypted-at-rest in PostgreSQL.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class CredentialType(str, Enum):
    KUBERNETES = "kubernetes"
    PROMETHEUS = "prometheus"
    LOKI       = "loki"
    DATABASE   = "database"


class KubernetesCredential(BaseModel):
    """Kubernetes cluster access configuration."""
    api_server_url: str = Field(..., description="e.g. https://my-cluster.k8s.io:6443")
    auth_type: str = Field(
        default="token",
        description="'token' (service account), 'kubeconfig' (raw kubeconfig YAML), or 'in_cluster'"
    )
    token: Optional[str] = Field(default=None, description="Bearer token for service account auth")
    kubeconfig_yaml: Optional[str] = Field(default=None, description="Raw kubeconfig YAML content")
    ca_cert: Optional[str] = Field(default=None, description="CA certificate PEM (if self-signed)")
    default_namespace: str = Field(default="default")
    verify_ssl: bool = Field(default=True)


class PrometheusCredential(BaseModel):
    """Prometheus / Grafana Mimir / Thanos connection."""
    base_url: str = Field(..., description="e.g. http://prometheus:9090 or https://mimir.company.com")
    auth_type: str = Field(
        default="none",
        description="'none', 'basic', or 'bearer'"
    )
    username: Optional[str] = Field(default=None, description="For basic auth")
    password: Optional[str] = Field(default=None, description="For basic auth")
    bearer_token: Optional[str] = Field(default=None, description="For bearer auth")


class LokiCredential(BaseModel):
    """Grafana Loki / log aggregation connection."""
    base_url: str = Field(..., description="e.g. http://loki:3100 or https://logs.company.com")
    auth_type: str = Field(
        default="none",
        description="'none', 'basic', 'bearer', or 'x-scope-orgid' (Loki multi-tenant header)"
    )
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    bearer_token: Optional[str] = Field(default=None)
    org_id: Optional[str] = Field(default=None, description="X-Scope-OrgID header for Loki multi-tenancy")


class DatabaseCredential(BaseModel):
    """External database connection for diagnostics (read-only)."""
    connection_url: str = Field(..., description="e.g. postgresql://ro_user:pass@db-host:5432/mydb")
    db_type: str = Field(default="postgresql", description="'postgresql', 'mysql'")
    ssl_mode: str = Field(default="prefer")


class TenantCredentials(BaseModel):
    """
    All infrastructure credentials for a single tenant.
    Stored as a JSONB column in the tenant_credentials table.
    """
    tenant_id: str
    kubernetes: Optional[KubernetesCredential] = None
    prometheus: Optional[PrometheusCredential] = None
    loki: Optional[LokiCredential] = None
    database: Optional[DatabaseCredential] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
