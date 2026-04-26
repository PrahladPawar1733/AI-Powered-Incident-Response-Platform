# AI-Powered Incident Response Platform — Agent Context Document

> **Purpose of this file:** This document is written for an AI agent (Claude or similar) to deeply understand the architecture, codebase, data flows, design decisions, and conventions of this project. Read this before generating any code, answering questions, or making architectural recommendations.

---

## 1. Project Identity

| Field | Value |
|---|---|
| **Project name** | incident-response-platform |
| **Purpose** | Autonomously triage, diagnose, and remediate production incidents using AI agents |
| **Primary KPI** | MTTR (Mean Time To Resolve) — target: reduce from ~45 min to ~8 min |
| **Language** | Python 3.12 |
| **AI model** | claude-opus-4-5 (via Anthropic SDK) |
| **Protocol** | MCP (Model Context Protocol) for all tool calls |
| **Message broker** | Apache Kafka 3.5 — used ONLY for async event flows, NOT for tool calls |
| **Multi-tenancy** | Level 1 — logical isolation, `tenant_id` in every layer (JWT → middleware → Kafka → Postgres → Redis → logs) |

---

## 2. Monorepo Structure

```
incident-response-platform/
├── shared/                          # Installed as a local Python package (pip install -e .)
│   ├── models/
│   │   ├── alert.py                 # AlertEvent, Severity, AlertSource, AlertStatus
│   │   ├── incident.py              # IncidentContext, Evidence, Action, RiskLevel, IncidentStatus
│   │   └── runbook.py               # Runbook, RunbookStep, PastIncident
│   ├── config.py                    # All env vars via pydantic BaseSettings — SINGLE SOURCE OF TRUTH
│   ├── kafka_utils.py               # KafkaProducer, KafkaConsumer base classes
│   ├── redis_client.py              # Async Redis — dedup, session state, approval tokens
│   ├── pg_client.py                 # Async SQLAlchemy + pgvector similarity queries
│   └── logger.py                    # structlog — JSON in prod, readable in dev
│
├── services/
│   ├── alert-ingestor/              # Phase 2 — FastAPI, receives alerts, normalizes, publishes
│   ├── triage-agent/                # Phase 3 — Agent 1: classify severity, retrieve runbook
│   ├── diagnosis-agent/             # Phase 5 — Agent 2: root cause via MCP tools
│   ├── remediation-agent/           # Phase 6 — Agent 3: plan + execute fixes, human gate
│   ├── audit-consumer/              # Phase 7 — Consumes audit.events → Postgres + Slack
│   ├── learning-loop/               # Phase 8 — Resolved incidents → pgvector embeddings
│   ├── dashboard/                   # Phase 10 — FastAPI + WebSocket live view
│   └── mcp-servers/
│       ├── k8s-mcp/                 # Port 8001 — Kubernetes read tools
│       ├── db-mcp/                  # Port 8002 — Database diagnostic tools
│       ├── logs-mcp/                # Port 8003 — Loki/CloudWatch log search
│       ├── metrics-mcp/             # Port 8004 — Prometheus query tools
│       └── remediation-mcp/         # Port 8005 — Write tools (restart, scale, rollback)
│
├── infra/
│   ├── docker-compose.yml           # Full local stack — one command to start everything
│   ├── postgres/init.sql            # Schema + pgvector setup + seed runbooks — runs on first start
│   ├── prometheus/prometheus.yml    # Scrape config
│   └── k8s/                         # Kubernetes manifests (Phase 10)
│
├── monitoring/grafana/dashboards/   # MTTR, active incidents, agent decision dashboards
├── pyproject.toml                   # Makes shared/ installable; lists all dependencies
└── .env                             # Never committed — copy from .env.example
```

### Import Convention

All services import shared code like a library:
```python
from shared.models.alert import AlertEvent, Severity
from shared.models.incident import IncidentContext, Evidence, Action, RiskLevel
from shared.models.runbook import Runbook, PastIncident
from shared.config import settings
from shared.kafka_utils import KafkaProducer, KafkaConsumer
from shared.redis_client import RedisClient, init_redis
from shared.pg_client import PostgresClient
from shared.logger import get_logger, configure_logging
```

---

## 3. Data Models — Complete Reference

### 3.1 AlertEvent (`shared/models/alert.py`)

The canonical normalized alert. Every alert source is normalized into this shape at the ingestor boundary.

