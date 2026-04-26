from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from shared.auth import extract_tenant
from normalizers import normalize_manual
from publisher import process_alert

router = APIRouter()

@router.post("/manual")
async def receive_manual_alert(
    request: Request,
    tenant_id: str = Depends(extract_tenant)
):
    """
    Receive a manually triggered alert (e.g., via curl).
    Expects a single flat JSON object.
    """
    try:
        raw_payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not raw_payload.get("name") or not raw_payload.get("service"):
        raise HTTPException(status_code=400, detail="name and service are required")

    alert = normalize_manual(raw_payload)
    alert.tenant_id = tenant_id  # Enforce multi-tenancy at boundary
        
    redis = request.app.state.redis
    kafka = request.app.state.kafka
    
    result = await process_alert(alert, redis, kafka)
            
    if result["status"] == "duplicate":
        return JSONResponse(
            status_code=200, 
            content={
                "message": "Duplicate manual alert skipped",
                "fingerprint": result["fingerprint"]
            }
        )
        
    return JSONResponse(
        status_code=202,
        content={
            "message": "Manual alert processed",
            "alert_id": result["alert_id"]
        }
    )
