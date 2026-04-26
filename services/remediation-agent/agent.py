"""
Remediation Agent — Core Logic.

Flow:
1. Receive diagnosed IncidentContext (has root_cause, evidence)
2. Call Claude to generate a remediation plan (list of Actions)
3. For each Action:
   - LOW risk → execute immediately via remediation-mcp
   - MEDIUM risk → request approval, wait up to 5 min, then execute
   - HIGH risk → request approval, wait indefinitely, then execute
4. Compile all results into resolution_summary
5. Set status = resolved
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import anthropic

from shared.models.incident import (
    IncidentContext, IncidentStatus, Action, RiskLevel,
)
from shared.redis_client import RedisClient
from shared.config import settings
from shared.logger import get_logger

from config import REMEDIATION_SYSTEM_PROMPT, AGENT_TEMPERATURE, MAX_TOKENS
from approval import request_approval, wait_for_approval

log = get_logger("remediation-agent")


def _get_tool_function(tool_fn: str):
    """Import and return a remediation-mcp tool function."""
    import sys
    import os
    import importlib.util

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    file_path = os.path.join(base_dir, "services", "mcp-servers", "remediation", "main.py")

    module_name = "_mcp_remediation"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    module = sys.modules[module_name]
    func = getattr(module, tool_fn, None)
    if func is None:
        raise AttributeError(f"Tool '{tool_fn}' not found in remediation-mcp")
    return func


class RemediationAgent:
    def __init__(self, redis: RedisClient):
        self.llm = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.redis = redis

    async def remediate(self, incident: IncidentContext) -> IncidentContext:
        """Run the full remediation lifecycle for a diagnosed incident."""
        log.info(
            "remediation_started",
            incident_id=incident.incident_id,
            root_cause=incident.root_cause[:80] if incident.root_cause else "unknown",
        )

        incident.status = IncidentStatus.REMEDIATING

        # Step 1: Generate remediation plan via LLM
        actions = await self._generate_plan(incident)
        if not actions:
            log.warning("no_remediation_plan", incident_id=incident.incident_id)
            incident.remediation_summary = "No remediation actions generated."
            incident.status = IncidentStatus.ESCALATED
            return incident

        incident.remediation_plan = actions

        # Step 2: Execute each action based on risk level
        results = []
        all_approved = True

        for action in actions:
            log.info(
                "executing_action",
                incident_id=incident.incident_id,
                tool=action.tool_fn,
                risk=action.risk_level.value,
            )

            if action.risk_level == RiskLevel.LOW:
                # Auto-execute
                result = await self._execute_action(incident, action)
                results.append(result)

            elif action.risk_level in (RiskLevel.MED, RiskLevel.HIGH):
                # Request human approval
                incident.status = IncidentStatus.AWAITING_APPROVAL

                request_id = await request_approval(
                    redis=self.redis,
                    tenant_id=incident.tenant_id,
                    incident_id=incident.incident_id,
                    action_summary=f"{action.tool_fn}({action.parameters}) — {action.reasoning}",
                    risk_level=action.risk_level.value,
                )

                # Wait for approval
                approval_status = await wait_for_approval(
                    redis=self.redis,
                    tenant_id=incident.tenant_id,
                    request_id=request_id,
                    risk_level=action.risk_level.value,
                )

                if approval_status == "approved":
                    incident.human_approved = True
                    result = await self._execute_action(incident, action)
                    results.append(result)
                elif approval_status == "rejected":
                    all_approved = False
                    action.result = "REJECTED by human operator"
                    results.append(f"REJECTED: {action.tool_fn}")
                    log.info("action_rejected", tool=action.tool_fn)
                    break  # Stop executing remaining actions
                else:  # expired
                    all_approved = False
                    action.result = "EXPIRED — no human response within timeout"
                    results.append(f"EXPIRED: {action.tool_fn}")
                    log.warning("action_expired", tool=action.tool_fn)

        # Step 3: Set final status
        if all_approved and all("ERROR" not in r for r in results):
            incident.status = IncidentStatus.RESOLVED
            incident.resolved_at = datetime.now(timezone.utc)
            incident.resolution_summary = (
                f"Remediation completed successfully. "
                f"Actions executed: {'; '.join(results)}"
            )
        elif not all_approved:
            incident.status = IncidentStatus.ESCALATED
            incident.resolution_summary = (
                f"Remediation escalated — action was rejected or expired. "
                f"Results: {'; '.join(results)}"
            )
        else:
            incident.status = IncidentStatus.ESCALATED
            incident.resolution_summary = (
                f"Remediation had errors. Results: {'; '.join(results)}"
            )

        incident.updated_at = datetime.now(timezone.utc)
        log.info(
            "remediation_completed",
            incident_id=incident.incident_id,
            status=incident.status.value,
            actions_count=len(actions),
            results=results,
        )
        return incident

    async def _generate_plan(self, incident: IncidentContext) -> list[Action]:
        """Call Claude to generate a remediation plan from the diagnosis."""
        prompt = f"""## Incident to Remediate

