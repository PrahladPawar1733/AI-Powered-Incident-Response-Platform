# shared/redis_client.py
"""
Async Redis client for the incident response platform.

Handles:
- Alert dedup (fingerprint-based, 10-min TTL)
- Incident status tracking (24-hr TTL)
- Incident context caching (full JSON, 24-hr TTL)
- Approval tokens (TTL based on risk level)
- Session tracking

Key patterns (multi-tenant — tenant_id prefix on all keys):
    alert:dedup:{tenant_id}:{fingerprint}         — string, 10 min TTL
    incident:status:{tenant_id}:{incident_id}     — string, 24 hr TTL
    incident:context:{tenant_id}:{incident_id}    — string (JSON), 24 hr TTL
    approval:token:{tenant_id}:{request_id}       — string, per risk level
    session:incident:{session_id}                  — set, 24 hr TTL

Why redis.asyncio?
    All services in this platform are async (FastAPI, asyncpg).
    Using sync Redis would block the event loop and kill throughput.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from shared.logger import get_logger

log = get_logger("redis-client")

# ── TTL constants (seconds) ──────────────────────────────────────────
DEDUP_TTL = 600          # 10 minutes — flapping alert window
STATUS_TTL = 86_400      # 24 hours
CONTEXT_TTL = 86_400     # 24 hours
SESSION_TTL = 86_400     # 24 hours

# Approval TTLs by risk level
APPROVAL_TTLS = {
    "low":    0,         # low risk auto-executes, no token needed
    "medium": 300,       # 5 minutes then escalate
    "high":   0,         # no timeout — human must respond (0 = no expiry)
}


class RedisClient:
    """
    Async Redis wrapper — all platform Redis operations go through here.

    Never use raw Redis keys in service code. Always call these methods.
    This ensures consistent key prefixes, TTLs, and logging.
    """

    def __init__(self, redis: aioredis.Redis):
        self._redis = redis

    # ── Alert deduplication ──────────────────────────────────────────

    async def is_duplicate(self, tenant_id: str, fingerprint: str) -> bool:
        """
        Check if this alert fingerprint was seen in the last 10 minutes.

        fingerprint = "{alert_name}:{service}:{environment}"
        Returns True if the alert is a duplicate (already in Redis).

        Why dedup?
        During a real incident, Prometheus fires the same alert every
        evaluation interval (15-60 seconds). Without dedup, a single
        incident spawns dozens of IncidentContext objects.

        Why tenant_id in the key?
        Without it, Tenant A's alert could suppress Tenant B's identical
        alert — a cross-tenant data leak.
        """
        key = f"alert:dedup:{tenant_id}:{fingerprint}"
        exists = await self._redis.exists(key)
        return bool(exists)

    async def mark_seen(self, tenant_id: str, fingerprint: str, ttl: int = DEDUP_TTL) -> None:
        """
        Mark this fingerprint as seen. Uses SET with EX (TTL).
        After TTL expires, the same alert pattern will be treated as new.
        """
        key = f"alert:dedup:{tenant_id}:{fingerprint}"
        await self._redis.set(key, "1", ex=ttl)
        log.debug("alert_dedup_marked", tenant_id=tenant_id, fingerprint=fingerprint, ttl=ttl)

    # ── Incident status tracking ─────────────────────────────────────

    async def set_incident_status(
        self, tenant_id: str, incident_id: str, status: str
    ) -> None:
        """
        Cache the current incident status for fast lookups.
        The dashboard uses this to show live status without hitting Postgres.
        """
        key = f"incident:status:{tenant_id}:{incident_id}"
        await self._redis.set(key, status, ex=STATUS_TTL)
        log.debug("incident_status_set",
                  tenant_id=tenant_id, incident_id=incident_id, status=status)

    async def get_incident_status(self, tenant_id: str, incident_id: str) -> str | None:
        """Get cached incident status. Returns None if expired/missing."""
        key = f"incident:status:{tenant_id}:{incident_id}"
        val = await self._redis.get(key)
        return val.decode("utf-8") if val else None

    # ── Incident context caching ─────────────────────────────────────

    async def cache_incident(
        self, tenant_id: str, incident_id: str, context: dict[str, Any]
    ) -> None:
        """
        Cache the full IncidentContext JSON.
        Agents and the dashboard read this instead of hitting Postgres
        for in-progress incidents.
        """
        key = f"incident:context:{tenant_id}:{incident_id}"
        await self._redis.set(
            key,
            json.dumps(context, default=str),
            ex=CONTEXT_TTL,
        )

    async def get_cached_incident(
        self, tenant_id: str, incident_id: str
    ) -> dict[str, Any] | None:
        """
        Retrieve cached IncidentContext. Returns None if not cached.
        """
        key = f"incident:context:{tenant_id}:{incident_id}"
        val = await self._redis.get(key)
        if val:
            return json.loads(val.decode("utf-8"))
        return None

    # ── Approval tokens ──────────────────────────────────────────────

    async def set_approval_token(
        self,
        tenant_id: str,
        request_id: str,
        risk_level: str,
        incident_id: str,
    ) -> None:
        """
        Create an approval token for a remediation action.

        LOW risk:    no token needed (auto-execute)
        MEDIUM risk: 5-minute TTL — escalate if no response
        HIGH risk:   no expiry — human must respond

        The Slack approval handler looks up this token to verify
        the request is still valid before executing.
        """
        key = f"approval:token:{tenant_id}:{request_id}"
        ttl = APPROVAL_TTLS.get(risk_level, 300)
        value = json.dumps({
            "request_id": request_id,
            "incident_id": incident_id,
            "tenant_id": tenant_id,
            "risk_level": risk_level,
            "status": "pending",
        })

        if ttl > 0:
            await self._redis.set(key, value, ex=ttl)
        else:
            # No expiry for high-risk — stays until explicitly resolved
            await self._redis.set(key, value)

        log.info("approval_token_created",
                 tenant_id=tenant_id, request_id=request_id,
                 risk_level=risk_level, ttl=ttl if ttl > 0 else "no_expiry")

    async def get_approval_token(
        self, tenant_id: str, request_id: str
    ) -> dict[str, Any] | None:
        """
        Retrieve an approval token. Returns None if expired or missing.
        An expired token means the approval window closed → escalate.
        """
        key = f"approval:token:{tenant_id}:{request_id}"
        val = await self._redis.get(key)
        if val:
            return json.loads(val.decode("utf-8"))
        return None

    async def resolve_approval_token(
        self,
        tenant_id: str,
        request_id: str,
        status: str,
        approved_by: str | None = None,
    ) -> bool:
        """
        Mark an approval token as approved/rejected.
        Returns False if the token expired (TTL elapsed).
        """
        key = f"approval:token:{tenant_id}:{request_id}"
        val = await self._redis.get(key)
        if not val:
            return False  # expired or doesn't exist

        token = json.loads(val.decode("utf-8"))
        token["status"] = status
        if approved_by:
            token["approved_by"] = approved_by

        # Keep the remaining TTL
        remaining_ttl = await self._redis.ttl(key)
        if remaining_ttl > 0:
            await self._redis.set(key, json.dumps(token), ex=remaining_ttl)
        else:
            await self._redis.set(key, json.dumps(token))

        log.info("approval_token_resolved",
                 tenant_id=tenant_id, request_id=request_id,
                 status=status, approved_by=approved_by)
        return True

    # ── Session tracking ─────────────────────────────────────────────

    async def add_to_session(
        self, session_id: str, incident_id: str
    ) -> None:
        """Track which incidents belong to a dashboard session."""
        key = f"session:incident:{session_id}"
        await self._redis.sadd(key, incident_id)
        await self._redis.expire(key, SESSION_TTL)

    async def get_session_incidents(
        self, session_id: str
    ) -> set[str]:
        """Get all incident IDs in a session."""
        key = f"session:incident:{session_id}"
        members = await self._redis.smembers(key)
        return {m.decode("utf-8") for m in members} if members else set()

    # ── Lifecycle ────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Ping Redis — used by /health endpoints."""
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def close(self) -> None:
        """Close the connection pool gracefully."""
        await self._redis.aclose()


# ── Factory function ─────────────────────────────────────────────────

async def init_redis(
    redis_url: str = "redis://localhost:6379/0",
    max_connections: int = 20,
) -> RedisClient:
    """
    Create and return a configured RedisClient.

    Usage at service startup:
        from shared.redis_client import init_redis
        redis = await init_redis(settings.redis_url)

    The connection pool is created once and shared across
    all coroutines — thread-safe and connection-efficient.
    """
    pool = aioredis.ConnectionPool.from_url(
        redis_url,
        max_connections=max_connections,
        decode_responses=False,  # we handle encoding ourselves
    )
    client = aioredis.Redis(connection_pool=pool)

    # Verify connectivity at startup
    try:
        await client.ping()
        log.info("redis_connected", url=redis_url.split("@")[-1])  # don't log passwords
    except Exception as exc:
        log.error("redis_connection_failed", url=redis_url, error=str(exc))
        raise

    return RedisClient(redis=client)
