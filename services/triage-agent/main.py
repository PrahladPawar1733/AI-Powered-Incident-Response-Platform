import asyncio
import json
from datetime import datetime
from uuid import uuid4

from shared.kafka_utils import KafkaConsumer, KafkaProducer
from shared.pg_client import PostgresClient
from shared.redis_client import init_redis
from shared.models.alert import AlertEvent
from shared.config import settings
from shared.logger import configure_logging, get_logger
from shared.slack_notifier import notify_incident_triaged
from agent import TriageAgent

configure_logging(settings.service_name or "triage-agent")
log = get_logger("triage-agent")

async def run_worker():
    log.info("triage_agent_starting", 
             brokers=settings.kafka_bootstrap_servers,
             redis=settings.redis_url)
    
    # Init Connections
    producer = KafkaProducer(settings.kafka_bootstrap_servers)
    pg_client = PostgresClient(settings.postgres_url)
    redis_client = await init_redis(settings.redis_url)
    
    triage_agent = TriageAgent(pg_client)
    
    # We use a consumer group so we can run multiple instances of triage-agent in parallel
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="triage-agent-group",
        topics=[settings.topic_alerts_raw]
    )

    async def handle_message(payload: dict, headers: dict):
        try:
            alert = AlertEvent(**payload)
            tenant_id = alert.tenant_id
            
            # Run LLM Agent
            incident = await triage_agent.process_alert(alert)
            if not incident:
                return
            
            incident_data = incident.model_dump(mode='json')
            
            # 1. Save to Database
            # To match the SQL schema precisely:
            db_payload = {
                "incident_id": incident.incident_id,
                "tenant_id": tenant_id,
                "status": incident.status.value,
                "alert_name": alert.name,
                "service": alert.service,
                "severity": incident.severity.value,
                "root_cause": None,
                "resolution_summary": None,
                "mttr_seconds": None,
                "trace_id": incident.trace_id,
                "raw_context": json.dumps(incident_data),
                "created_at": incident.created_at
            }
            await pg_client.save_incident(db_payload)
            
            # 2. Cache full status in Redis for dashboard
            await redis_client.set_incident_status(tenant_id, incident.incident_id, incident.status.value)
            await redis_client.cache_incident(tenant_id, incident.incident_id, incident_data)
            
            # 3. Publish down the pipeline to diagnosis
            key = f"{tenant_id}:{incident.incident_id}"
            producer.publish(
                topic=settings.topic_alerts_triaged,
                value=incident_data,
                key=key,
                headers={"tenant_id": tenant_id}
            )
            
            # 4. Write audit event
            producer.publish(
                topic=settings.topic_audit_events,
                value={
                    "event_id": str(uuid4()),
                    "incident_id": incident.incident_id,
                    "tenant_id": tenant_id,
                    "agent": "triage-agent",
                    "action": "triage_completed",
                    "details": {
                        "severity": incident.severity.value,
                        "confidence": incident.triage_confidence,
                        "matched_runbook": incident.matched_runbook_id
                    },
                    "created_at": datetime.utcnow().isoformat() + "Z"
                },
                key=key,
                headers={"tenant_id": tenant_id}
            )
            
            # 5. Send Slack notification (per-tenant credentials)
            try:
                await notify_incident_triaged(
                    tenant_id=tenant_id,
                    incident_id=incident.incident_id,
                    alert_name=alert.name,
                    service=alert.service,
                    severity=incident.severity.value,
                    triage_summary=incident.triage_summary,
                    confidence=incident.triage_confidence,
                    matched_runbook=incident.matched_runbook_id,
                )
            except Exception as slack_err:
                log.warning("slack_notification_failed", error=str(slack_err))
            
            log.info("incident_fully_triaged_and_persisted", incident_id=incident.incident_id)
            
        except Exception as e:
            log.error("message_handler_failed", error=str(e), exc_info=True)
            raise  # Reraise so Kafka offset is NOT committed!

    # Start infinity loop
    await consumer.consume_loop(handle_message)

if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        log.info("triage_agent_stopped_by_user")
