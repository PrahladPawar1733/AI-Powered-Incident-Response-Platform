# Phase 1 — Foundation: Complete Learning Guide

> **What Phase 1 does:** Sets up the entire monorepo structure, all shared Python models, infrastructure clients (Redis, Postgres, Kafka), the Docker Compose stack (Kafka, Redis, Postgres, Prometheus, Grafana), the database schema, and project configuration. After Phase 1, every future service can `from shared.models.alert import AlertEvent` and just start building.

---

## Table of Contents

1. [Project Structure — What Was Created and Why](#1-project-structure)
2. [pyproject.toml — Making `shared/` a Reusable Package](#2-pyprojecttoml)
3. [.env and .env.example — Configuration Without Hardcoding](#3-env-files)
4. [shared/models/alert.py — The Alert Data Model](#4-alert-model)
5. [shared/models/incident.py — The Central Pipeline Object](#5-incident-model)
6. [shared/models/runbook.py — Runbooks and Past Incidents](#6-runbook-model)
7. [shared/config.py — Single Source of Truth for Settings](#7-config)
8. [shared/logger.py — Structured Logging](#8-logger)
9. [shared/kafka_utils.py — Kafka Producer and Consumer](#9-kafka-utils)
10. [shared/pg_client.py — Postgres + pgvector Client](#10-pg-client)
11. [shared/redis_client.py — Async Redis Client](#11-redis-client)
12. [infra/docker-compose.yml — The Full Infrastructure Stack](#12-docker-compose)
13. [infra/postgres/init.sql — Database Schema and Seed Data](#13-init-sql)
14. [infra/prometheus/prometheus.yml — Metrics Scraping Config](#14-prometheus)
15. [Key Concepts Learned in Phase 1](#15-key-concepts)
16. [How Everything Connects — The Big Picture](#16-big-picture)

---

## 1. Project Structure

```
incident-response-platform/
├── shared/                          ← Shared Python package (pip install -e .)
│   ├── __init__.py                  ← Makes it a Python package
│   ├── models/
│   │   ├── __init__.py
│   │   ├── alert.py                 ← AlertEvent, Severity, AlertSource, AlertStatus
│   │   ├── incident.py              ← IncidentContext, Evidence, Action, RiskLevel
│   │   └── runbook.py               ← Runbook, RunbookStep, PastIncident
│   ├── config.py                    ← Pydantic BaseSettings — reads from .env
│   ├── logger.py                    ← structlog — JSON in prod, readable in dev
│   ├── kafka_utils.py               ← KafkaProducer + KafkaConsumer wrappers
│   ├── pg_client.py                 ← Async SQLAlchemy + pgvector queries
│   └── redis_client.py              ← Async Redis — dedup, state, approval tokens
│
├── services/                        ← All microservices (empty in Phase 1)
│   ├── alert-ingestor/              ← Phase 2
│   ├── triage-agent/                ← Phase 3
│   ├── diagnosis-agent/             ← Phase 5
│   ├── remediation-agent/           ← Phase 6
│   ├── audit-consumer/              ← Phase 7
│   ├── learning-loop/               ← Phase 8
│   ├── dashboard/                   ← Phase 10
│   └── mcp-servers/                 ← Phase 4 (k8s, db, logs, metrics, remediation)
│
├── infra/
│   ├── docker-compose.yml           ← Full local stack — one command to start
│   ├── postgres/init.sql            ← DB schema + seed data — runs on first start
│   ├── prometheus/prometheus.yml    ← Scrape config for all services
│   ├── kafka/                       ← Empty (Kafka config is in docker-compose)
│   └── k8s/                         ← Empty (Phase 10)
│
├── monitoring/
│   ├── grafana/dashboards/          ← Empty (Phase 10)
│   └── prometheus/                  ← Empty
│
├── pyproject.toml                   ← Makes shared/ installable via pip install -e .
├── .env                             ← Your local config (never committed to git)
├── .env.example                     ← Template showing all required variables
├── .gitignore                       ← Ignores venv/, __pycache__/, .env
└── AGENT_CONTEXT.md                 ← Full architecture reference document
```

### Why This Structure?

**Monorepo** — All services, shared code, and infrastructure live in one repository. This means:
- One `git clone` and you have everything
- Shared models are always in sync across services
- Docker Compose can reference local Dockerfiles
- CI/CD builds and tests everything together

**`shared/` as a package** — Instead of copying code between services, we install `shared/` as a Python package (`pip install -e .`). Every service imports from it like a library:
```python
from shared.models.alert import AlertEvent, Severity
from shared.config import settings
```

**`services/` with separate directories** — Each service is an independent process that can be deployed, scaled, and restarted individually. They communicate only through Kafka (messages) or MCP (HTTP tool calls).

---

## 2. pyproject.toml — Making `shared/` a Reusable Package

**File:** `pyproject.toml`

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "incident-response-platform"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.5",
    "pydantic-settings>=2.0",
    "redis[asyncio]>=5.0",
    "asyncpg>=0.29",
    "sqlalchemy[asyncio]>=2.0",
    "structlog>=24.0",
    "python-dotenv>=1.0",
    "confluent-kafka>=2.3",
    "anthropic>=0.25",
    "httpx>=0.27",
    "fastmcp>=0.1",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["shared*"]
```

### What This Does

- **`[build-system]`** — Tells Python to use `setuptools` to build/install this project.
- **`[project]`** — Project metadata: name, version, Python version requirement.
- **`dependencies`** — All pip packages needed. When you run `pip install -e .`, ALL of these are installed.
- **`[tool.setuptools.packages.find]`** — Tells setuptools to look in the current directory (`.`) and include only `shared*` packages. This makes `shared/`, `shared/models/` etc. importable.

### Why `pip install -e .`?

The `-e` flag means "**editable** install". It creates a symlink so that when you edit files in `shared/`, the changes take effect immediately without reinstalling. Without `-e`, you'd have to run `pip install .` every time you change shared code.

### Key Dependencies Explained

| Package | Why We Need It |
|---------|---------------|
| `pydantic>=2.5` | Data validation — all our models (AlertEvent, IncidentContext) are Pydantic BaseModels. Pydantic validates types at runtime and serializes/deserializes JSON automatically. |
| `pydantic-settings>=2.0` | Separate package (split from pydantic v2) — reads environment variables into a Settings class. `config.py` uses this. |
| `redis[asyncio]>=5.0` | `redis.asyncio` module — non-blocking Redis operations. The `[asyncio]` extra installs the async transport layer. |
| `asyncpg>=0.29` | Native async PostgreSQL driver. SQLAlchemy uses this under the hood when the connection URL starts with `postgresql+asyncpg://`. 3-5x faster than psycopg2 in async contexts. |
| `sqlalchemy[asyncio]>=2.0` | ORM/SQL toolkit. We use it for connection pooling and session management, but write raw SQL for pgvector queries since SQLAlchemy doesn't support the `<=>` operator natively. |
| `structlog>=24.0` | Structured logging — outputs JSON in production (searchable in ELK/DataDog), pretty-printed in development. Every log line has consistent fields. |
| `python-dotenv>=1.0` | Reads `.env` files into environment variables. Pydantic-settings uses this automatically. |
| `confluent-kafka>=2.3` | Kafka client built on librdkafka (C library). 3-5x faster than the pure-Python `kafka-python` package. |
| `anthropic>=0.25` | Official Anthropic SDK for calling Claude. Used by triage, diagnosis, and remediation agents (Phase 3+). |
| `httpx>=0.27` | Async HTTP client. Used by agents to call MCP servers. Cleaner API than `aiohttp`, with built-in connection pooling. |
| `fastmcp>=0.1` | Library to create MCP (Model Context Protocol) servers. Claude discovers tools exposed by these servers automatically. |
| `fastapi>=0.110` | Web framework for building REST APIs. Used by alert-ingestor, MCP servers, and dashboard. |
| `uvicorn[standard]>=0.27` | ASGI server that runs FastAPI apps. The `[standard]` extra includes uvloop (faster event loop) and httptools. |

---

## 3. .env Files — Configuration Without Hardcoding

**File:** `.env.example` (committed to git — template)
**File:** `.env` (never committed — your actual secrets)

```bash
# .env.example  — copy to .env and fill in
ANTHROPIC_API_KEY=sk-ant-...

KAFKA_BOOTSTRAP_SERVERS=localhost:29092
KAFKA_SCHEMA_REGISTRY_URL=http://localhost:8081

REDIS_URL=redis://localhost:6379/0

POSTGRES_URL=postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db

SLACK_BOT_TOKEN=xoxb-...
SLACK_INCIDENTS_CHANNEL=#incidents

ENVIRONMENT=development
LOG_LEVEL=INFO
```

### Why Two Files?

- **`.env.example`** → Checked into git. Shows every variable a developer needs to set. Values are placeholders (`sk-ant-...`).
- **`.env`** → Listed in `.gitignore`. Contains your actual API keys and passwords. Never pushed to GitHub.

### How It Works

When `shared/config.py` loads, Pydantic-settings reads from:
1. Environment variables (highest priority)
2. `.env` file (fallback)
3. Default values in the code (lowest priority)

This means:
- In Docker: you set env vars in `docker-compose.yml` → Pydantic reads them directly
- In local dev: you fill in `.env` → Pydantic reads the file automatically
- No hardcoded values in any Python file

---

## 4. shared/models/alert.py — The Alert Data Model

**File:** `shared/models/alert.py`

This is the **entry point** of the entire system. Every alert — whether from Prometheus, Grafana, PagerDuty, or manual — gets normalized into an `AlertEvent`.

### Full Code with Explanations

```python
# shared/models/alert.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
```

**Imports explained:**
- `from __future__ import annotations` — Allows writing `str | None` instead of `Optional[str]`. This is a Python 3.10+ syntax that's made available in earlier versions via this import.
- `datetime` — For timestamps (`fired_at`, `received_at`)
- `Enum` — Python's enumeration class. Combined with `str`, creates string-valued enums that serialize cleanly to JSON.
- `uuid4` — Generates random unique IDs like `"9c3f6107-a4b2-4f3e-8c12-abcdef123456"`. These become the primary keys in our system.
- `BaseModel` — Pydantic's base class. Any class inheriting from it gets automatic JSON serialization, type validation, and schema generation.
- `Field` — Allows setting defaults, factories, and descriptions on model fields.

### The Enums

```python
class AlertSource(str, Enum):
    """Where did this alert come from?"""
    PROMETHEUS    = "prometheus"
    GRAFANA       = "grafana"
    PAGERDUTY     = "pagerduty"
    DATADOG       = "datadog"
    WEBHOOK       = "webhook"
    MANUAL        = "manual"    # engineer manually triggered
```

**Why `(str, Enum)`?** — By inheriting from both `str` and `Enum`, the enum values serialize to plain strings in JSON. Without `str`, Pydantic would serialize `AlertSource.PROMETHEUS` as an object, not as `"prometheus"`.

```python
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
```

**Severity is the single most important classification in the system:**
- **P1** alerts → go through all 3 agents (triage → diagnosis → remediation)
- **P2** alerts → same as P1
- **P3/P4** alerts → triage agent auto-resolves them, no diagnosis needed

```python
class AlertStatus(str, Enum):
    FIRING    = "firing"      # active alert
    RESOLVED  = "resolved"    # alert condition gone
    SILENCED  = "silenced"    # intentionally suppressed
```

### AlertAnnotation

```python
class AlertAnnotation(BaseModel):
    """Human-readable metadata attached to the alert"""
    summary:     str
    description: str = ""
    runbook_url: str = ""     # link to wiki/Confluence runbook if exists
```

Prometheus Alertmanager attaches annotations to alerts. The `summary` field is critical — it's what gets embedded for pgvector similarity search in Phase 3.

### AlertEvent — The Main Model

```python
class AlertEvent(BaseModel):
    alert_id:    str = Field(default_factory=lambda: str(uuid4()))
    source:      AlertSource
    status:      AlertStatus = AlertStatus.FIRING
    severity:    Severity | None = None      # None until triage sets it
    name:        str                         # "HighErrorRate", "PodCrashLooping"
    service:     str                         # "payment-service", "order-api"
    environment: str = "production"
    labels:      dict[str, str] = Field(default_factory=dict)
    annotations: AlertAnnotation | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    fired_at:    datetime = Field(default_factory=datetime.utcnow)
    received_at: datetime = Field(default_factory=datetime.utcnow)
    trace_id:    str | None = None
```

**Key design decisions:**

| Field | Why |
|-------|-----|
| `alert_id = Field(default_factory=lambda: str(uuid4()))` | Auto-generates a unique ID. `default_factory` runs the lambda each time a new AlertEvent is created (not once at class level). |
| `severity: Severity \| None = None` | Starts as `None` because severity is set by the triage agent in Phase 3, not by the alert source. |
| `raw_payload: dict[str, Any]` | The original JSON from Prometheus/PagerDuty is preserved here for debugging. If normalization loses something, you can always check `raw_payload`. |
| `fired_at` vs `received_at` | `fired_at` = when the alert condition triggered. `received_at` = when our system got it. The difference tells you about ingestion latency. |

### The fingerprint() Method — Why This Matters

```python
def fingerprint(self) -> str:
    return f"{self.name}:{self.service}:{self.environment}"
```

**This is the deduplication key.** During a real incident:
1. Prometheus evaluates alert rules every 15-60 seconds
2. If `HighErrorRate` on `payment-service` is still true, it fires the same alert again
3. Without dedup, you'd create 40+ duplicate incidents in 10 minutes
4. The fingerprint `HighErrorRate:payment-service:production` is stored in Redis with a 10-minute TTL
5. If the fingerprint already exists in Redis → skip (duplicate)
6. If it doesn't exist → process the alert and set the fingerprint

---

## 5. shared/models/incident.py — The Central Pipeline Object

**File:** `shared/models/incident.py`

`IncidentContext` is **the most important object in the entire system**. It's created when an alert enters the system and gets enriched as it flows through each agent:

```
AlertEvent → [Triage Agent] → IncidentContext (+ severity, runbook)
                                    ↓
                              [Diagnosis Agent] → IncidentContext (+ root_cause, evidence)
                                    ↓
                              [Remediation Agent] → IncidentContext (+ actions, resolution)
```

### The Enums

```python
class IncidentStatus(str, Enum):
    TRIAGING     = "triaging"          # triage agent working
    DIAGNOSING   = "diagnosing"        # diagnosis agent working
    REMEDIATING  = "remediating"       # remediation agent working
    AWAITING_APPROVAL = "awaiting_approval"  # human gate
    RESOLVED     = "resolved"          # fixed
    ESCALATED    = "escalated"         # handed to human, agent gave up
```

**Status flow:** `triaging → diagnosing → remediating → (awaiting_approval →) resolved`
If anything fails, status becomes `escalated` and a human takes over.

```python
class RiskLevel(str, Enum):
    LOW  = "low"       # auto-execute (restart pod, clear cache)
    MED  = "medium"    # Slack approval, 5-min timeout
    HIGH = "high"      # Slack + PagerDuty, no timeout
```

**This is the safety mechanism.** An AI agent with unrestricted write access to production is dangerous. RiskLevel determines how much human oversight is needed before executing a fix.

### Evidence — What the Agent Saw

```python
class Evidence(BaseModel):
    source:       str           # "k8s-mcp", "logs-mcp", "metrics-mcp"
    tool_name:    str           # "get_pod_logs", "search_logs"
    content:      str           # the actual data (log lines, metrics, etc)
    relevance:    str           # agent's explanation of why this matters
    collected_at: datetime = Field(default_factory=datetime.utcnow)
```

When the diagnosis agent calls MCP tools (e.g., "get pod logs from Kubernetes"), each tool result becomes an `Evidence` object. This creates an **audit trail** — humans can see exactly what data the agent based its root cause analysis on.

### Action — What the Agent Plans to Do

```python
class Action(BaseModel):
    action_id:   str = Field(default_factory=lambda: str(uuid4()))
    tool:        str            # "remediation-mcp"
    tool_fn:     str            # "restart_pod"
    parameters:  dict[str, Any] # {"namespace": "prod", "pod": "payment-xyz"}
    risk_level:  RiskLevel
    reasoning:   str            # why the agent thinks this will help
    executed:    bool = False
    result:      str | None = None
    executed_at: datetime | None = None
```

**Key insight:** Actions are **planned first, then executed separately**. The remediation agent:
1. Creates a list of Actions (the plan)
2. Checks each action's `risk_level`
3. Only then executes (if low risk) or requests approval (if medium/high risk)

This means a human can see the full remediation plan before anything happens.

### IncidentContext — The Full Model

```python
class IncidentContext(BaseModel):
    incident_id:   str = Field(default_factory=lambda: str(uuid4()))
    status:        IncidentStatus = IncidentStatus.TRIAGING
    alert:         AlertEvent         # the original alert — never mutated

    # ── Set by Triage Agent ──────────────────────────────────────────
    severity:            Severity | None = None
    triage_summary:      str = ""
    matched_runbook_id:  str | None = None
    similar_incident_ids: list[str] = Field(default_factory=list)
    triage_confidence:   float = 0.0    # 0.0–1.0
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
    approved_by:         str | None = None
    resolved_at:         datetime | None = None
    resolution_summary:  str = ""

    # ── Metadata ─────────────────────────────────────────────────────
    created_at:  datetime = Field(default_factory=datetime.utcnow)
    updated_at:  datetime = Field(default_factory=datetime.utcnow)
    trace_id:    str | None = None
```

**Why are most fields optional/defaulted?** Because the IncidentContext is created at triage time with only the alert. Diagnosis fields are empty until the diagnosis agent sets them. Remediation fields are empty until the remediation agent sets them. The model grows richer as it flows through the pipeline.

### Critical Methods

```python
def mttr_seconds(self) -> int | None:
    """Mean Time To Resolve — the KPI you quote in interviews."""
    if self.resolved_at:
        return int((self.resolved_at - self.created_at).total_seconds())
    return None
```

**MTTR (Mean Time To Resolve)** is the primary KPI. The goal is to reduce it from ~45 minutes to ~8 minutes. This method calculates it from timestamps.

```python
def add_evidence(self, source: str, tool: str, content: str, relevance: str) -> None:
    self.evidence.append(Evidence(
        source=source, tool_name=tool,
        content=content, relevance=relevance
    ))
    self.updated_at = datetime.utcnow()
```

Convenience method for the diagnosis agent to add evidence from MCP tool calls.

---

## 6. shared/models/runbook.py — Runbooks and Past Incidents

**File:** `shared/models/runbook.py`

### RunbookStep

```python
class RunbookStep(BaseModel):
    order:       int
    description: str
    command:     str | None = None    # shell command if applicable
    automated:   bool = False         # can the agent do this step?
```

The `automated` flag is critical. A runbook might say "Step 3: Rollback deployment" — but if `automated=False`, the agent knows it needs human approval before executing this step.

### Runbook

```python
class Runbook(BaseModel):
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
```

**Why no `embedding` field?** Embeddings are 1536-element float arrays (6KB each). Storing them in the Pydantic model would bloat every JSON serialization. Instead, the embedding lives only in the Postgres `runbooks.embedding` column and is managed by `pg_client.py`.

### PastIncident — Few-Shot Context for the Triage Agent

```python
class PastIncident(BaseModel):
    incident_id:      str
    alert_name:       str
    service:          str
    root_cause:       str
    resolution:       str
    mttr_seconds:     int
    severity:         Severity
    resolved_at:      datetime
    similarity_score: float     # cosine similarity from pgvector query
```

When a new alert comes in, the triage agent asks: "Have we seen something similar before?" pgvector returns the top-3 most similar past incidents, and they're injected into Claude's context:

> "We saw this exact pattern in October — root cause was connection pool exhaustion, fixed by restarting the app server in 4 minutes."

---

## 7. shared/config.py — Single Source of Truth for Settings

**File:** `shared/config.py`

```python
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from pathlib import Path


class Settings(BaseSettings):
    # ── Anthropic ────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    anthropic_model: str = Field(default="claude-opus-4-5")

    # ── Kafka ────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(default="localhost:29092")

    # Topic names — defined once, used everywhere
    topic_alerts_raw: str = Field(default="alerts.raw")
    topic_alerts_triaged: str = Field(default="alerts.triaged")
    topic_incidents_active: str = Field(default="incidents.active")
    topic_audit_events: str = Field(default="audit.events")
    topic_incidents_resolved: str = Field(default="incidents.resolved")

    # ── Redis, Postgres, MCP, Slack, App ───────────────────────────
    # ... (all defined with sensible defaults)

    class Config:
        env_file = str(Path(__file__).parent.parent / ".env")
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

### How It Works

**`Field(...)` (three dots)** — The `...` means "required, no default". If `ANTHROPIC_API_KEY` is not in the environment or `.env`, the app crashes at startup with a clear error. This is intentional — you want to fail fast, not silently break when calling Claude.

**`Field(default="alerts.raw")`** — Has a default. You can override it via env var `TOPIC_ALERTS_RAW=something.else`, but most people use the default.

**`class Config: case_sensitive = False`** — Pydantic-settings will match `KAFKA_BOOTSTRAP_SERVERS` to `kafka_bootstrap_servers` regardless of case. This is important because env vars are traditionally UPPER_CASE but Python fields are snake_case.

**`@lru_cache()`** — The `get_settings()` function is called once, and the result is cached forever. Every subsequent call returns the same `Settings` object. This is safe because env vars don't change at runtime.

**`settings = get_settings()`** — Module-level convenience. Services just do:
```python
from shared.config import settings
print(settings.kafka_bootstrap_servers)  # "localhost:29092"
```

### Why Not Hardcode?

Inside Docker, the Kafka address is `kafka:9092` (Docker network name). On your local machine, it's `localhost:29092` (port-mapped out of Docker). The same code needs to work in both environments — hence configuration via environment variables.

---

## 8. shared/logger.py — Structured Logging

**File:** `shared/logger.py`

```python
import structlog

def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer()
            if _is_development()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
```

### Why structlog?

Standard Python logging produces lines like:
```
2024-01-15 12:34:56 INFO Alert abc123 triaged with severity P1
```

**This is unsearchable.** If you want to find all P1 alerts for `payment-service`, you'd need regex.

structlog produces:
```json
{"level":"info","service":"triage-agent","event":"alert_triaged","alert_id":"abc123","severity":"P1","confidence":0.85,"timestamp":"2024-01-15T12:34:56Z"}
```

Now you can search in ELK/DataDog: `severity:P1 AND service:payment-service` — instant results.

### Development vs Production

```python
def _is_development() -> bool:
    import os
    return os.getenv("ENVIRONMENT", "development") == "development"
```

- **Development** → `ConsoleRenderer()` → pretty-printed, colored output for your terminal
- **Production** → `JSONRenderer()` → structured JSON for log aggregation systems

### Usage Pattern

```python
log = get_logger("triage-agent")

# CORRECT — structured fields (searchable)
log.info("alert_triaged",
    alert_id=alert.alert_id,
    severity="P1",
    confidence=0.85,
    duration_ms=234,
)

# WRONG — unstructured string (not searchable)
log.info(f"Alert {alert_id} triaged with severity P1")
```

---

## 9. shared/kafka_utils.py — Kafka Producer and Consumer

**File:** `shared/kafka_utils.py`

### Why Kafka?

When a deployment goes wrong, Prometheus fires 50+ alerts in seconds. Without a message broker:
- Your alert handler gets overwhelmed
- Alerts get dropped if the handler is slow
- If the handler crashes, all in-flight alerts are lost

Kafka acts as a **buffer** between alert sources and agents:
- Alerts are durably stored on disk
- If the triage agent is down, alerts wait in the queue
- When the agent restarts, it replays all missed alerts
- Multiple consumers can read from the same topic independently

### KafkaProducer

```python
class KafkaProducer:
    def __init__(self, bootstrap_servers: str):
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",              # wait for all replicas to confirm
            "retries": 3,
            "retry.backoff.ms": 300,
            "compression.type": "snappy",
            "linger.ms": 5,             # batch messages for 5ms before sending
        })
```

**Key settings explained:**

| Setting | Value | Why |
|---------|-------|-----|
| `acks: all` | Wait for all replicas | Guarantees no message loss even if a broker dies |
| `retries: 3` | Retry failed sends | Network blips are transient; retrying usually works |
| `compression.type: snappy` | Compress messages | Reduces network bandwidth; snappy is fast with decent compression |
| `linger.ms: 5` | Wait 5ms before sending | Batches multiple messages together for efficiency instead of sending one at a time |

```python
def publish(self, topic: str, value: dict, key: str | None = None,
            headers: dict[str, str] | None = None) -> None:
    self._producer.produce(
        topic=topic,
        value=json.dumps(value, default=str).encode(),
        key=key.encode() if key else None,
        headers=kafka_headers,
        on_delivery=self._on_delivery,
    )
    self._producer.poll(0)
```

**Why `key`?** — Kafka partitions messages by key. Same key = same partition = same ordering. We use `incident_id` as key so all messages for one incident are processed in order.

**Why `json.dumps(value, default=str).encode()`?** — Kafka only accepts bytes. We serialize Python dicts to JSON strings, then encode to UTF-8 bytes. The `default=str` ensures datetime objects become strings instead of crashing.

### KafkaConsumer

```python
class KafkaConsumer:
    def __init__(self, bootstrap_servers, group_id, topics):
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,    # CRITICAL — manual commit
            "max.poll.interval.ms": 300_000,  # 5 min
        })
```

**Critical settings:**

| Setting | Value | Why |
|---------|-------|-----|
| `group.id` | e.g., `"triage-agent"` | Consumers in the same group share partitions. 4 partitions + 4 consumers = each processes 1/4 of messages. Horizontal scaling. |
| `auto.offset.reset: earliest` | Start from beginning | If a new consumer group is created, read ALL existing messages, not just new ones. No missed alerts. |
| `enable.auto.commit: False` | **NEVER auto-commit** | Auto-commit marks a message as processed as soon as it's received. If the agent crashes during processing, the message is lost forever. Manual commit means we only mark it processed AFTER successful handling. |
| `max.poll.interval.ms: 300000` | 5 minutes | Claude LLM calls can take 30-60 seconds. Without this high timeout, Kafka would think the consumer is dead and reassign its partitions. |

```python
async def consume_loop(self, handler):
    while self._running:
        msg = self._consumer.poll(timeout=1.0)
        if msg is None:
            continue

        try:
            payload = json.loads(msg.value().decode("utf-8"))
            headers = self._extract_headers(msg)
            await handler(payload, headers)

            # Commit AFTER successful processing
            self._consumer.commit(msg)
        except Exception:
            # Don't commit — message will be redelivered
            pass
```

**The commit pattern is the most important thing here.** Notice:
1. Receive message
2. Process it (call handler)
3. **Only then** commit the offset
4. If step 2 crashes → offset is NOT committed → Kafka redelivers the message on restart

---

## 10. shared/pg_client.py — Postgres + pgvector Client

**File:** `shared/pg_client.py`

### Connection Setup

```python
class PostgresClient:
    def __init__(self, database_url: str, pool_size: int = 10):
        self._engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=5,
            pool_pre_ping=True,      # check connection health before using
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
```

**Why async?** Every service in our platform is async (FastAPI, Redis, Kafka consumers). If we used sync Postgres calls, a single slow query would block ALL other requests on that thread. Async lets other coroutines run while waiting for Postgres.

**`pool_size=10`** — Postgres connections are expensive to create. The connection pool maintains 10 persistent connections. When a query needs a connection, it borrows from the pool. When done, it returns it. No connection setup overhead.

**`pool_pre_ping=True`** — Before using a pooled connection, ping Postgres to check if it's still alive. If the connection dropped (Postgres restarted), get a fresh one. Without this, you'd get "connection reset" errors.

### Session Context Manager

```python
@asynccontextmanager
async def session(self) -> AsyncIterator[AsyncSession]:
    async with self._session_factory() as sess:
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
```

This ensures:
- On success → `commit()` (save changes)
- On error → `rollback()` (discard changes, no partial writes)
- Connection is always returned to the pool

### save_incident — Upsert Pattern

```python
async def save_incident(self, incident: dict) -> None:
    async with self.session() as sess:
        await sess.execute(text("""
            INSERT INTO incidents (...)
            VALUES (...)
            ON CONFLICT (incident_id) DO UPDATE SET
                status = EXCLUDED.status,
                root_cause = EXCLUDED.root_cause,
                ...
        """), incident)
```

**Why upsert (INSERT ... ON CONFLICT)?** An incident is saved multiple times as it progresses:
1. Triage agent saves it (status=triaging, severity=P1)
2. Diagnosis agent saves it again (adds root_cause)
3. Remediation agent saves it again (adds resolution)

`ON CONFLICT DO UPDATE` means: if this `incident_id` already exists, update the row instead of failing.

### pgvector Similarity Search

```python
async def find_similar_runbooks(self, embedding, service, limit=3):
    result = await sess.execute(text("""
        SELECT
            runbook_id, title, description, steps, tags,
            1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM runbooks
        WHERE service = ANY(CAST(:services AS text[]))
           OR :service = ANY(services)
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """), {...})
```

**The `<=>` operator** is pgvector's cosine distance operator:
- `<=>` returns the **distance** (0 = identical, 2 = opposite)
- `1 - distance` = **similarity** (1 = identical, 0 = unrelated)
- `ORDER BY <=> ASC` = most similar first

**Why cosine similarity?** Cosine measures the angle between vectors, not the magnitude. A short 10-word alert description and a long 200-word runbook can still have high similarity if they discuss the same concepts.

**HNSW index** (`CREATE INDEX ... USING hnsw`) makes this query fast:
- Without index: O(n) — compares against every embedding. Slow at 10K+ rows.
- With HNSW index: ~O(log n) — navigates a graph of nearest neighbors. Fast even at 1M rows.

---

## 11. shared/redis_client.py — Async Redis Client

**File:** `shared/redis_client.py`

### Redis Key Patterns

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `alert:dedup:{tenant_id}:{fingerprint}` | string | 10 min | Prevent duplicate incidents from flapping alerts |
| `incident:status:{tenant_id}:{incident_id}` | string | 24 hr | Fast status lookup for dashboard |
| `incident:context:{tenant_id}:{incident_id}` | string (JSON) | 24 hr | Cached IncidentContext to avoid hitting Postgres |
| `approval:token:{tenant_id}:{request_id}` | string (JSON) | risk-based | Approval gate for remediation actions |
| `session:incident:{session_id}` | set | 24 hr | Dashboard session tracking |

> **Why `tenant_id` in every key?** Without it, Tenant A's alert fingerprint could suppress Tenant B's identical alert — a cross-tenant data leak. The prefix ensures complete isolation.

### Alert Deduplication — The Most Critical Function

```python
async def is_duplicate(self, tenant_id: str, fingerprint: str) -> bool:
    key = f"alert:dedup:{tenant_id}:{fingerprint}"
    exists = await self._redis.exists(key)
    return bool(exists)

async def mark_seen(self, tenant_id: str, fingerprint: str, ttl: int = 600) -> None:
    key = f"alert:dedup:{tenant_id}:{fingerprint}"
    await self._redis.set(key, "1", ex=ttl)
```

**Flow in the alert ingestor (Phase 2):**
1. Alert arrives: `HighErrorRate:payment-service:production` for tenant `acme_corp`
2. Call `is_duplicate("acme_corp", "HighErrorRate:payment-service:production")` → `False`
3. Call `mark_seen("acme_corp", "HighErrorRate:payment-service:production")` → stored for 10 min
4. Same alert from a **different tenant** `globex_inc` → `is_duplicate()` returns `False` (different key!)
5. Same alert from `acme_corp` again → `is_duplicate()` returns `True` → skip

**Why 10 minutes?** If an alert is firing continuously for 10 minutes and we haven't resolved it, the dedup expires and a new incident is created. This handles cases where the first incident was escalated/stuck.

### Approval Tokens — The Human Safety Gate

```python
APPROVAL_TTLS = {
    "low":    0,         # no token needed, auto-execute
    "medium": 300,       # 5 minutes then escalate
    "high":   0,         # no timeout — human must respond
}

async def set_approval_token(self, tenant_id, request_id, risk_level, incident_id):
    key = f"approval:token:{tenant_id}:{request_id}"
    ttl = APPROVAL_TTLS.get(risk_level, 300)
    value = json.dumps({
        "request_id": request_id,
        "incident_id": incident_id,
        "risk_level": risk_level,
        "status": "pending",
    })
    if ttl > 0:
        await self._redis.set(key, value, ex=ttl)
    else:
        await self._redis.set(key, value)   # no expiry
```

**Why Redis for approval tokens?** Because tokens need to:
1. Be checked very fast (sub-millisecond)
2. Expire automatically (TTL-based)
3. Be accessible from multiple services (Slack webhook handler + remediation agent)

### Factory Function

```python
async def init_redis(redis_url: str, max_connections: int = 20) -> RedisClient:
    pool = aioredis.ConnectionPool.from_url(redis_url, max_connections=max_connections)
    client = aioredis.Redis(connection_pool=pool)
    await client.ping()   # verify connectivity at startup
    return RedisClient(redis=client)
```

**Usage at service startup:**
```python
from shared.redis_client import init_redis
from shared.config import settings

redis = await init_redis(settings.redis_url)
is_dup = await redis.is_duplicate("HighErrorRate:payment-service:production")
```

---

## 12. infra/docker-compose.yml — The Full Infrastructure Stack

**File:** `infra/docker-compose.yml`

One command starts the entire platform's infrastructure:
```bash
cd infra && docker compose up -d
```

### Services Started

| Service | Image | Port | What It Does |
|---------|-------|------|-------------|
| **Zookeeper** | confluentinc/cp-zookeeper:7.5.3 | 2181 | Kafka's control plane — manages cluster membership, leader election. Required for Kafka to start. |
| **Kafka** | confluentinc/cp-kafka:7.5.3 | 9092 (internal), 29092 (external) | Message broker. All inter-service communication goes through Kafka topics. |
| **Schema Registry** | confluentinc/cp-schema-registry:7.5.3 | 8081 | Validates that messages on Kafka topics match expected schemas. Prevents malformed messages. |
| **Kafka UI** | provectuslabs/kafka-ui | 8080 | Web interface to see topics, messages, consumer groups. Open http://localhost:8080. |
| **Redis** | redis:7.2-alpine | 6379 | In-memory store for dedup, state, approval tokens. Alpine image is tiny (~5MB). |
| **Postgres** | pgvector/pgvector:pg16 | 5432 | Primary database with pgvector extension for embedding storage and similarity search. |
| **Prometheus** | prom/prometheus:v2.48.0 | 9090 | Scrapes metrics from all services every 15 seconds. |
| **Grafana** | grafana/grafana:10.2.0 | 3000 | Dashboards for MTTR, active incidents, agent performance. |

### Kafka Dual Listener — Why Two Ports?

```yaml
KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka:9092,EXTERNAL://localhost:29092
```

- **Port 9092** — Used by services inside Docker. They reference Kafka as `kafka:9092` (Docker DNS resolves the container name).
- **Port 29092** — Used by your local machine (when running Python scripts outside Docker). Maps to the same broker.

Same Kafka, two addresses, two audiences.

### Health Checks — Anti-Race Condition

```yaml
kafka:
    depends_on:
      zookeeper:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "kafka-broker-api-versions", "--bootstrap-server", "localhost:9092"]
      interval: 15s
      start_period: 30s
```

Without health checks, Docker starts all containers simultaneously. Kafka tries to connect to Zookeeper before Zookeeper is ready → crash. `condition: service_healthy` ensures Zookeeper is fully running before Kafka starts.

### Redis Configuration

```yaml
command: >
  redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru --loglevel warning
```

- `--appendonly yes` → Persist data to disk. If Redis restarts, approval tokens aren't lost.
- `--maxmemory 512mb` → Cap memory usage at 512MB.
- `--maxmemory-policy allkeys-lru` → When full, evict Least Recently Used keys.

### Postgres Initialization

```yaml
volumes:
  - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
```

The `init.sql` file is mounted into the special `/docker-entrypoint-initdb.d/` directory. The official Postgres Docker image automatically runs all `.sql` files in this directory on **first** container start (not on restarts).

---

## 13. infra/postgres/init.sql — Database Schema and Seed Data

**File:** `infra/postgres/init.sql`

### Extensions

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- uuid generation functions
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- trigram text search
```

The `vector` extension is what makes Postgres understand embedding vectors. Without it, `vector(1536)` would be an unknown type.

### Four Tables

**1. `incidents`** — One row per incident
```sql
CREATE TABLE incidents (
    id                 SERIAL PRIMARY KEY,
    incident_id        TEXT UNIQUE NOT NULL,
    status             TEXT NOT NULL DEFAULT 'triaging',
    alert_name         TEXT NOT NULL,
    service            TEXT NOT NULL,
    severity           TEXT,
    root_cause         TEXT,
    resolution_summary TEXT,
    mttr_seconds       INTEGER,
    raw_context        JSONB NOT NULL DEFAULT '{}',   -- full IncidentContext JSON
    embedding          vector(1536),                   -- 1536 floats from embedding model
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ...
);
```

**`raw_context JSONB`** — The full IncidentContext (with all evidence, actions, etc.) stored as JSON. This means we can reconstruct the complete incident lifecycle from a single row.

**`embedding vector(1536)`** — The pgvector column. `1536` is the dimension of embeddings from OpenAI's `text-embedding-3-small` model (or equivalent). Stored as a binary array of 1536 floats.

**2. `runbooks`** — Operational procedures
**3. `audit_events`** — Every agent action (audit trail)
**4. `approval_requests`** — Pending/resolved human approvals

### HNSW Indexes

```sql
CREATE INDEX idx_incidents_embedding ON incidents
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);
```

**Without this index:** Similarity search scans every row. O(n). At 10K incidents → hundreds of milliseconds.
**With HNSW index:** Approximate nearest neighbor search. ~O(log n). At 10K incidents → ~5ms.

**`m = 16`** — Number of links per node in the graph. Higher = better accuracy, more memory.
**`ef_construction = 128`** — How many candidates to consider during index build. Higher = better index quality, slower build.

### Triggers for `updated_at`

```sql
CREATE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER incidents_touch
    BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
```

Every time a row is updated, `updated_at` is automatically set to the current timestamp. No need to set it manually in application code.

### Seed Runbooks

The init script inserts 3 starter runbooks:
1. **High error rate** — for `HighErrorRate` alerts on payment/order services
2. **Pod crash looping** — for `PodCrashLooping` alerts on any service
3. **DB connection pool exhausted** — for database connection issues

These give the triage agent something to match against from day 1.

---

## 14. infra/prometheus/prometheus.yml — Metrics Scraping Config

**File:** `infra/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s        # how often to scrape /metrics endpoints
  evaluation_interval: 15s    # how often to evaluate alert rules

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: "alert-ingestor"
    static_configs:
      - targets: ["alert-ingestor:8000"]
        labels:
          service: "alert-ingestor"

  # ... similar entries for all MCP servers and dashboard
```

**What this does:** Every 15 seconds, Prometheus sends an HTTP GET to each service's `/metrics` endpoint and stores the metrics. In Phase 9, services will expose custom metrics like:
- `incident_mttr_seconds` — histogram of MTTR values
- `agent_processing_duration_ms` — how long each agent takes
- `alerts_processed_total` — counter of processed alerts

For now, services that don't exist yet simply fail to scrape silently — Prometheus logs a warning and moves on.

---

## 15. Key Concepts Learned in Phase 1

### 1. Pydantic Data Models
- `BaseModel` gives you free JSON serialization, type validation, and documentation
- `Field(default_factory=...)` for mutable defaults (lists, dicts, UUIDs)
- `str | None` for optional fields
- `(str, Enum)` for string-valued enums that serialize cleanly

### 2. Configuration via Environment Variables
- `pydantic-settings` reads env vars automatically
- `.env` file for local dev, env vars for Docker
- `lru_cache` for singleton settings object

### 3. Kafka Fundamentals
- Producer: publish messages to topics with keys for ordering
- Consumer: poll for messages, process, then commit offset
- **Never auto-commit** — always commit after successful processing
- Consumer groups for horizontal scaling

### 4. Async Python
- `asyncpg` for non-blocking Postgres queries
- `redis.asyncio` for non-blocking Redis operations
- `asynccontextmanager` for clean resource management
- Why async matters: one slow query doesn't block everything else

### 5. pgvector
- Store embeddings as `vector(1536)` columns
- `<=>` operator for cosine distance
- HNSW index for fast approximate nearest neighbor search
- Similarity = `1 - cosine_distance`

### 6. Docker Compose
- `depends_on` with `condition: service_healthy` prevents race conditions
- Named volumes persist data across container restarts
- Health checks tell Docker when a service is ready

### 7. Structured Logging
- Every log line has consistent, searchable fields
- JSON in production, readable text in development
- Always include `alert_id`, `incident_id`, `service`, `severity`

### 8. Monorepo with shared packages
- `pip install -e .` makes `shared/` importable everywhere
- `pyproject.toml` defines the package and its dependencies
- One source of truth for models, config, and utilities

### 9. Multi-Tenancy (Level 1 — Logical Isolation)
- `tenant_id` added to `AlertEvent`, `IncidentContext`, and every DB table
- Redis keys prefixed with `tenant_id` to prevent cross-tenant cache collisions
- Postgres queries always include `WHERE tenant_id = :tenant_id`
- Kafka message keys use `{tenant_id}:{id}` for partition routing
- JWT middleware extracts `tenant_id` at the request boundary — never trusted from client body
- Same pattern used by Stripe, Slack, Datadog for 10–10,000 tenants
- Defense in depth: 7 layers (middleware, API input, Kafka key, Kafka header, DB, Redis, logs)

---

## 16. How Everything Connects — The Big Picture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Phase 1 — What We Built                          │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                     shared/ (Python Package)                        │ │
│  │                                                                     │ │
│  │  models/alert.py ──→ AlertEvent (the input)                        │ │
│  │  models/incident.py → IncidentContext (flows through pipeline)     │ │
│  │  models/runbook.py ─→ Runbook, PastIncident (context for agents)   │ │
│  │                                                                     │ │
│  │  config.py ──────────→ Settings (all env vars, single object)      │ │
│  │  logger.py ──────────→ structlog (JSON logging for all services)   │ │
│  │  kafka_utils.py ─────→ KafkaProducer + KafkaConsumer               │ │
│  │  pg_client.py ───────→ PostgresClient (save, search, embed)        │ │
│  │  redis_client.py ────→ RedisClient (dedup, state, approvals)       │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                              ↑ imported by ↑                             │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                     infra/ (Docker Compose)                         │ │
│  │                                                                     │ │
│  │  Kafka (:29092) ─────→ 5 topics for agent handoffs                 │ │
│  │  Redis (:6379) ──────→ dedup keys, approval tokens, state          │ │
│  │  Postgres (:5432) ───→ 4 tables, pgvector indexes, seed data      │ │
│  │  Prometheus (:9090) ─→ scrapes /metrics from all services          │ │
│  │  Grafana (:3000) ────→ dashboards (configured in Phase 10)         │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                     services/ (Empty — Built in Phases 2–10)        │ │
│  │                                                                     │ │
│  │  Phase 2:  alert-ingestor  →  receives alerts, publishes to Kafka  │ │
│  │  Phase 3:  triage-agent    →  classifies severity via Claude       │ │
│  │  Phase 4:  mcp-servers     →  diagnostic tools for agents          │ │
│  │  Phase 5:  diagnosis-agent →  finds root cause via MCP tools       │ │
│  │  Phase 6:  remediation     →  fixes issues with human approval     │ │
│  │  Phase 7:  audit-consumer  →  records all actions                  │ │
│  │  Phase 8:  learning-loop   →  embeds resolved incidents            │ │
│  │  Phase 10: dashboard       →  live incident view                   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

### The Data Flow (After All Phases Are Built)

```
Prometheus Alert
      │
      ▼
Alert Ingestor (Phase 2)
  ├── Normalize → AlertEvent
  ├── Redis dedup check
  └── Publish to Kafka: alerts.raw
                │
                ▼
Triage Agent (Phase 3)
  ├── Generate embedding
  ├── pgvector → similar runbooks + past incidents
  ├── Claude → severity + summary
  └── Publish to Kafka: alerts.triaged
                │
                ▼
Diagnosis Agent (Phase 5)
  ├── Call k8s-mcp → pod status, logs
  ├── Call logs-mcp → error patterns
  ├── Call metrics-mcp → latency, error rate
  ├── Claude → root cause analysis
  └── Publish to Kafka: incidents.active
                │
                ▼
Remediation Agent (Phase 6)
  ├── Claude → generate Action plan
  ├── Risk check: LOW → execute, MED → Slack, HIGH → Slack+PagerDuty
  ├── Execute via remediation-mcp
  └── Publish to Kafka: incidents.resolved
                │
                ▼
Learning Loop (Phase 8)
  ├── Generate embedding of incident
  └── Store in Postgres pgvector → next similar alert benefits
```

**Phase 1 built the foundation that makes all of this possible.** Every box in this diagram imports from `shared/` and connects to infrastructure defined in `infra/docker-compose.yml`.