```
AlertEvent
├── alert_id: str              — uuid4, auto-generated
├── tenant_id: str             — set by middleware, NEVER trusted from client body
├── source: AlertSource        — prometheus|grafana|pagerduty|datadog|webhook|manual
├── status: AlertStatus        — firing|resolved|silenced
├── severity: Severity|None    — None until triage agent sets it
├── name: str                  — "HighErrorRate", "PodCrashLooping"
├── service: str               — "payment-service", "order-api"
├── environment: str           — default "production"
├── labels: dict[str,str]      — {"env":"prod","team":"payments"}
├── annotations: AlertAnnotation|None
│   ├── summary: str
│   ├── description: str
│   └── runbook_url: str
├── raw_payload: dict          — original source JSON preserved for debugging
├── fired_at: datetime
├── received_at: datetime
└── trace_id: str|None         — OpenTelemetry trace ID (Phase 8)

Methods:
  fingerprint() → str          — "{name}:{service}:{environment}" — used for Redis dedup
```

**Severity enum:**
- `P1` — production down, revenue impact, all hands
- `P2` — degraded, some users affected, on-call responds
- `P3` — warning, no user impact yet, business hours
- `P4` — informational, no action needed

### 3.2 IncidentContext (`shared/models/incident.py`)

The central object that flows through the entire pipeline. Each agent reads it, enriches it, and publishes the enriched version to the next Kafka topic.

```
IncidentContext
├── incident_id: str           — uuid4, auto-generated
├── tenant_id: str             — forwarded from AlertEvent, never changed after creation
├── status: IncidentStatus     — triaging|diagnosing|remediating|awaiting_approval|resolved|escalated
├── alert: AlertEvent          — the original alert (never mutated)
│
├── [SET BY TRIAGE AGENT]
│   ├── severity: Severity|None
│   ├── triage_summary: str
│   ├── matched_runbook_id: str|None
│   ├── similar_incident_ids: list[str]
│   ├── triage_confidence: float      — 0.0–1.0
│   └── triaged_at: datetime|None
│
├── [SET BY DIAGNOSIS AGENT]
│   ├── root_cause: str
│   ├── affected_services: list[str]
│   ├── affected_components: list[str]
│   ├── evidence: list[Evidence]
│   ├── diagnosis_summary: str
│   └── diagnosed_at: datetime|None
│
├── [SET BY REMEDIATION AGENT]
│   ├── remediation_plan: list[Action]
│   ├── remediation_summary: str
│   ├── human_approved: bool
│   ├── approved_by: str|None
│   ├── resolved_at: datetime|None
│   └── resolution_summary: str
│
├── created_at: datetime
├── updated_at: datetime
└── trace_id: str|None

Methods:
  mttr_seconds() → int|None    — (resolved_at - created_at).total_seconds()
  add_evidence(source, tool, content, relevance) → None
```

### 3.3 Evidence (`shared/models/incident.py`)

A single piece of diagnostic data collected by the diagnosis agent via an MCP tool call.

```
Evidence
├── source: str          — "k8s-mcp", "logs-mcp", "metrics-mcp", "db-mcp"
├── tool_name: str       — "get_pod_logs", "search_logs", "query_prometheus"
├── content: str         — actual data (log lines, JSON, metric values)
├── relevance: str       — agent's explanation of why this matters for the root cause
└── collected_at: datetime
```

### 3.4 Action (`shared/models/incident.py`)

A single remediation step planned by the remediation agent. Stored before execution for human review.

```
Action
├── action_id: str       — uuid4
├── tool: str            — "remediation-mcp"
├── tool_fn: str         — "restart_pod", "scale_deployment", "rollback_deployment"
├── parameters: dict     — {"namespace":"prod","deployment":"payment-service","replicas":3}
├── risk_level: RiskLevel — low|medium|high
├── reasoning: str       — why the agent thinks this will resolve the incident
├── executed: bool       — False until approval + execution
├── result: str|None     — output after execution
└── executed_at: datetime|None
```

**RiskLevel routing:**
- `low` → auto-execute immediately, notify after
- `medium` → Slack approval required, 5-minute timeout, then escalate
- `high` → Slack + PagerDuty page, no timeout, human must respond

### 3.5 Runbook (`shared/models/runbook.py`)

Human-written or auto-generated procedure. Stored with a 1536-dim embedding in Postgres for similarity search.

```
Runbook
├── runbook_id: str
├── title: str
├── description: str           — embedded and searched via pgvector
├── services: list[str]        — which services this applies to
├── alert_names: list[str]     — alert names this handles
├── severity: Severity
├── steps: list[RunbookStep]
│   └── RunbookStep
│       ├── order: int
│       ├── description: str
│       ├── command: str|None
│       └── automated: bool    — can the agent execute this step autonomously?
└── tags: list[str]
```

