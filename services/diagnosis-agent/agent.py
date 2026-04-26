"""
Diagnosis Agent — Multi-Turn Agentic Loop.

This is the most sophisticated agent in the platform. Unlike the Triage Agent
which makes a single LLM call, the Diagnosis Agent runs a loop where Claude
autonomously decides which tools to call, examines results, and keeps
investigating until it determines the root cause.

The loop pattern:
1. Send incident context + available tools to Claude
2. Claude responds with tool_use blocks (e.g. "call get_pod_status")
3. We execute those tools against the MCP servers
4. We send the results back to Claude
5. Claude either requests more tools or outputs the final diagnosis
6. Repeat until Claude returns end_turn or we hit MAX_TOOL_CALLS
"""
from __future__ import annotations

import json
from datetime import datetime

import anthropic

from shared.models.incident import IncidentContext, IncidentStatus, Evidence
from shared.config import settings
from shared.logger import get_logger

from config import (
    DIAGNOSIS_SYSTEM_PROMPT,
    AGENT_TEMPERATURE,
    MAX_TOOL_CALLS,
    MAX_TOKENS,
)
from tools import DIAGNOSTIC_TOOLS, TOOL_SOURCE, execute_tool

log = get_logger("diagnosis-agent")


class DiagnosisAgent:
    def __init__(self):
        self.llm = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def diagnose(self, incident: IncidentContext) -> IncidentContext:
        """
        Run the full diagnosis lifecycle for a triaged incident.

        This is where the magic happens — Claude acts as an autonomous
        investigator, calling diagnostic tools and reasoning about the
        results until it can determine the root cause.
        """
        log.info(
            "diagnosis_started",
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            alert_name=incident.alert.name,
            service=incident.alert.service,
            severity=incident.severity.value if incident.severity else "unknown",
        )

        incident.status = IncidentStatus.DIAGNOSING

        # Build the initial prompt with all context from triage
        user_prompt = self._build_prompt(incident)

        # The conversation history for the agentic loop
        messages = [{"role": "user", "content": user_prompt}]

        tool_call_count = 0

        try:
            # ── AGENTIC LOOP ──────────────────────────────────────────
            while tool_call_count < MAX_TOOL_CALLS:
                log.info(
                    "llm_call",
                    incident_id=incident.incident_id,
                    loop_iteration=tool_call_count,
                )

                response = await self.llm.messages.create(
                    model=settings.anthropic_model,
                    system=DIAGNOSIS_SYSTEM_PROMPT,
                    messages=messages,
                    tools=DIAGNOSTIC_TOOLS,
                    temperature=AGENT_TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )

                # Check stop reason
                if response.stop_reason == "end_turn":
                    # Claude is done — extract the final diagnosis
                    final_text = self._extract_text(response)
                    diagnosis = self._parse_diagnosis(final_text)
                    incident = self._apply_diagnosis(incident, diagnosis)
                    log.info(
                        "diagnosis_completed",
                        incident_id=incident.incident_id,
                        root_cause=incident.root_cause[:100] if incident.root_cause else "none",
                        evidence_count=len(incident.evidence),
                        tool_calls=tool_call_count,
                    )
                    return incident

                elif response.stop_reason == "tool_use":
                    # Claude wants to call tools — execute them
                    # First, add Claude's response (with tool_use blocks) to messages
                    messages.append({"role": "assistant", "content": response.content})

                    # Process each tool_use block
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tool_call_count += 1
                            tool_name = block.name
                            tool_input = block.input

                            log.info(
                                "tool_call",
                                incident_id=incident.incident_id,
                                tool=tool_name,
                                input_keys=list(tool_input.keys()),
                                call_number=tool_call_count,
                            )

                            # Execute the tool against the MCP server
                            result = await execute_tool(tool_name, tool_input)

                            # Store as evidence on the incident
                            source = TOOL_SOURCE.get(tool_name, "unknown")
                            incident.add_evidence(
                                source=source,
                                tool=tool_name,
                                content=result[:2000],  # Cap evidence size
                                relevance=f"Called by diagnosis agent during investigation (call #{tool_call_count})",
                            )

                            # Add tool result for Claude
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result[:4000],  # Cap for token limits
                            })

                    # Send all tool results back to Claude
                    messages.append({"role": "user", "content": tool_results})

                else:
                    # Unexpected stop reason — extract whatever we can
                    log.warning(
                        "unexpected_stop_reason",
                        stop_reason=response.stop_reason,
                        incident_id=incident.incident_id,
                    )
                    final_text = self._extract_text(response)
                    diagnosis = self._parse_diagnosis(final_text)
                    incident = self._apply_diagnosis(incident, diagnosis)
                    return incident

            # ── MAX TOOL CALLS REACHED ────────────────────────────────
            log.warning(
                "max_tool_calls_reached",
                incident_id=incident.incident_id,
                tool_calls=tool_call_count,
            )
            # Ask Claude for a final verdict with what it has
            messages.append({
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool calls. "
                    "Based on the evidence collected so far, provide your best "
                    "diagnosis as JSON now."
                ),
            })
            response = await self.llm.messages.create(
                model=settings.anthropic_model,
                system=DIAGNOSIS_SYSTEM_PROMPT,
                messages=messages,
                temperature=AGENT_TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
            final_text = self._extract_text(response)
            diagnosis = self._parse_diagnosis(final_text)
            incident = self._apply_diagnosis(incident, diagnosis)
            return incident

        except Exception as e:
            log.error(
                "diagnosis_failed",
                incident_id=incident.incident_id,
                error=str(e),
                tool_calls=tool_call_count,
            )
            # Graceful fallback — set what we can
            incident.root_cause = f"Diagnosis failed: {str(e)}"
            incident.diagnosis_summary = (
                f"The diagnosis agent encountered an error after {tool_call_count} "
                f"tool calls: {str(e)}. Manual investigation required."
            )
            incident.status = IncidentStatus.REMEDIATING
            incident.diagnosed_at = datetime.utcnow()
            return incident

    def _build_prompt(self, incident: IncidentContext) -> str:
        """Build the initial user prompt from the incident context."""
        alert = incident.alert

        prompt = f"""## Incident to Diagnose

**Incident ID:** {incident.incident_id}
**Tenant ID:** {incident.tenant_id}
**Severity:** {incident.severity.value if incident.severity else 'unknown'}
**Status:** {incident.status.value}

### Alert Details
- **Alert Name:** {alert.name}
- **Service:** {alert.service}
- **Environment:** {alert.environment}
- **Summary:** {alert.annotations.summary}
- **Description:** {alert.annotations.description}

### Triage Results
- **Triage Summary:** {incident.triage_summary}
- **Confidence:** {incident.triage_confidence}
- **Matched Runbook:** {incident.matched_runbook_id or 'None'}
- **Similar Past Incidents:** {', '.join(incident.similar_incident_ids) or 'None'}

### Investigation Context
- The service runs in namespace "{alert.environment}" (use "default" if not specified)
- The tenant_id for all tool calls is: "{incident.tenant_id}"
- The database name is: "incident_db"

Please investigate this incident using the available diagnostic tools and determine the root cause.
"""
        return prompt

    def _extract_text(self, response) -> str:
        """Extract text content from Claude's response."""
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    def _parse_diagnosis(self, text: str) -> dict:
        """Parse the JSON diagnosis from Claude's response."""
        try:
            # Try to extract JSON if wrapped in markdown code block
            if "```json" in text:
                json_str = text.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "```" in text:
                json_str = text.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "{" in text and "}" in text:
                json_str = text[text.find("{"):text.rfind("}") + 1]
                return json.loads(json_str)
            else:
                return json.loads(text)
        except (json.JSONDecodeError, IndexError) as e:
            log.error("diagnosis_parse_failed", error=str(e), raw_text=text[:200])
            return {
                "root_cause": text[:200] if text else "Unable to parse diagnosis",
                "affected_services": [],
                "affected_components": [],
                "diagnosis_summary": text or "LLM returned unparseable response",
                "confidence": 0.3,
            }

    def _apply_diagnosis(self, incident: IncidentContext, diagnosis: dict) -> IncidentContext:
        """Apply the parsed diagnosis to the incident context."""
        incident.root_cause = diagnosis.get("root_cause", "Unknown")
        incident.affected_services = diagnosis.get("affected_services", [incident.alert.service])
        incident.affected_components = diagnosis.get("affected_components", [])
        incident.diagnosis_summary = diagnosis.get("diagnosis_summary", "")
        incident.diagnosed_at = datetime.utcnow()
        incident.status = IncidentStatus.REMEDIATING
        incident.updated_at = datetime.utcnow()
        return incident
