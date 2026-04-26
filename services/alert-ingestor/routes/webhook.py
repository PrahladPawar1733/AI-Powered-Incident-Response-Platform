from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from shared.auth import extract_tenant
from normalizers import normalize_webhook
from publisher import process_alert

router = APIRouter()

@router.post("/webhook")
async def receive_generic_webhook(
    request: Request,
    tenant_id: str = Depends(extract_tenant)
):
    """
    Generic webhook receiver for other services that send a single JSON alert.
    Tries its best to parse the format.
    """
    try:
        raw_payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    alert = normalize_webhook(raw_payload)
    alert.tenant_id = tenant_id  # Enforce multi-tenancy at boundary
        
    redis = request.app.state.redis
    kafka = request.app.state.kafka
    
    result = await process_alert(alert, redis, kafka)
            
    if result["status"] == "duplicate":
        return JSONResponse(
            status_code=200, 
            content={
                "message": "Duplicate webhook alert skipped",
                "fingerprint": result["fingerprint"]
            }
        )
        
    return JSONResponse(
        status_code=202,
        content={
            "message": "Webhook alert processed",
            "alert_id": result["alert_id"]
        }
    )