### 3.6 PastIncident (`shared/models/runbook.py`)

A resolved incident returned from pgvector similarity search. Used by triage agent as few-shot context.

```
PastIncident
├── incident_id: str
├── alert_name: str
├── service: str
├── root_cause: str
├── resolution: str
├── mttr_seconds: int
├── severity: Severity
├── resolved_at: datetime
└── similarity_score: float    — cosine similarity from pgvector (0.0–1.0)
```

---

## 4. Kafka Topics — Complete Reference

| Topic | Producer | Consumer(s) | Message type | Purpose |
|---|---|---|---|---|
| `alerts.raw` | alert-ingestor | triage-agent | AlertEvent | Raw normalized alerts from all sources |
| `alerts.triaged` | triage-agent | diagnosis-agent | IncidentContext | Alerts with severity + runbook assigned |
| `incidents.active` | diagnosis-agent | remediation-agent | IncidentContext | Incidents with root cause + evidence |
| `audit.events` | all agents | audit-consumer | dict (event) | Every agent action — fan-out to Postgres + Slack |
| `incidents.resolved` | remediation-agent | learning-loop | IncidentContext | Resolved incidents for embedding + learning |

**Kafka is NOT used for:**
- Agent → MCP tool calls (those are direct HTTP, synchronous)
- Any operation where the agent needs the result before continuing

**Kafka IS used for:**
- Alert ingestion (bursty, push-based, needs buffering)
- Agent pipeline handoffs (durable, replayable on failure)
- Audit event fan-out (multiple consumers, zero coupling)
- Learning loop triggering (async, non-blocking)

**Key settings:**
```
num_partitions: 4          # 4 parallel consumers per topic
auto_offset_reset: earliest
enable_auto_commit: false  # manual commit AFTER processing only
max_poll_interval_ms: 300000  # 5 min — allows slow LLM calls
acks: all                  # durability — all replicas must confirm
```

---

## 5. MCP Tool Servers — Complete Reference

Each MCP server is a FastAPI app using the `fastmcp` library. Claude calls tools via HTTP to these servers.

### 5.1 k8s-mcp (Port 8001) — Read-only Kubernetes

```
Tools exposed:
  get_pod_status(namespace, service)
    → list of pod names, status, restarts, age

  get_pod_logs(namespace, pod_name, tail=100, previous=False)
    → last N log lines (previous=True gets logs from crashed container)

  get_recent_events(namespace, service, minutes=30)
    → Kubernetes events filtered by service label

  get_deployment_history(namespace, deployment)
    → last 10 rollout revisions with timestamps

  describe_service(namespace, service)
    → full kubectl describe output

  get_resource_usage(namespace, service)
    → CPU/memory requests, limits, actual usage
```

### 5.2 db-mcp (Port 8002) — Read-only Database Diagnostics

```
Tools exposed:
  get_connection_count(db_name)
    → current connections by state (active/idle/idle_in_transaction)

  get_slow_queries(threshold_ms=1000, limit=10)
    → queries exceeding threshold with execution time + query text

  get_replication_lag()
    → replication delay in seconds for each replica

  check_table_bloat(schema="public")
    → tables with >20% dead tuple ratio (need VACUUM)

  get_recent_errors(minutes=30)
    → error log entries from pg_log

  get_lock_waits()
    → queries blocked waiting for locks
```

### 5.3 logs-mcp (Port 8003) — Log Search (Loki / CloudWatch)

```
Tools exposed:
  search_logs(service, query, minutes=30, limit=100)
    → log lines matching query string for service

  get_error_rate(service, minutes=30)
    → errors per minute time series

  get_stack_traces(service, minutes=30)
    → exception stack traces grouped by type

  correlate_by_trace_id(trace_id)
    → all log lines across all services sharing this trace_id

  get_log_volume(service, minutes=60)
    → log lines per minute — spike detection
```

### 5.4 metrics-mcp (Port 8004) — Prometheus Queries

```
Tools exposed:
  query_prometheus(promql, time_range_minutes=30)
    → raw PromQL result as JSON

  get_service_latency(service, percentile=99)
    → p50/p95/p99 latency over time

  get_error_rate_history(service, minutes=60)
    → error rate % over time

  get_saturation_metrics(service)
    → CPU%, memory%, queue depth

  get_sli_status(service)
    → current SLI vs SLO target
```

### 5.5 remediation-mcp (Port 8005) — Write Tools (requires approval for medium/high risk)

