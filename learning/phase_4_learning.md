# Phase 4 Learning: Diagnostic MCP Servers & Tenant Credential System

## 1. What We Built in Phase 4

Phase 4 introduced two major systems:

1. **Four Model Context Protocol (MCP) Servers** — standalone microservices that expose diagnostic tools over HTTP. These are the "eyes and ears" that the Diagnosis Agent (Phase 5) will use to investigate incidents autonomously.
2. **A Tenant Credential Management System** — a full CRUD API + PostgreSQL-backed store that allows each tenant to register their own Kubernetes cluster, Prometheus instance, Loki endpoint, and Database connection. The MCP servers dynamically load these credentials per-tenant before making any API call.

### Files & Folders Created

| File | Purpose |
|------|---------|
| **Credential System** | |
| `shared/models/credentials.py` | Pydantic models defining the shape of each credential type (K8s, Prometheus, Loki, Database) |
| `shared/credential_store.py` | PostgreSQL CRUD operations — `save_credentials()`, `get_credentials()`, `delete_credentials()` |
| `services/alert-ingestor/routes/credentials.py` | FastAPI REST API — tenants call `PUT /credentials/kubernetes` etc. to register their infrastructure |
| **MCP Servers** | |
| `services/mcp-servers/k8s/main.py` | Kubernetes diagnostics via the official `kubernetes` Python client |
| `services/mcp-servers/db/main.py` | PostgreSQL diagnostics via raw `asyncpg` queries against `pg_stat_activity` |
| `services/mcp-servers/logs/main.py` | Log search via Grafana Loki's HTTP API (LogQL queries) |
| `services/mcp-servers/metrics/main.py` | Metrics via Prometheus HTTP API (PromQL queries) |
| `services/mcp-servers/*/Dockerfile` | Container definitions for each MCP server |
| **Infrastructure** | |
| `infra/loki/loki-config.yaml` | Loki server configuration — filesystem storage, no auth for local dev |
| `infra/loki/promtail-config.yaml` | Promtail agent config — auto-discovers Docker containers and ships their logs to Loki |
| `infra/docker-compose.yml` (modified) | Added Loki + Promtail services |
| `infra/docker-compose.override.yml` | Docker Compose definitions for the 4 MCP servers |
| `infra/postgres/init.sql` (modified) | Added `tenant_credentials` table |

---

## 2. Why We Built It This Way

### Why MCP Instead of Hardcoded Functions?

In Phase 3, the Triage Agent's logic was self-contained — it queried pgvector and called Claude, all within one Python process. But the Diagnosis Agent (Phase 5) needs to do something fundamentally different: it needs to **interact with external infrastructure** (Kubernetes, databases, log systems, metrics).

We could have written all those API calls directly inside the Diagnosis Agent. But that creates several problems:

1. **Permission Explosion**: The Diagnosis Agent would need credentials for K8s, Postgres, Loki, AND Prometheus all in one process. If the agent gets compromised, the attacker has access to everything.
2. **Tight Coupling**: Every time you add a new diagnostic source (e.g., AWS CloudWatch, Datadog), you'd need to modify the agent code itself.
3. **No Reusability**: Other agents (or humans) couldn't use those same diagnostic tools.

**MCP solves all three.** Each tool server runs as an independent process with its own permissions. The Diagnosis Agent simply tells Claude "here are your available tools at these URLs" and Claude calls them via HTTP. Adding a new diagnostic source = deploying a new MCP server. Zero changes to the agent.

### Why Tenant Credentials Instead of Global Config?

In Phase 2, we made the platform multi-tenant — each tenant's data is isolated by `tenant_id`. But what about their *infrastructure*? Tenant A might run their services on GKE (Google Kubernetes), while Tenant B uses EKS (Amazon). Their Prometheus instances are at completely different URLs with different auth tokens.

If we stored one global Prometheus URL in `shared/config.py`, we could only monitor one organization's infrastructure. That defeats the entire purpose of multi-tenancy.

