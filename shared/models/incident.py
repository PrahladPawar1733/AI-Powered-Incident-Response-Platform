# shared/models/incident.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from shared.models.alert import AlertEvent, Severity


class IncidentStatus(str, Enum):
    TRIAGING     = "triaging"      # triage agent working
    DIAGNOSING   = "diagnosing"    # diagnosis agent working
    REMEDIATING  = "remediating"   # remediation agent working
    AWAITING_APPROVAL = "awaiting_approval"  # human gate
    RESOLVED     = "resolved"      # fixed
    ESCALATED    = "escalated"     # handed to human, agent gave up


class RiskLevel(str, Enum):
    """
    Determines whether remediation needs human approval.
    LOW  → auto-execute (restart pod, clear cache)
    MED  → Slack approval with 5-min timeout
    HIGH → Slack + PagerDuty page, no timeout
    """
    LOW  = "low"
    MED  = "medium"
    HIGH = "high"


class Evidence(BaseModel):
    """
    A piece of diagnostic data the diagnosis agent collected.
    Each MCP tool call that returns something useful becomes an Evidence.
    Stored so humans can audit exactly what the agent saw.
    """
    source:       str           # "k8s-mcp", "logs-mcp", "metrics-mcp"
    tool_name:    str           # "get_pod_logs", "search_logs"
    content:      str           # the actual data (log lines, metrics, etc)
    relevance:    str           # agent's explanation of why this matters
    collected_at: datetime = Field(default_factory=datetime.utcnow)


class Action(BaseModel):
    """
    A single remediation step the remediation agent plans.
    Stored before execution so humans can review the full plan.
    """
    action_id:   str = Field(default_factory=lambda: str(uuid4()))
    tool:        str            # "remediation-mcp"
    tool_fn:     str            # "restart_pod"
    parameters:  dict[str, Any] # {"namespace": "prod", "pod": "payment-xyz"}
    risk_level:  RiskLevel
    reasoning:   str            # why the agent thinks this will help
    executed:    bool = False
    result:      str | None = None
    executed_at: datetime | None = None


class IncidentContext(BaseModel):
    """
    The central object that flows through the entire pipeline.
    Published to Kafka at each stage — enriched as it moves
    through triage → diagnosis → remediation.

    Think of it as the incident's complete memory:
    every agent reads it, adds to it, passes it on.
    """
    incident_id:   str = Field(default_factory=lambda: str(uuid4()))
    tenant_id:     str = "default"               # forwarded from AlertEvent, never changed
    status:        IncidentStatus = IncidentStatus.TRIAGING
    alert:         AlertEvent

    # ── Set by Triage Agent ──────────────────────────────────────────
    severity:            Severity | None = None
    triage_summary:      str = ""
    matched_runbook_id:  str | None = None
    similar_incident_ids: list[str] = Field(default_factory=list)
    triage_confidence:   float = 0.0    # 0.0–1.0, how sure triage is
    triaged_at:          datetime | None = None

    # ── Set by Diagnosis Agent ───────────────────────────────────────
    root_cause:          str = ""
    affected_services:   list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    evidence:            list[Evidence] = Field(default_factory=list)
    diagnosis_summary:   str = ""
    diagnosed_at:        datetime | None = None

    # ── Set by Remediation Agent ─────────────────────────────────────
    remediation_plan:    list[Action] = Field(default_factory=list)
    remediation_summary: str = ""
    human_approved:      bool = False
    approved_by:         str | None = None    # Slack user who approved
    resolved_at:         datetime | None = None
    resolution_summary:  str = ""

    # ── Metadata ─────────────────────────────────────────────────────
    created_at:  datetime = Field(default_factory=datetime.utcnow)
    updated_at:  datetime = Field(default_factory=datetime.utcnow)
    trace_id:    str | None = None

    def mttr_seconds(self) -> int | None:
        """Mean Time To Resolve — the KPI you quote in interviews."""
        if self.resolved_at:
            return int((self.resolved_at - self.created_at).total_seconds())
        return None

    def add_evidence(self, source: str, tool: str, content: str, relevance: str) -> None:
        self.evidence.append(Evidence(
            source=source, tool_name=tool,
            content=content, relevance=relevance
        ))
        self.updated_at = datetime.utcnow()