```
Tools exposed:
  restart_pod(namespace, pod_name)                           — risk: LOW
    → deletes pod, k8s recreates it

  restart_deployment(namespace, deployment)                  — risk: MEDIUM
    → kubectl rollout restart

  scale_deployment(namespace, deployment, replicas)          — risk: MEDIUM
    → kubectl scale

  rollback_deployment(namespace, deployment, revision=None)  — risk: HIGH
    → kubectl rollout undo

  toggle_feature_flag(flag_name, enabled)                    — risk: MEDIUM
    → updates feature flag in config store

  clear_cache(service, cache_type)                           — risk: LOW
    → flushes Redis cache for service

  drain_node(node_name)                                      — risk: HIGH
    → cordons and drains a Kubernetes node
```

---

## 6. Agent System — How Each Agent Works

### 6.1 Triage Agent

**Consumes:** `alerts.raw`
**Publishes:** `alerts.triaged`, `audit.events`
**MCP servers:** None (read-only, uses pgvector)

**Algorithm:**
1. Receive AlertEvent from Kafka
2. Check Redis for duplicate (fingerprint-based, 10-min TTL)
3. Generate embedding of `"{alert.name} {alert.service} {alert.annotations.summary}"`
4. pgvector similarity search → top-3 matching runbooks + top-3 past incidents
5. Call Claude with: alert details + runbook context + past incident context
6. Claude outputs: severity (P1-P4), triage_summary, confidence score
7. Populate `IncidentContext` with triage fields, set status = `diagnosing`
8. Publish enriched IncidentContext to `alerts.triaged`
9. Publish audit event: `{agent:"triage", action:"alert_triaged", severity, confidence}`

**Claude system prompt pattern:**
```
You are an SRE triage agent. You receive production alerts and must:
1. Determine severity (P1=production down, P2=degraded, P3=warning, P4=info)
2. Based on the alert details and similar past incidents provided, summarize what is likely happening
3. Output your assessment as JSON: {"severity":"P1","summary":"...","confidence":0.85}

Relevant runbooks: {runbook_context}
Similar past incidents: {past_incidents}
```

### 6.2 Diagnosis Agent

**Consumes:** `alerts.triaged` (P1 and P2 only — P3/P4 auto-resolved)
**Publishes:** `incidents.active`, `audit.events`
**MCP servers:** k8s-mcp, db-mcp, logs-mcp, metrics-mcp (READ ONLY)

**Algorithm:**
1. Receive IncidentContext from Kafka
2. Set status = `diagnosing`
3. Call Claude with full incident context + available MCP tools
4. Claude runs agentic loop: calls MCP tools, examines results, forms hypothesis, calls more tools
5. Each MCP tool result → add_evidence() on IncidentContext
6. Claude outputs: root_cause, affected_services, diagnosis_summary
7. Populate IncidentContext diagnosis fields, set status = `remediating`
8. Publish to `incidents.active`

**Agentic loop pattern:**
```python
response = client.beta.messages.create(
    model=settings.anthropic_model,
    max_tokens=4096,
    system=DIAGNOSIS_SYSTEM_PROMPT,
    messages=[{"role":"user","content": incident_prompt}],
    mcp_servers=[
        {"type":"url","url":settings.mcp_k8s_url},
        {"type":"url","url":settings.mcp_db_url},
        {"type":"url","url":settings.mcp_logs_url},
        {"type":"url","url":settings.mcp_metrics_url},
    ],
)
# Claude will call tools multiple times until it has enough evidence
# Each tool_use block in response.content = one MCP tool call
```

### 6.3 Remediation Agent

**Consumes:** `incidents.active`
**Publishes:** `incidents.resolved`, `audit.events`
**MCP servers:** remediation-mcp (WRITE — gated by risk level)

**Algorithm:**
1. Receive IncidentContext (has root_cause + evidence)
2. Set status = `remediating`
3. Call Claude to generate remediation_plan (list of Actions) — NO tool execution yet
4. For each Action:
   - `risk=LOW` → execute immediately via remediation-mcp
   - `risk=MEDIUM` → post Slack approval request, wait up to 5 min
   - `risk=HIGH` → post Slack + page PagerDuty, wait indefinitely
5. On approval: execute Action via MCP, record result
6. On rejection: escalate to human, set status = `escalated`
7. After all actions: set status = `resolved`, populate resolution_summary
8. Publish to `incidents.resolved`

**Human approval gate (Slack):**
```
🔴 P1 Incident — payment-service: connection pool exhausted
Root cause: 847 idle connections leaking from order-service
Proposed fix: restart payment-service deployment (risk: MEDIUM)
Expected downtime: <10 seconds (rolling restart)
Similar past: Oct 14 — same fix resolved in 4 min

[✅ Approve]  [❌ Reject]  [🔍 Show full RCA]
```