**The Credential Store pattern** solves this:
1. Tenant registers their infrastructure endpoints via REST API: `PUT /credentials/prometheus`
2. Credentials are stored in PostgreSQL's `tenant_credentials` table as JSONB
3. When an MCP tool is invoked, it receives `tenant_id`, loads credentials from the store, and connects to *that tenant's* Prometheus/K8s/Loki
4. If no credentials exist, it falls back to the platform's local infrastructure (useful for development)

### Why Loki for Logs?

We needed a real log aggregation backend for the `logs-mcp` server to query. The choices were:

| Option | Pros | Cons |
|--------|------|------|
| **Grafana Loki** ✅ | Lightweight, LogQL is similar to PromQL, native Grafana integration, free | Less powerful full-text search than Elasticsearch |
| Elasticsearch | Most powerful search, industry standard | Heavy (needs 4GB+ RAM), complex to operate |
| CloudWatch | AWS-native | Not self-hostable, vendor lock-in |

Loki was the right pick because:
- It integrates naturally with our existing Grafana and Prometheus stack
- **Promtail** (the log shipper) auto-discovers Docker containers via the Docker socket — zero configuration per service
- LogQL's syntax mirrors PromQL, so there's only one query language to learn

### Why `asyncpg` Instead of SQLAlchemy for DB MCP?

The `db-mcp` server runs diagnostic queries against PostgreSQL system catalogs (`pg_stat_activity`, `pg_locks`, `pg_stat_user_tables`). These are raw system-level queries that don't map to ORM models.

Using SQLAlchemy here would add unnecessary abstraction layers. `asyncpg` gives us:
- **Direct access** to PostgreSQL-specific features (system catalogs, `$1` parameterized queries)
- **Better performance** — no ORM overhead for simple diagnostic reads
- **Cleaner code** — the queries are straightforward SELECT statements

### Why Each Tool is `async`?

Every MCP tool function is `async` because:
1. **Credential loading** requires a database query (async I/O)
2. **Kubernetes API calls** may take 1-5 seconds over the network
3. **Prometheus/Loki queries** involve HTTP roundtrips
4. FastMCP's SSE transport is inherently async

If these were synchronous, a single slow Prometheus query would block ALL other tool calls on that server.

---

## 3. How It Works Under The Hood

### The Credential Flow (End-to-End)

```
Tenant  ──PUT /credentials/prometheus──▶  Alert Ingestor (FastAPI)
                                              │
                                              ▼
                                     shared/credential_store.py
                                              │
                                              ▼
                                     PostgreSQL: tenant_credentials table
                                     ┌─────────────────────────────────┐
                                     │ tenant_id: "acme_corp"          │
                                     │ credentials: {                  │
                                     │   "prometheus": {               │
                                     │     "base_url": "https://...",  │
                                     │     "auth_type": "bearer",      │
                                     │     "bearer_token": "sk-..."    │
                                     │   }                             │
                                     │ }                               │
                                     └─────────────────────────────────┘

Later, when Diagnosis Agent triggers a tool call:

Claude ──tool_use: query_prometheus(tenant_id="acme_corp", promql="up")──▶ metrics-mcp
    │
    ▼
_get_prometheus_client("acme_corp")
    │
    ├── get_credentials("acme_corp")  →  loads from PostgreSQL
    │
    ├── Finds prometheus.base_url = "https://prometheus.acme.com"
    │   Finds prometheus.bearer_token = "sk-..."
    │
    └── httpx.GET("https://prometheus.acme.com/api/v1/query?query=up",
                  headers={"Authorization": "Bearer sk-..."})
```

### How Each MCP Server Works

#### K8s MCP (Port 8001)

The `kubernetes` Python client mimics `kubectl` commands programmatically:

