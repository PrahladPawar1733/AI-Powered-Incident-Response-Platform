"""
Remediation Agent — Kafka Consumer Worker.

Consumes from incidents.active (diagnosed incidents), runs the
RemediationAgent to generate and execute a remediation plan,
and publishes resolved/escalated incidents downstream.
"""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from shared.kafka_utils import KafkaConsumer, KafkaProducer
from shared.pg_client import PostgresClient
from shared.redis_client import init_redis
from shared.models.incident import IncidentContext, IncidentStatus
from shared.config import settings
from shared.logger import configure_logging, get_logger

from agent import RemediationAgent

configure_logging(settings.service_name or "remediation-agent")
log = get_logger("remediation-agent")


async def run_worker():
    log.info(
        "remediation_agent_starting",
        brokers=settings.kafka_bootstrap_servers,
    )

    # Init connections
    producer = KafkaProducer(settings.kafka_bootstrap_servers)
    pg_client = PostgresClient(settings.postgres_url)
    redis_client = await init_redis(settings.redis_url)

    remediation_agent = RemediationAgent(redis=redis_client)

    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="remediation-agent-group",
        topics=[settings.topic_incidents_active],
    )

    async def handle_message(payload: dict, headers: dict):
        try:
            incident = IncidentContext(**payload)
            tenant_id = incident.tenant_id

            log.info(
                "incident_received_for_remediation",
                incident_id=incident.incident_id,
                tenant_id=tenant_id,
                root_cause=incident.root_cause[:80] if incident.root_cause else "none",
            )

            # Run the remediation lifecycle
            incident = await remediation_agent.remediate(incident)

            # Serialize for persistence
            incident_data = incident.model_dump(mode="json")

            # 1. Save to PostgreSQL
            db_payload = {
                "incident_id": incident.incident_id,
                "tenant_id": tenant_id,
                "status": incident.status.value,
                "alert_name": incident.alert.name,
                "service": incident.alert.service,
                "severity": incident.severity.value if incident.severity else None,
                "root_cause": incident.root_cause,
                "resolution_summary": incident.resolution_summary,
                "mttr_seconds": incident.mttr_seconds(),
                "trace_id": incident.trace_id,
                "raw_context": json.dumps(incident_data),
                "created_at": incident.created_at,
            }
            await pg_client.save_incident(db_payload)

            # 2. Update Redis cache
            await redis_client.set_incident_status(
                tenant_id, incident.incident_id, incident.status.value
            )
            await redis_client.cache_incident(
                tenant_id, incident.incident_id, incident_data
            )

            # 3. Publish to the appropriate topic
            key = f"{tenant_id}:{incident.incident_id}"

            if incident.status == IncidentStatus.RESOLVED:
                producer.publish(
                    topic=settings.topic_incidents_resolved,
                    value=incident_data,
                    key=key,
                    headers={"tenant_id": tenant_id},
                )
            else:
                # Escalated — could go to a separate topic or notification
                producer.publish(
                    topic=settings.topic_audit_events,
                    value={
                        "event_id": str(uuid4()),
                        "incident_id": incident.incident_id,
                        "tenant_id": tenant_id,
                        "agent": "remediation-agent",
                        "action": "incident_escalated",
                        "details": {
                            "status": incident.status.value,
                            "resolution_summary": incident.resolution_summary,
                        },
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    key=key,
                    headers={"tenant_id": tenant_id},
                )

            # 4. Audit event
            producer.publish(
                topic=settings.topic_audit_events,
                value={
                    "event_id": str(uuid4()),
                    "incident_id": incident.incident_id,
                    "tenant_id": tenant_id,
                    "agent": "remediation-agent",
                    "action": "remediation_completed",
                    "details": {
                        "status": incident.status.value,
                        "actions_count": len(incident.remediation_plan),
                        "mttr_seconds": incident.mttr_seconds(),
                        "resolution": incident.resolution_summary[:200] if incident.resolution_summary else None,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                key=key,
                headers={"tenant_id": tenant_id},
            )

            log.info(
                "incident_remediation_persisted",
                incident_id=incident.incident_id,
                status=incident.status.value,
                mttr=incident.mttr_seconds(),
            )

        except Exception as e:
            log.error("message_handler_failed", error=str(e), exc_info=True)
            raise

    await consumer.consume_loop(handle_message)


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        log.info("remediation_agent_stopped_by_user")
