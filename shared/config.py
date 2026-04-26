# shared/config.py
from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """
    All configuration for the entire platform.
    Pydantic reads from environment variables automatically.
    The Field(...) means required — startup fails with a clear
    error if the variable is missing. Field(default=...) is optional.
    """

    # ── Anthropic ────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    anthropic_model: str = Field(
        default="claude-opus-4-5",
        description="Model used by all agents"
    )

    # ── Kafka ────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(default="localhost:29092")
    kafka_schema_registry_url: str = Field(default="http://localhost:8081")

    # Topic names — defined once, used everywhere
    topic_alerts_raw: str = Field(default="alerts.raw")
    topic_alerts_triaged: str = Field(default="alerts.triaged")
    topic_incidents_active: str = Field(default="incidents.active")
    topic_audit_events: str = Field(default="audit.events")
    topic_incidents_resolved: str = Field(default="incidents.resolved")

    # ── Redis ────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(default=20)

    # ── Postgres ─────────────────────────────────────────────────────
    postgres_url: str = Field(
        default="postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db"
    )
    postgres_pool_size: int = Field(default=10)

    # ── MCP Server URLs ──────────────────────────────────────────────
    mcp_k8s_url: str = Field(default="http://localhost:8001/mcp")
    mcp_db_url: str = Field(default="http://localhost:8002/mcp")
    mcp_logs_url: str = Field(default="http://localhost:8003/mcp")
    mcp_metrics_url: str = Field(default="http://localhost:8004/mcp")
    mcp_remediation_url: str = Field(default="http://localhost:8005/mcp")

    # ── Slack ────────────────────────────────────────────────────────
    slack_bot_token: str = Field(default="")
    slack_incidents_channel: str = Field(default="#incidents")
    slack_approvals_channel: str = Field(default="#incident-approvals")

    # ── App ──────────────────────────────────────────────────────────
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    service_name: str = Field(default="unknown")

    # ── Multi-tenancy ────────────────────────────────────────────────
    jwt_secret_key: str = Field(
        default="dev-secret-change-in-production",
        description="JWT signing key — for dev/testing. Production uses a vault."
    )
    jwt_algorithm: str = Field(default="HS256")
    default_tenant_id: str = Field(
        default="default",
        description="Used for dev/testing when no JWT is present"
    )

    class Config:
        env_file = str(Path(__file__).parent.parent / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    lru_cache means this is only called once — same Settings
    object returned every time. Safe because env vars don't
    change at runtime.
    """
    return Settings()


# Convenience alias — services do: from shared.config import settings
settings = get_settings()