---

## 7. Multi-Tenancy — Level 1 Logical Isolation

**Architecture decision:** Level 1 logical isolation — the standard SaaS pattern used by Stripe, Slack, and Datadog for 10–10,000 tenants. Every data access is filtered by `tenant_id`.

| Level | Isolation Method | Operational Cost | Best For | Used Here? |
|---|---|---|---|---|
| **Level 1** | tenant_id column + query filtering | Low — one schema, simple backups | 10–10,000 tenants. Standard SaaS. | ✅ Yes |
| Level 2 | Separate Postgres schema per tenant | High — 50+ schemas, complex migrations | 5–50 enterprise customers, data residency | — |
| Level 3 | Separate DB instance per tenant | Very high — 100+ instances | Enterprise-only (<20 customers) | — |

### tenant_id Propagation — Every Layer

**1. JWT — Single Source of Truth**
Every user receives a JWT signed by the auth service. The token payload contains `tenant_id` and `role`. No other source of truth.
```json
{"sub": "user_123", "tenant_id": "acme_corp", "role": "engineer", "exp": 1234567890}
```

**2. FastAPI Middleware — Enforcement at Request Boundary**
Every service has a dependency function that decodes the JWT and injects `tenant_id`. No endpoint can be called without this.
```python
async def extract_tenant(request: Request) -> str:
    token = request.headers["Authorization"].split()[1]
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    request.state.tenant_id = payload["tenant_id"]
    return payload["tenant_id"]

@app.post("/alerts/manual")
async def create_alert(alert: AlertEvent, tid: str = Depends(extract_tenant)):
    alert.tenant_id = tid  # enforced at boundary, not trusted from client
```

**3. Kafka — tenant_id in Key, Headers, and Payload**
The Kafka message key is `{tenant_id}:{incident_id}`. This guarantees all messages for one tenant's incident go to the same partition. The header carries `tenant_id` for fast filtering without deserializing.
```python
producer.publish(
    topic="alerts.raw",
    value=incident.model_dump(),          # payload contains tenant_id
    key=f"{tenant_id}:{incident_id}",     # partition routing
    headers={"tenant_id": tenant_id, "trace_id": trace_id},
)
```

**4. PostgreSQL — WHERE tenant_id = :tenant_id on Every Query**
Every SELECT, UPDATE, and DELETE includes a `tenant_id` filter. A missing WHERE clause would return cross-tenant data.
```python
async def get_incident(self, incident_id: str, tenant_id: str):
    result = await session.execute(text("""
        SELECT * FROM incidents
        WHERE incident_id = :incident_id
          AND tenant_id   = :tenant_id   -- ALWAYS present
    """), {"incident_id": incident_id, "tenant_id": tenant_id})
```

**5. Redis — {tenant_id}: Key Prefix**
All Redis keys are prefixed with `tenant_id`. Without this, a cache entry for one tenant could be served to another.
```python
key = f"alert:dedup:{tenant_id}:{alert.fingerprint()}"    # dedup
key = f"incident:status:{tenant_id}:{incident_id}"        # state
key = f"approval:token:{tenant_id}:{request_id}"          # approvals
```

**6. Structured Logs — tenant_id in Every Log Line**
Every log line includes `tenant_id` for audit and debugging.

### Defense in Depth — 7 Enforcement Layers

| Layer | How Enforced | Failure Mode if Missing |
|---|---|---|
| Middleware | JWT decoded, tenant_id extracted on every request | 401 Unauthorized — request rejected |
| API input | `alert.tenant_id = extracted_tenant_id` (not from body) | Cannot spoof tenant via request body |
| Kafka key | `{tenant_id}:{incident_id}` | Messages mixed between tenants |
| Kafka headers | `tenant_id` header in every message | Consumer can't filter without full deserialization |
| DB queries | `WHERE tenant_id = :tenant_id` | Returns cross-tenant data silently |
| Redis keys | `{tenant_id}:` prefix on all keys | Cache collision — wrong tenant gets cached data |
| Structured logs | `tenant_id` field in every log line | Audit trail is unusable for investigation |

### Migration Path — Level 1 to Level 2 (on-demand)

If a customer demands data residency (e.g., GDPR EU-only storage), migrate them to their own schema:
```sql
CREATE SCHEMA tenant_acme;
CREATE TABLE tenant_acme.incidents AS SELECT * FROM incidents WHERE tenant_id = 'acme_corp';
-- Route acme's JWT to the new schema in settings
TENANT_SCHEMA_MAP = { 'acme_corp': 'tenant_acme', 'default': 'public' }
DELETE FROM incidents WHERE tenant_id = 'acme_corp';
-- All other tenants: zero impact, zero downtime
```

