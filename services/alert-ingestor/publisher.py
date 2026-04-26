import json
from uuid import uuid4
from datetime import datetime
from shared.models.alert import AlertEvent
from shared.kafka_utils import KafkaProducer
from shared.redis_client import RedisClient
from shared.config import settings
from shared.logger import get_logger

log = get_logger("alert-ingestor")

async def process_alert(
    alert: AlertEvent, 
    redis: RedisClient, 
    kafka: KafkaProducer
) -> dict:
    """
    Process a single normalized alert:
    1. Check Redis for deduplication (tenant isolated)
    2. If duplicate, skip and log
    3. If new, mark seen in Redis
    4. Publish to alerts.raw
    5. Publish to audit.events
    """
    tenant_id = alert.tenant_id
    fingerprint = alert.fingerprint()
    
    # 1. Tenant-scoped deduplication
    if await redis.is_duplicate(tenant_id, fingerprint):
        log.info("alert_duplicate", tenant_id=tenant_id, fingerprint=fingerprint)
        return {"status": "duplicate", "fingerprint": fingerprint}
        
    # 2. Mark as seen
    await redis.mark_seen(tenant_id, fingerprint)
    
    # 3. Publish alert event
    # The key is tenant_id:alert_id to ensure partition isolation and ordering
    key = f"{tenant_id}:{alert.alert_id}"
    kafka.publish(
        topic=settings.topic_alerts_raw,
        value=alert.model_dump(),
        key=key,
        headers={"tenant_id": tenant_id}
    )
    
    log.info("alert_published", 
             tenant_id=tenant_id, 
             alert_id=alert.alert_id, 
             fingerprint=fingerprint,
             topic=settings.topic_alerts_raw)
             
    # 4. Emit audit event
    audit_event = {
        "event_id": str(uuid4()),
        "incident_id": None,  # No incident ID yet
        "agent": "alert-ingestor",
        "action": "alert_received",
        "details": {
            "alert_id": alert.alert_id,
            "alert_name": alert.name,
            "service": alert.service,
            "source": alert.source.value,
            "deduplicated": False
        },
        "trace_id": alert.trace_id,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    
    kafka.publish(
        topic=settings.topic_audit_events,
        value=audit_event,
        key=key,
        headers={"tenant_id": tenant_id}
    )
    
    return {"status": "processed", "alert_id": alert.alert_id}
