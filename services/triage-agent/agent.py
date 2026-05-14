import json
from uuid import uuid4
from datetime import datetime

from google import genai
from shared.models.alert import AlertEvent, Severity
from shared.models.incident import IncidentContext, IncidentStatus
from shared.pg_client import PostgresClient
from shared.logger import get_logger
from shared.config import settings
from config import triage_settings
from embeddings import EmbeddingService

log = get_logger("triage-agent")

class TriageAgent:
    def __init__(self, pg_client: PostgresClient):
        self.pg = pg_client
        self.llm = genai.Client(api_key=settings.gemini_api_key)
        self.embedding_service = EmbeddingService()

    async def process_alert(self, alert: AlertEvent) -> IncidentContext | None:
        """
        Runs the full triage lifecycle for a new alert.
        """
        tenant_id = alert.tenant_id
        
        log.info("triage_started", alert_id=alert.alert_id, tenant_id=tenant_id)
        
        # 1. Initialize incident context
        incident = IncidentContext(
            incident_id=str(uuid4()),
            tenant_id=tenant_id,
            alert=alert,
            status=IncidentStatus.TRIAGING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # 2. Get embeddings for search
        alert_text = f"{alert.name} {alert.service} {alert.annotations.summary}"
        embedding = self.embedding_service.get_embedding(alert_text)
        
        # 3. Retrieve context via pgvector
        similar_runbooks = await self.pg.find_similar_runbooks(
            embedding=embedding, 
            service=alert.service,
            tenant_id=tenant_id,
            limit=2
        )
        
        past_incidents = await self.pg.find_similar_incidents(
            embedding=embedding,
            tenant_id=tenant_id,
            limit=3
        )
        
        # 4. Call LLM
        classification = await self._classify_with_llm(alert, similar_runbooks, past_incidents)
        
        if not classification:
            log.error("triage_classification_failed", incident_id=incident.incident_id)
            classification = {
                "severity": "P2", # Fallback default
                "triage_summary": "Failed to classify incident via LLM. Falling back to default P2.",
                "matched_runbook_id": None,
                "confidence": 0.0
            }
            
        # 5. Enrich Context
        incident.severity = Severity(classification.get("severity", "P2"))
        incident.triage_summary = classification.get("triage_summary", "")
        incident.matched_runbook_id = classification.get("matched_runbook_id")
        incident.triage_confidence = classification.get("confidence", 0.0)
        incident.similar_incident_ids = [str(inc["incident_id"]) for inc in past_incidents]
        incident.triaged_at = datetime.utcnow()
        
        # Update status to ready for Diagnosis
        incident.status = IncidentStatus.DIAGNOSING
        
        log.info("triage_completed", 
                 incident_id=incident.incident_id, 
                 severity=incident.severity.value,
                 runbook=incident.matched_runbook_id)
                 
        return incident

    async def _classify_with_llm(self, alert: AlertEvent, runbooks: list, past_incidents: list) -> dict | None:
        
        prompt = f"""
        Alert Event:
        {alert.model_dump_json(indent=2)}
        
        Relevant Runbooks:
        {json.dumps(runbooks, indent=2, default=str)}
        
        Similar Past Incidents:
        {json.dumps(past_incidents, indent=2, default=str)}
        """
        
        try:
            response = await self.llm.aio.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=triage_settings.system_prompt,
                    temperature=triage_settings.agent_temperature,
                    max_output_tokens=500,
                    response_mime_type="application/json",
                )
            )
            
            # Extract JSON block
            raw_text = response.text
            
            # Simple heuristic to extract JSON if Claude wrapped it in markdown
            if "{" in raw_text and "}" in raw_text:
                json_str = raw_text[raw_text.find("{"):raw_text.rfind("}")+1]
                return json.loads(json_str)
            else:
                return json.loads(raw_text)
                
        except Exception as e:
            log.error("llm_call_failed", error=str(e))
            return None