---

## 8. Infrastructure — Ports and Services

| Service | Port | Purpose | Health check |
|---|---|---|---|
| Kafka (internal) | 9092 | Docker network communication | kafka-broker-api-versions |
| Kafka (external) | 29092 | Local machine access | same |
| Zookeeper | 2181 | Kafka control plane | echo ruok |
| Schema Registry | 8081 | Message schema validation | HTTP /subjects |
| Kafka UI | 8080 | Visual broker management | HTTP |
| Redis | 6379 | Dedup, state, approval tokens | redis-cli ping |
| Postgres | 5432 | Incidents, runbooks, audit log | pg_isready |
| Prometheus | 9090 | Metrics scraping | HTTP /-/healthy |
| Grafana | 3000 | Dashboards | HTTP /api/health |
| alert-ingestor | 8000 | Receives external alerts | /health |
| k8s-mcp | 8001 | Kubernetes MCP tools | /health |
| db-mcp | 8002 | Database MCP tools | /health |
| logs-mcp | 8003 | Log search MCP tools | /health |
| metrics-mcp | 8004 | Prometheus MCP tools | /health |
| remediation-mcp | 8005 | Remediation MCP tools | /health |
| dashboard | 8006 | Live incident dashboard | /health |

---

## 9. Database Schema

### incidents
```sql
incident_id TEXT UNIQUE NOT NULL          -- primary key (uuid)
tenant_id TEXT NOT NULL                   -- logical isolation — EVERY query filters on this
status TEXT NOT NULL DEFAULT 'triaging'   -- triaging|diagnosing|remediating|resolved|escalated
alert_name TEXT NOT NULL
service TEXT NOT NULL
environment TEXT NOT NULL DEFAULT 'production'
severity TEXT                              -- P1|P2|P3|P4
root_cause TEXT
resolution_summary TEXT
mttr_seconds INTEGER
trace_id TEXT
raw_context JSONB NOT NULL DEFAULT '{}'   -- full IncidentContext JSON
embedding vector(1536)                    -- pgvector — populated after resolution
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
resolved_at TIMESTAMPTZ
```

### runbooks
```sql
runbook_id TEXT UNIQUE NOT NULL
tenant_id TEXT NOT NULL                    -- runbooks are tenant-scoped
title TEXT NOT NULL
description TEXT NOT NULL
services TEXT[]                            -- array of applicable service names
alert_names TEXT[]                         -- array of applicable alert names
severity TEXT NOT NULL
steps JSONB NOT NULL DEFAULT '[]'
tags TEXT[]
embedding vector(1536)                     -- pgvector — populated on creation
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

### audit_events
```sql
event_id TEXT UNIQUE NOT NULL
incident_id TEXT → incidents(incident_id)
agent TEXT NOT NULL                        -- 'triage'|'diagnosis'|'remediation'|'system'
action TEXT NOT NULL                       -- 'alert_received'|'runbook_found'|'pod_restarted'
details JSONB NOT NULL DEFAULT '{}'
trace_id TEXT
created_at TIMESTAMPTZ
```

### approval_requests
```sql
request_id TEXT UNIQUE NOT NULL
incident_id TEXT → incidents(incident_id)
action JSONB NOT NULL                      -- the Action object
risk_level TEXT NOT NULL
slack_ts TEXT                              -- Slack message timestamp for updating the message
status TEXT DEFAULT 'pending'             -- pending|approved|rejected|expired
approved_by TEXT                           -- Slack user ID
expires_at TIMESTAMPTZ
created_at TIMESTAMPTZ
resolved_at TIMESTAMPTZ
```

**pgvector indexes (HNSW):**
```sql
CREATE INDEX idx_incidents_embedding ON incidents
    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128);

CREATE INDEX idx_runbooks_embedding ON runbooks
    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128);
```

**Tenant isolation indexes:**
```sql
CREATE INDEX idx_incidents_tenant ON incidents(tenant_id);
CREATE INDEX idx_runbooks_tenant ON runbooks(tenant_id);
```

---

## 10. Configuration — All Environment Variables

Defined in `shared/config.py` as pydantic BaseSettings. All services import `settings`.

```env
# Required — startup fails without this
ANTHROPIC_API_KEY=sk-ant-...

# Kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:29092       # Use kafka:9092 inside Docker
KAFKA_SCHEMA_REGISTRY_URL=http://localhost:8081

