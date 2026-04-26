"""
Approval API — REST endpoints for human-in-the-loop approval.

In production, these would be triggered by Slack interactive buttons.
For development, humans call these via curl to approve/reject actions.

Usage:
  POST /approvals/{request_id}/approve?approved_by=john
  POST /approvals/{request_id}/reject?approved_by=john
  GET  /approvals/pending  — list all pending approvals
"""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException

from shared.auth import extract_tenant
from shared.logger import get_logger

router = APIRouter(prefix="/approvals", tags=["approvals"])
log = get_logger("approvals-api")


@router.post("/{request_id}/approve", summary="Approve a remediation action")
async def approve_action(
    request_id: str,
    request: Request,
    approved_by: str = "operator",
):
    """
    Approve a pending remediation action.
    The remediation agent polls Redis and will detect this approval.
    """
    redis = request.app.state.redis
    # Use default tenant for now (in production, extract from JWT)
    tenant_id = "default"

    token = await redis.get_approval_token(tenant_id, request_id)
    if not token:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")

    if token.get("status") != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Request already {token.get('status')}"
        )

    success = await redis.resolve_approval_token(
        tenant_id=tenant_id,
        request_id=request_id,
        status="approved",
        approved_by=approved_by,
    )

    if not success:
        raise HTTPException(status_code=410, detail="Approval token expired")

    log.info("action_approved", request_id=request_id, approved_by=approved_by)
    return {
        "message": "Action approved",
        "request_id": request_id,
        "approved_by": approved_by,
        "incident_id": token.get("incident_id"),
    }


@router.post("/{request_id}/reject", summary="Reject a remediation action")
async def reject_action(
    request_id: str,
    request: Request,
    approved_by: str = "operator",
):
    """Reject a pending remediation action. The incident will be escalated."""
    redis = request.app.state.redis
    tenant_id = "default"

    token = await redis.get_approval_token(tenant_id, request_id)
    if not token:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")

    success = await redis.resolve_approval_token(
        tenant_id=tenant_id,
        request_id=request_id,
        status="rejected",
        approved_by=approved_by,
    )

    if not success:
        raise HTTPException(status_code=410, detail="Approval token expired")

    log.info("action_rejected", request_id=request_id, rejected_by=approved_by)
    return {
        "message": "Action rejected — incident will be escalated",
        "request_id": request_id,
        "rejected_by": approved_by,
        "incident_id": token.get("incident_id"),
    }


@router.get("/pending", summary="List pending approval requests")
async def list_pending(request: Request):
    """List all pending approval requests (scans Redis for approval:token:* keys)."""
    redis = request.app.state.redis
    pending = []

    # Scan for all approval tokens
    async for key in redis._redis.scan_iter(match="approval:token:*", count=50):
        val = await redis._redis.get(key)
        if val:
            import json
            token = json.loads(val.decode("utf-8"))
            if token.get("status") == "pending":
                ttl = await redis._redis.ttl(key)
                token["remaining_seconds"] = ttl if ttl > 0 else "no_expiry"
                pending.append(token)

    return {"pending_approvals": pending, "count": len(pending)}