**Incident ID:** {incident.incident_id}
**Service:** {incident.alert.service}
**Severity:** {incident.severity.value if incident.severity else 'unknown'}
**Environment:** {incident.alert.environment}

### Root Cause
{incident.root_cause}

### Diagnosis Summary
{incident.diagnosis_summary}

### Evidence Collected
{chr(10).join(f'- [{e.source}/{e.tool_name}]: {e.content[:200]}' for e in incident.evidence[:5])}

### Triage Summary
{incident.triage_summary}

Generate a remediation plan. Remember: the tenant_id is "{incident.tenant_id}" and the namespace is "{incident.alert.environment}".
"""

        try:
            response = await self.llm.messages.create(
                model=settings.anthropic_model,
                system=REMEDIATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                temperature=AGENT_TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )

            raw_text = response.content[0].text
            plan_data = self._parse_plan(raw_text)

            actions = []
            for item in plan_data:
                actions.append(Action(
                    action_id=str(uuid4()),
                    tool="remediation-mcp",
                    tool_fn=item["tool_fn"],
                    parameters=item.get("parameters", {}),
                    risk_level=RiskLevel(item.get("risk_level", "medium")),
                    reasoning=item.get("reasoning", ""),
                ))

            log.info("plan_generated", actions_count=len(actions))
            return actions

        except Exception as e:
            log.error("plan_generation_failed", error=str(e))
            # Fallback: generate a safe default action
            return [Action(
                action_id=str(uuid4()),
                tool="remediation-mcp",
                tool_fn="restart_pod",
                parameters={
                    "namespace": incident.alert.environment or "default",
                    "pod_name": f"{incident.alert.service}-0",
                },
                risk_level=RiskLevel.LOW,
                reasoning=f"LLM plan generation failed ({str(e)[:50]}). Attempting safe pod restart as fallback.",
            )]

    def _parse_plan(self, text: str) -> list[dict]:
        """Parse the JSON remediation plan from Claude's response."""
        try:
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
            elif "[" in text and "]" in text:
                json_str = text[text.find("["):text.rfind("]") + 1]
            else:
                json_str = text

            return json.loads(json_str)
        except (json.JSONDecodeError, IndexError) as e:
            log.error("plan_parse_failed", error=str(e), text=text[:200])
            return []

    async def _execute_action(self, incident: IncidentContext, action: Action) -> str:
        """Execute a single remediation action via the remediation-mcp tool."""
        try:
            # Add tenant_id to parameters
            params = {**action.parameters, "tenant_id": incident.tenant_id}
            func = _get_tool_function(action.tool_fn)
            result = await func(**params)

            action.executed = True
            action.result = str(result)
            action.executed_at = datetime.now(timezone.utc)

            log.info(
                "action_executed",
                tool=action.tool_fn,
                result=str(result)[:100],
            )
            return f"{action.tool_fn}: {str(result)[:100]}"
        except Exception as e:
            error_msg = f"ERROR executing {action.tool_fn}: {str(e)}"
            action.result = error_msg
            log.error("action_execution_failed", tool=action.tool_fn, error=str(e))
            return error_msg