# Topic names — always use settings.topic_alerts_raw, never hardcode
TOPIC_ALERTS_RAW=alerts.raw
TOPIC_ALERTS_TRIAGED=alerts.triaged
TOPIC_INCIDENTS_ACTIVE=incidents.active
TOPIC_AUDIT_EVENTS=audit.events
TOPIC_INCIDENTS_RESOLVED=incidents.resolved

# Redis
REDIS_URL=redis://localhost:6379/0

# Postgres — uses asyncpg driver
POSTGRES_URL=postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db

# MCP servers — use service names inside Docker
MCP_K8S_URL=http://localhost:8001/mcp
MCP_DB_URL=http://localhost:8002/mcp
MCP_LOGS_URL=http://localhost:8003/mcp
MCP_METRICS_URL=http://localhost:8004/mcp
MCP_REMEDIATION_URL=http://localhost:8005/mcp

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_INCIDENTS_CHANNEL=#incidents
SLACK_APPROVALS_CHANNEL=#incident-approvals

# App
ENVIRONMENT=development          # development|staging|production
LOG_LEVEL=INFO                   # DEBUG|INFO|WARNING|ERROR
SERVICE_NAME=unknown             # set per-service in each Dockerfile
ANTHROPIC_MODEL=claude-opus-4-5

# Multi-tenancy
JWT_SECRET_KEY=your-secret-key   # for dev/testing — production uses a vault
DEFAULT_TENANT_ID=default        # used for dev/testing when no JWT is present
```

---

## 11. Redis Key Patterns

All Redis keys used in the platform — never use raw strings, always go through RedisClient methods.
**All keys include `tenant_id` prefix to prevent cross-tenant cache collisions.**

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `alert:dedup:{tenant_id}:{fingerprint}` | string | 10 min | Alert deduplication |
| `incident:status:{tenant_id}:{incident_id}` | string | 24 hr | Current incident status |
| `incident:context:{tenant_id}:{incident_id}` | string (JSON) | 24 hr | Full IncidentContext cache |
| `approval:token:{tenant_id}:{request_id}` | string | per risk level | Approval gate token |
| `session:incident:{session_id}` | set | 24 hr | Incident IDs in session |

---

## 12. Logging Conventions

All services use `structlog` via `shared/logger.py`. Every log line must include:

```python
log = get_logger("triage-agent")

# CORRECT — structured fields (always include tenant_id)
log.info("alert_triaged",
    tenant_id=tenant_id,
    alert_id=alert.alert_id,
    incident_id=incident.incident_id,
    severity=severity,
    confidence=confidence,
    runbook_matched=bool(runbook_id),
    duration_ms=duration_ms,
    trace_id=trace_id,
)

