"""
Diagnosis Agent — Kafka Consumer Worker.

Consumes from alerts.triaged, runs the DiagnosisAgent agentic loop,
and publishes enriched incidents to incidents.active.

P3/P4 incidents are auto-resolved (they don't need expensive LLM diagnosis).
Only P1 and P2 incidents enter the full diagnostic pipeline.
"""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from shared.kafka_utils import KafkaConsumer, KafkaProducer
from shared.pg_client import PostgresClient
from shared.redis_client import init_redis
from shared.models.incident import IncidentContext, IncidentStatus
from shared.models.alert import Severity
from shared.config import settings
from shared.logger import configure_logging, get_logger

from agent import DiagnosisAgent

configure_logging(settings.service_name or "diagnosis-agent")
log = get_logger("diagnosis-agent")


async def run_worker():
    log.info(
        "diagnosis_agent_starting",
        brokers=settings.kafka_bootstrap_servers,
        redis=settings.redis_url,
    )

    # Init connections
    producer = KafkaProducer(settings.kafka_bootstrap_servers)
    pg_client = PostgresClient(settings.postgres_url)
    redis_client = await init_redis(settings.redis_url)

    diagnosis_agent = DiagnosisAgent()

    # Consumer group for scaling: multiple diagnosis-agent instances share the load
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="diagnosis-agent-group",
        topics=[settings.topic_alerts_triaged],
    )

    async def handle_message(payload: dict, headers: dict):
        try:
            # Reconstruct the IncidentContext from the triage agent's output
            incident = IncidentContext(**payload)
            tenant_id = incident.tenant_id

            log.info(
                "incident_received_for_diagnosis",
                incident_id=incident.incident_id,
                tenant_id=tenant_id,
                severity=incident.severity.value if incident.severity else "unknown",
                alert_name=incident.alert.name,
            )

            # ── P3/P4 Auto-Resolution ────────────────────────────────
            # Low-severity incidents skip the expensive LLM diagnosis loop.
            if incident.severity in (Severity.P3, Severity.P4):
                log.info(
                    "auto_resolving_low_severity",
                    incident_id=incident.incident_id,
                    severity=incident.severity.value,
                )
                incident.root_cause = "Low severity — auto-resolved without diagnosis"
                incident.diagnosis_summary = (
                    f"Severity {incident.severity.value} incident auto-resolved. "
                    "No investigation required per severity policy."
                )
                incident.status = IncidentStatus.RESOLVED
                incident.diagnosed_at = datetime.now(timezone.utc)
                incident.resolved_at = datetime.now(timezone.utc)
            else:
                # ── Full Diagnostic Loop ─────────────────────────────
                incident = await diagnosis_agent.diagnose(incident)

            # Serialize for downstream
            incident_data = incident.model_dump(mode="json")

            # 1. Save enriched incident to PostgreSQL
            db_payload = {
                "incident_id": incident.incident_id,
                "tenant_id": tenant_id,
                "status": incident.status.value,
                "alert_name": incident.alert.name,
                "service": incident.alert.service,
                "severity": incident.severity.value if incident.severity else None,
                "root_cause": incident.root_cause,
                "resolution_summary": incident.resolution_summary or None,
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

            # 3. Publish to the next pipeline stage
            key = f"{tenant_id}:{incident.incident_id}"

            if incident.status == IncidentStatus.RESOLVED:
                # P3/P4 auto-resolved — publish directly to incidents.resolved
                producer.publish(
                    topic=settings.topic_incidents_resolved,
                    value=incident_data,
                    key=key,
                    headers={"tenant_id": tenant_id},
                )
            else:
                # P1/P2 diagnosed — publish to incidents.active for remediation
                producer.publish(
                    topic=settings.topic_incidents_active,
                    value=incident_data,
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
                    "agent": "diagnosis-agent",
                    "action": "diagnosis_completed",
                    "details": {
                        "root_cause": incident.root_cause[:200] if incident.root_cause else None,
                        "evidence_count": len(incident.evidence),
                        "affected_services": incident.affected_services,
                        "status": incident.status.value,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                key=key,
                headers={"tenant_id": tenant_id},
            )

            log.info(
                "incident_diagnosis_persisted",
                incident_id=incident.incident_id,
                status=incident.status.value,
                root_cause=incident.root_cause[:80] if incident.root_cause else None,
                evidence_count=len(incident.evidence),
            )

        except Exception as e:
            log.error("message_handler_failed", error=str(e), exc_info=True)
            raise  # Don't commit offset — message will be retried

    # Start the infinite consumer loop
    await consumer.consume_loop(handle_message)


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        log.info("diagnosis_agent_stopped_by_user")