```python
# What kubectl does:           What our code does:
# kubectl get pods -l app=X    v1.list_namespaced_pod(namespace, label_selector="app=X")
# kubectl logs pod-name        v1.read_namespaced_pod_log(name, namespace, tail_lines=100)
# kubectl get events           v1.list_namespaced_event(namespace)
```

**Authentication**: The `_build_k8s_client()` function constructs a Kubernetes API client dynamically from the tenant's credentials. It supports three auth methods:
- **Token**: Service account bearer token (most common in production)
- **Kubeconfig**: Raw kubeconfig YAML (parses clusters, users, contexts)
- **In-cluster**: When the MCP server itself runs inside Kubernetes

#### DB MCP (Port 8002)

Runs real PostgreSQL diagnostic queries against the tenant's database:

```sql
-- get_connection_count: How many connections, grouped by state?
SELECT state, count(*) FROM pg_stat_activity WHERE datname = $1 GROUP BY state;

-- get_slow_queries: What queries are running too long?
SELECT pid, query FROM pg_stat_activity
WHERE state = 'active' AND EXTRACT(EPOCH FROM (NOW() - query_start)) * 1000 > $1;

-- get_lock_waits: Are any queries deadlocked?
SELECT blocked.pid, blocked.query, blocking.pid, blocking.query
FROM pg_stat_activity blocked
JOIN pg_locks ... WHERE NOT blocked_locks.granted;

-- check_table_bloat: Which tables need VACUUM?
SELECT relname, ROUND(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1) AS dead_pct
FROM pg_stat_user_tables;
```

#### Logs MCP (Port 8003)

Translates diagnostic intent into LogQL queries against Loki:

```
search_logs("checkout", "timeout")  →  LogQL: {app="checkout"} |= `timeout`
get_error_rate("checkout")          →  LogQL: sum(count_over_time({app="checkout"} |= `ERROR` [30m]))
get_stack_traces("checkout")        →  LogQL: {app="checkout"} |~ `(?i)(traceback|exception|error)`
correlate_by_trace_id("abc-123")    →  LogQL: {} |= `abc-123`  (searches ALL services)
```

The `correlate_by_trace_id` tool is particularly powerful — it searches across every service's logs for a single trace ID, enabling distributed tracing without OpenTelemetry.

#### Metrics MCP (Port 8004)

Constructs real PromQL queries and hits Prometheus's `/api/v1/query` endpoint:

```python
# get_service_latency("payment-api", 99) generates:
promql = 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service="payment-api"}[5m])) by (le))'

# get_error_rate_history("payment-api") generates:
promql = '100 * sum(rate(http_requests_total{service="payment-api",status=~"5.."}[5m])) / sum(rate(http_requests_total{service="payment-api"}[5m]))'

# get_saturation_metrics("payment-api") runs 3 separate queries:
# CPU, Memory, and container restart counts
```

### How Promtail Auto-Discovers Containers

Promtail uses Docker service discovery (`docker_sd_configs`) to automatically find every running Docker container:

```yaml
scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock   # Reads the Docker socket
        refresh_interval: 5s                 # Re-scans every 5 seconds
    relabel_configs:
      - source_labels: ['__meta_docker_container_name']
        regex: '/(.*)'
        target_label: 'app'                  # Container name becomes the "app" label
```

This means when you spin up a new service container, Promtail **automatically** starts shipping its logs to Loki. Zero configuration needed per service.

### Security: Why Secrets Are Redacted on GET

When a tenant calls `GET /credentials/`, we return their configuration but **redact the secrets**:

```python
# What's stored in PostgreSQL:
{"bearer_token": "sk-ant-real-secret-key-here"}

# What GET /credentials/ returns:
{"bearer_token": "***REDACTED***"}
```

This prevents accidental token exposure in UIs, logs, or browser developer tools. The original secrets are only ever read internally by the MCP servers when they need to connect.

---

## 4. How to Test It

### Step 1: Start Infrastructure
```bash
cd infra
docker compose up -d
# This now starts: Kafka, Redis, Postgres, Prometheus, Grafana, Loki, Promtail
```

