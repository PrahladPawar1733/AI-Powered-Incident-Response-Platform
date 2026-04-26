# shared/models/alert.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AlertSource(str, Enum):
    """Where did this alert come from?"""
    PROMETHEUS    = "prometheus"
    GRAFANA       = "grafana"
    PAGERDUTY     = "pagerduty"
    DATADOG       = "datadog"
    WEBHOOK       = "webhook"
    MANUAL        = "manual"    # engineer manually triggered


class Severity(str, Enum):
    """
    P1 = production down, revenue impact, all hands
    P2 = degraded, some users affected, on-call responds
    P3 = warning, no user impact yet, business hours
    P4 = informational, no action needed
    """
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class AlertStatus(str, Enum):
    FIRING    = "firing"      # active alert
    RESOLVED  = "resolved"    # alert condition gone
    SILENCED  = "silenced"    # intentionally suppressed


class AlertLabel(BaseModel):
    """Key-value labels from Prometheus/Grafana — service=payment, env=prod"""
    name:  str
    value: str


class AlertAnnotation(BaseModel):
    """Human-readable metadata attached to the alert"""
    summary:     str
    description: str = ""
    runbook_url: str = ""     # link to wiki/Confluence runbook if exists


class AlertEvent(BaseModel):
    """
    The canonical alert object. Everything entering the system
    gets normalized into this shape regardless of source.

    Why normalize? Prometheus sends Alertmanager JSON.
    PagerDuty sends its own format. Datadog sends another.
    By normalizing at the ingestor boundary, every downstream
    service speaks one language.
    """
    alert_id:    str = Field(default_factory=lambda: str(uuid4()))
    tenant_id:   str = "default"             # set by JWT middleware, NEVER trusted from client
    source:      AlertSource
    status:      AlertStatus = AlertStatus.FIRING
    severity:    Severity | None = None      # None until triage sets it
    name:        str                         # "HighErrorRate", "PodCrashLooping"
    service:     str                         # "payment-service", "order-api"
    environment: str = "production"
    labels:      dict[str, str] = Field(default_factory=dict)
    annotations: AlertAnnotation | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)  # original source data
    fired_at:    datetime = Field(default_factory=datetime.utcnow)
    received_at: datetime = Field(default_factory=datetime.utcnow)
    trace_id:    str | None = None

    def fingerprint(self) -> str:
        """
        Stable identifier for deduplication.
        Same alert from same service = same fingerprint.
        Used by Redis to prevent processing the same alert twice
        during a flapping incident.
        """
        return f"{self.tenant_id}:{self.name}:{self.service}:{self.environment}"