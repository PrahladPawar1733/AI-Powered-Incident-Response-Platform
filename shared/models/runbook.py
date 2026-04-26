# shared/models/runbook.py
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from shared.models.alert import Severity


class RunbookStep(BaseModel):
    order:       int
    description: str
    command:     str | None = None    # shell command if applicable
    automated:   bool = False         # can the agent do this step?


class Runbook(BaseModel):
    """
    A human-written or auto-generated procedure for handling
    a specific type of incident. Stored as embeddings in pgvector
    so the triage agent can find the most relevant one.
    """
    runbook_id:   str = Field(default_factory=lambda: str(uuid4()))
    title:        str
    description:  str
    services:     list[str]           # which services this applies to
    alert_names:  list[str]           # alert names this handles
    severity:     Severity
    steps:        list[RunbookStep] = Field(default_factory=list)
    tags:         list[str] = Field(default_factory=list)
    created_at:   datetime = Field(default_factory=datetime.utcnow)
    updated_at:   datetime = Field(default_factory=datetime.utcnow)
    # embedding stored in Postgres pgvector, not here


class PastIncident(BaseModel):
    """
    A resolved incident retrieved from pgvector similarity search.
    The triage agent uses these as few-shot examples:
    'Last time we saw this alert, root cause was X, fixed by Y in Z minutes.'
    """
    incident_id:      str
    alert_name:       str
    service:          str
    root_cause:       str
    resolution:       str
    mttr_seconds:     int
    severity:         Severity
    resolved_at:      datetime
    similarity_score: float     # cosine similarity from pgvector query


# Export everything cleanly
__all__ = ["Runbook", "RunbookStep", "PastIncident"]