### Step 2: Start the Alert Ingestor (for credential API)
```bash
source venv/bin/activate
cd services/alert-ingestor
uvicorn main:app --port 8000
```

### Step 3: Register Your Infrastructure Credentials
```bash
# Register your Prometheus
curl -X PUT http://localhost:8000/credentials/prometheus \
  -H "Content-Type: application/json" \
  -d '{"base_url": "http://localhost:9090", "auth_type": "none"}'

# Register your Loki
curl -X PUT http://localhost:8000/credentials/loki \
  -H "Content-Type: application/json" \
  -d '{"base_url": "http://localhost:3100", "auth_type": "none"}'

# Register your Kubernetes (if you have Docker Desktop K8s enabled)
curl -X PUT http://localhost:8000/credentials/kubernetes \
  -H "Content-Type: application/json" \
  -d '{
    "api_server_url": "https://kubernetes.docker.internal:6443",
    "auth_type": "token",
    "token": "YOUR_SERVICE_ACCOUNT_TOKEN",
    "verify_ssl": false
  }'

# Verify what got saved (secrets are redacted)
curl http://localhost:8000/credentials/
```

### Step 4: Start the MCP Servers
```bash
# Terminal 2 — DB MCP
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/mcp-servers/db/main.py

# Terminal 3 — K8s MCP
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/mcp-servers/k8s/main.py

# Terminal 4 — Logs MCP
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/mcp-servers/logs/main.py

# Terminal 5 — Metrics MCP
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/mcp-servers/metrics/main.py
```

### Step 5: Verify They're Running
```bash
# Check all 4 ports are listening
lsof -i:8001 -i:8002 -i:8003 -i:8004 | grep LISTEN

# Check SSE endpoint responds
curl http://localhost:8001/sse    # Should return "event: endpoint"
curl http://localhost:8002/sse
curl http://localhost:8003/sse
curl http://localhost:8004/sse
```

### Step 6: Verify Live Data
```bash
# Test that Prometheus returns real data
curl -s "http://localhost:9090/api/v1/query?query=up" | python3 -m json.tool

# Test that Loki is healthy  
curl -s http://localhost:3100/ready
```

---

## 5. Key Concepts to Remember

### What is MCP (Model Context Protocol)?
MCP is a standard created by Anthropic that defines how AI models interact with external tools. Instead of hardcoding tool logic inside your agent, you expose tools as HTTP endpoints that Claude can call dynamically. Think of it as "USB for AI" — plug in any tool server and the AI can immediately use it.

### What is FastMCP?
`fastmcp` is a Python library that makes creating MCP servers trivial. You decorate a function with `@mcp.tool()` and it automatically:
- Generates the tool's JSON schema from the function signature
- Exposes it over Server-Sent Events (SSE) transport
- Handles the MCP protocol negotiation with Claude

### What is LogQL?
LogQL is Loki's query language, modeled after PromQL. Key patterns:
- `{app="service-name"}` — select log streams by label
- `|= "error"` — filter lines containing "error"
- `|~ "regex"` — filter lines matching a regex
- `count_over_time(...)` — count matching lines (for rate calculations)

### What is PromQL?
PromQL is Prometheus's query language for time-series metrics:
- `up` — which targets are currently healthy
- `rate(http_requests_total[5m])` — requests per second over 5 minutes
- `histogram_quantile(0.99, ...)` — p99 latency from histogram buckets

### Defense in Depth for Credentials
The credential system has 4 security layers:
1. **JWT Authentication**: Only authenticated tenants can register credentials
2. **Tenant Isolation**: `WHERE tenant_id = :tid` on every credential query
3. **Secret Redaction**: GET endpoint never returns raw tokens
4. **Least Privilege**: Each MCP server only loads the credential type it needs (K8s MCP only reads kubernetes credentials, never prometheus)
