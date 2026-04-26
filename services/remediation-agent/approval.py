"""
Approval Gate — Human-in-the-loop approval for medium/high risk actions.

Uses Redis as the approval store. The flow:
1. Remediation Agent creates an approval request in Redis
2. Human calls POST /approvals/{request_id}/approve (or Slack button in production)
3. Remediation Agent polls Redis until approved/rejected/expired
"""
from __future__ import annotations
import asyncio
from uuid import uuid4

from shared.redis_client import RedisClient
from shared.logger import get_logger

log = get_logger("approval-gate")

# How often to poll Redis for approval status
POLL_INTERVAL_SECONDS = 2

# Timeout for medium risk (5 minutes)
MEDIUM_TIMEOUT_SECONDS = 300

# High risk has no timeout — we poll forever (but cap at 1 hour for safety)
HIGH_TIMEOUT_SECONDS = 3600


async def request_approval(
    redis: RedisClient,
    tenant_id: str,
    incident_id: str,
    action_summary: str,
    risk_level: str,
) -> str:
    """
    Create an approval request and return the request_id.
    The human-facing API or Slack bot uses this ID to approve/reject.
    """
    request_id = str(uuid4())

    await redis.set_approval_token(
        tenant_id=tenant_id,
        request_id=request_id,
        risk_level=risk_level,
        incident_id=incident_id,
    )

    log.info(
        "approval_requested",
        request_id=request_id,
        incident_id=incident_id,
        risk_level=risk_level,
        action=action_summary,
    )

    # In production, this is where you'd post to Slack:
    # await slack.post_approval_message(channel, incident_id, action_summary, request_id)
    log.info(
        "HUMAN_ACTION_REQUIRED",
        message=f"Approve via: POST /approvals/{request_id}/approve",
        action=action_summary,
        risk=risk_level,
    )

    return request_id


async def wait_for_approval(
    redis: RedisClient,
    tenant_id: str,
    request_id: str,
    risk_level: str,
) -> str:
    """
    Poll Redis until the approval is approved, rejected, or times out.
    Returns: 'approved', 'rejected', or 'expired'
    """
    timeout = MEDIUM_TIMEOUT_SECONDS if risk_level == "medium" else HIGH_TIMEOUT_SECONDS
    elapsed = 0

    while elapsed < timeout:
        token = await redis.get_approval_token(tenant_id, request_id)

        if token is None:
            # Token expired (TTL elapsed) → escalate
            log.warning("approval_expired", request_id=request_id, elapsed=elapsed)
            return "expired"

        status = token.get("status", "pending")
        if status in ("approved", "rejected"):
            log.info(
                "approval_resolved",
                request_id=request_id,
                status=status,
                approved_by=token.get("approved_by"),
            )
            return status

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    # Timeout reached
    log.warning("approval_timeout", request_id=request_id, timeout=timeout)
    return "expired"