# WRONG — unstructured string
log.info(f"Alert {alert_id} triaged with severity {severity}")
```

**Standard field names (use these consistently):**
- `tenant_id` — tenant that owns this data — ALWAYS PRESENT
- `alert_id` — AlertEvent.alert_id
- `incident_id` — IncidentContext.incident_id
- `service` — the service the alert is about
- `severity` — P1/P2/P3/P4
- `trace_id` — OpenTelemetry trace ID
- `duration_ms` — operation duration in milliseconds
- `agent` — which agent produced the log ("triage", "diagnosis", "remediation")

---

## 13. Build Phases

| Phase | What gets built | Key learning |
|---|---|---|
| 1 | Monorepo scaffold, Docker Compose, shared models | Pydantic, pgvector schema, Kafka config |
| 2 | Alert ingestor — FastAPI + Kafka producer | Alert normalization, Redis dedup |
| 3 | Triage agent — Claude + pgvector retrieval | Embeddings, similarity search, agentic calls |
| 4 | 4 diagnostic MCP servers | fastmcp, k8s SDK, async HTTP |
| 5 | Diagnosis agent — multi-turn MCP tool loop | Agentic loops, evidence collection |
| 6 | Remediation agent + approval gate | Risk classification, Slack interactivity |
| 7 | Audit consumer — Postgres + Slack notifier | Fan-out consumers, Slack Bolt |
| 8 | Learning loop — postmortem → embeddings | Embedding generation, pgvector upsert |
| 9 | OpenTelemetry — trace propagation in Kafka | OTel SDK, context propagation |
| 10 | Dashboard + CI/CD + Kubernetes | WebSocket, GitHub Actions, K8s HPA |

---

## 14. Design Decisions — Why Each Technology

| Decision | Chosen | Rejected | Reason |
|---|---|---|---|
| Tool calling | MCP (direct HTTP) | Kafka for tools | Agent needs synchronous results to reason; Kafka adds latency with no benefit |
| Alert ingestion | Kafka | Direct HTTP/polling | Alerts are bursty push events; Kafka absorbs spikes and provides durability |
| Vector search | Postgres + pgvector | Pinecone/Weaviate | Avoid separate infra; join with relational data in one query |
| Three agents | Triage/Diagnosis/Remediation | One agent | Separation of permissions; failure isolation; audit clarity |
| Manual offset commit | Yes | Auto-commit | Prevents lost alerts if agent crashes mid-processing |
| Human approval gate | Risk-level based | Always manual / Always auto | Balance speed vs safety; low-risk auto-execute, high-risk always manual |
| Multi-tenancy | Level 1 (tenant_id column) | Level 2 (schema-per-tenant) / Level 3 (DB-per-tenant) | Low operational cost; sufficient for 10-10K tenants; one schema, simple backups; migration to Level 2 on-demand |
| asyncpg driver | Yes | psycopg2 | Native async; 3-5x faster than sync driver in async context |
| structlog | Yes | logging stdlib | Structured JSON output; consistent fields across all services |

---

## 15. Common Mistakes to Avoid

1. **Never hardcode topic names** — always use `settings.topic_alerts_raw` etc.
2. **Never auto-commit Kafka offsets** — always `enable.auto.commit: false`
3. **Never call remediation-mcp without checking risk level first**
4. **Never run pgvector queries without HNSW index** — exact search is O(n), too slow
5. **Never use sync Redis/Postgres clients in async services** — blocks the event loop
6. **Never commit to an offset before the handler completes successfully**
7. **Never skip the alert fingerprint dedup** — flapping alerts will spawn duplicate incidents
8. **Never expose remediation-mcp to the diagnosis agent** — separation of permissions
9. **Always include trace_id in every log line, Kafka message header, and DB record**
10. **Always validate AlertEvent at the ingestor boundary** — downstream services trust the shape
11. **Never omit `tenant_id` from a Postgres WHERE clause** — a missing filter returns cross-tenant data silently
12. **Never trust `tenant_id` from the request body** — always extract from JWT via middleware
13. **Always prefix Redis keys with `tenant_id`** — prevents cache collisions between tenants
14. **Always include `tenant_id` in Kafka message keys and headers** — ensures partition isolation and fast filtering

---

## 16. Interview Talking Points

When asked about this project, lead with the business problem, then justify each technology:

**Opening:**
"Production incidents cost an average of $5,600 per minute. I built a platform that reduces MTTR from 45 minutes to ~8 minutes by having three specialized AI agents autonomously handle the full incident lifecycle. The system learns from every resolved incident via pgvector embeddings, is multi-tenant from day one with tenant_id flowing through every layer, and routes risky remediation actions through human approval."

**On Kafka:**
"I used Kafka for alert ingestion because alerts are bursty push events — Prometheus fires 50 alerts when a deployment goes wrong. Kafka absorbs the burst, provides durability (no dropped alerts if the agent restarts), and decouples the alert sources from the processing. Critically, I did NOT use Kafka for tool calls — the agents need synchronous results to reason, so MCP tools are direct HTTP calls."

**On the three-agent architecture:**
"I separated into three agents — triage, diagnosis, remediation — primarily for permission isolation. The diagnosis agent only has read access to k8s, logs, and metrics. The remediation agent has write access — but only after human approval for medium and high risk actions. A single agent would require write access during diagnosis, which violates least privilege."

**On pgvector:**
"The system learns from every resolved incident. After resolution, the learning loop generates an embedding of the alert + root cause and stores it in Postgres with pgvector. Next time a similar alert fires, the triage agent retrieves the 3 most similar past incidents and says 'we saw this in October, root cause was connection pool exhaustion, fixed by restarting the app server in 4 minutes.' The agent gets smarter over time without any retraining."

**On the human approval gate:**
"An AI agent with unrestricted write access to production infrastructure is dangerous. I implemented a risk classifier: low-risk actions (restart a pod, clear cache) auto-execute with post-hoc notification. Medium-risk actions (scale deployment, config change) require Slack approval with a 5-minute timeout. High-risk actions (rollback, drain node) require explicit human approval with no timeout — I'd rather have a slower resolution than an autonomous bad rollback."

**On multi-tenancy:**
"It's multi-tenant from day one — Level 1 logical isolation, the same pattern Stripe and Datadog use. tenant_id flows from the JWT through FastAPI middleware, into Kafka partition keys and headers, every Postgres query has WHERE tenant_id = ?, and Redis keys are prefixed with tenant_id. Defense in depth at 7 layers. If a customer needs data residency compliance, I migrate them to their own Postgres schema on-demand without touching any other tenant. But at 10–10,000 customers, Level 1 is the right choice."
