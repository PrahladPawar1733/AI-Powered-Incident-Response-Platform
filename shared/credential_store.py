"""
Credential Store — PostgreSQL-backed credential management for tenant infrastructure.

Each tenant stores their K8s, Prometheus, Loki, and Database credentials here.
The MCP servers call `get_credentials(tenant_id)` to dynamically load the
right connection details before making any tool calls.
"""
from __future__ import annotations
from typing import Optional
from sqlalchemy import text
from shared.pg_client import PostgresClient
from shared.models.credentials import TenantCredentials
from shared.config import settings
from shared.logger import get_logger
import json

log = get_logger("credential-store")

# Singleton client reused across credential operations
_pg = PostgresClient(settings.postgres_url)


async def save_credentials(creds: TenantCredentials) -> None:
    """
    Upsert tenant credentials into the database.
    Stores the full credential payload as JSONB for flexible schema evolution.
    """
    async with _pg.session() as sess:
        await sess.execute(
            text("""
                INSERT INTO tenant_credentials (tenant_id, credentials, updated_at)
                VALUES (:tenant_id, CAST(:credentials AS jsonb), NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                    credentials = CAST(:credentials AS jsonb),
                    updated_at  = NOW()
            """),
            {
                "tenant_id": creds.tenant_id,
                "credentials": creds.model_dump_json(exclude={"tenant_id", "created_at", "updated_at"}),
            },
        )
    log.info("credentials_saved", tenant_id=creds.tenant_id)


async def get_credentials(tenant_id: str) -> Optional[TenantCredentials]:
    """
    Load credentials for a tenant. Returns None if no credentials are registered.
    """
    async with _pg.session() as sess:
        result = await sess.execute(
            text("SELECT credentials FROM tenant_credentials WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        row = result.fetchone()
        if not row:
            log.warning("no_credentials_found", tenant_id=tenant_id)
            return None

        data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        data["tenant_id"] = tenant_id
        return TenantCredentials(**data)


async def delete_credentials(tenant_id: str) -> bool:
    """Remove all credentials for a tenant."""
    async with _pg.session() as sess:
        result = await sess.execute(
            text("DELETE FROM tenant_credentials WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        deleted = result.rowcount > 0
        if deleted:
            log.info("credentials_deleted", tenant_id=tenant_id)
        return deleted
