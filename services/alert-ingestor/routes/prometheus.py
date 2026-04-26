from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from shared.auth import extract_tenant
from normalizers import normalize_prometheus
from publisher import process_alert

router = APIRouter()

@router.post("/prometheus")
async def receive_prometheus_alert(
    request: Request,
    tenant_id: str = Depends(extract_tenant)
):
    """
    Receive alerts from Prometheus Alertmanager.
    Alertmanager batches alerts, so we process each one.
    """
    try:
        raw_payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    alerts = normalize_prometheus(raw_payload)
    
    if not alerts:
        return JSONResponse(status_code=400, content={"message": "No alerts found in payload"})
        
    redis = request.app.state.redis
    kafka = request.app.state.kafka
    
    processed = 0
    skipped = 0
    
    for alert in alerts:
        alert.tenant_id = tenant_id  # Enforce multi-tenancy at boundary
        result = await process_alert(alert, redis, kafka)
        if result["status"] == "duplicate":
            skipped += 1
        else:
            processed += 1
            
    return JSONResponse(
        status_code=202,
        content={
            "message": "Prometheus alerts processed",
            "processed": processed,
            "skipped": skipped,
            "total": len(alerts)
        }
    )
