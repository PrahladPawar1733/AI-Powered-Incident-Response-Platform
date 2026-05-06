"""
Dashboard API — REST endpoints for the frontend dashboard.

Provides read-only access to incidents, evidence, and platform statistics.
All queries are scoped by tenant_id for multi-tenant isolation.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from sqlalchemy import text

from shared.pg_client import PostgresClient
from shared.config import settings
from shared.logger import get_logger
from shared.auth import extract_tenant

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
log = get_logger("dashboard-api")

# Reusable Postgres connection
_pg: PostgresClient | None = None


def _get_pg() -> PostgresClient:
    global _pg
    if _pg is None:
        _pg = PostgresClient(settings.postgres_url)
    return _pg


@router.get("/incidents", summary="List all incidents")
async def list_incidents(
    request: Request,
    tenant_id: str = Depends(extract_tenant),
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    """Get a paginated list of incidents, optionally filtered by status."""
    pg = _get_pg()

    query = """
        SELECT incident_id, tenant_id, status, alert_name, service,
               severity, root_cause, resolution_summary, mttr_seconds,
               created_at, raw_context
        FROM incidents
        WHERE tenant_id = :tenant_id
    """
    params = {"tenant_id": tenant_id}

    if status:
        query += " AND status = :status"
        params["status"] = status

    query += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    async with pg.session() as sess:
        result = await sess.execute(text(query), params)
        rows = result.fetchall()

        # Count total
        count_query = "SELECT count(*) FROM incidents WHERE tenant_id = :tenant_id"
        count_params = {"tenant_id": tenant_id}
        if status:
            count_query += " AND status = :status"
            count_params["status"] = status
        count_result = await sess.execute(text(count_query), count_params)
        total = count_result.scalar()

    incidents = []
    for row in rows:
        incidents.append({
            "incident_id": row[0],
            "tenant_id": row[1],
            "status": row[2],
            "alert_name": row[3],
            "service": row[4],
            "severity": row[5],
            "root_cause": row[6],
            "resolution_summary": row[7],
            "mttr_seconds": row[8],
            "created_at": str(row[9]) if row[9] else None,
        })

    return {"incidents": incidents, "total": total, "limit": limit, "offset": offset}


@router.get("/incidents/{incident_id}", summary="Get incident details")
async def get_incident_detail(
    incident_id: str,
    tenant_id: str = Depends(extract_tenant)
):
    """Get the full incident context including evidence and remediation plan."""
    pg = _get_pg()

    async with pg.session() as sess:
        result = await sess.execute(
            text("""
                SELECT raw_context FROM incidents
                WHERE incident_id = :incident_id AND tenant_id = :tenant_id
            """),
            {"incident_id": incident_id, "tenant_id": tenant_id},
        )
        row = result.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Incident not found")

    context = row[0]
    if isinstance(context, str):
        return json.loads(context)
    return context


@router.get("/stats", summary="Get platform statistics")
async def get_stats(tenant_id: str = Depends(extract_tenant)):
    """Get aggregate stats: total incidents, MTTR, status breakdown."""
    pg = _get_pg()

    async with pg.session() as sess:
        # Status breakdown
        result = await sess.execute(
            text("""
                SELECT status, count(*) as cnt
                FROM incidents WHERE tenant_id = :tid
                GROUP BY status
            """),
            {"tid": tenant_id},
        )
        status_counts = {row[0]: row[1] for row in result}

        # Average MTTR
        mttr_result = await sess.execute(
            text("""
                SELECT AVG(mttr_seconds), MIN(mttr_seconds), MAX(mttr_seconds)
                FROM incidents
                WHERE tenant_id = :tid AND mttr_seconds IS NOT NULL
            """),
            {"tid": tenant_id},
        )
        mttr_row = mttr_result.fetchone()

        # Recent incidents (last 24h)
        recent_result = await sess.execute(
            text("""
                SELECT count(*) FROM incidents
                WHERE tenant_id = :tid AND created_at > NOW() - INTERVAL '24 hours'
            """),
            {"tid": tenant_id},
        )
        recent_count = recent_result.scalar()

    return {
        "total_incidents": sum(status_counts.values()),
        "status_breakdown": status_counts,
        "last_24h": recent_count,
        "mttr": {
            "avg_seconds": float(mttr_row[0]) if mttr_row[0] else None,
            "min_seconds": mttr_row[1],
            "max_seconds": mttr_row[2],
        },
    }
