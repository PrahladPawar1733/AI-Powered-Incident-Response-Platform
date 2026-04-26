# Phase 6 Learning: Remediation Agent & Human-in-the-Loop Approval

## 1. What We Built in Phase 6

Phase 6 completes the full autonomous incident response pipeline. We built two systems:

1. **Remediation MCP Server** — a FastMCP server with real Kubernetes write tools (restart pods, scale deployments, rollback) that execute against the tenant's cluster using their registered credentials.
2. **Remediation Agent** — a Kafka consumer that receives diagnosed incidents, uses Claude to generate a risk-assessed remediation plan, then executes it with a **human approval gate** for medium/high-risk actions.

Together with Phases 1-5, the full pipeline is now:
```
Alert → Ingest → Triage → Diagnose → Remediate → Resolved
```

### Files Created

| File | Purpose |
|------|---------|
| **Remediation MCP Server** | |
| `services/mcp-servers/remediation/main.py` | 7 write tools: restart_pod, clear_cache, restart_deployment, scale_deployment, toggle_feature_flag, rollback_deployment, drain_node |
| `services/mcp-servers/remediation/Dockerfile` | Container definition |
| **Remediation Agent** | |
| `services/remediation-agent/config.py` | System prompt instructing Claude to generate remediation plans with risk levels |
| `services/remediation-agent/approval.py` | Redis-based approval gate — request, poll, expire flow |
| `services/remediation-agent/agent.py` | Core logic: plan generation → risk-based execution → resolution |
| `services/remediation-agent/main.py` | Kafka consumer consuming `incidents.active` |
| `services/remediation-agent/Dockerfile` | Container definition |
| **Approval API** | |
| `services/alert-ingestor/routes/approvals.py` | REST endpoints: approve, reject, list pending |
| **Modified** | |
| `services/alert-ingestor/main.py` | Mounted approvals router |
| `infra/docker-compose.override.yml` | Added remediation-mcp on port 8005 |

---

## 2. Why We Built It This Way

### Why Separate Diagnosis and Remediation into Two Agents?

It might seem simpler to have one agent that diagnoses AND fixes. But separation gives us critical safety properties:

1. **Different permission models**: The Diagnosis Agent has READ-ONLY access (k8s-mcp, logs-mcp, metrics-mcp). The Remediation Agent has WRITE access (remediation-mcp). If the diagnosis loop goes haywire, it can't accidentally delete pods or rollback deployments.

2. **Human checkpoint**: Between diagnosis and remediation there's a natural checkpoint. A human can review the root cause analysis before any destructive action is taken. With a single agent, you'd need to interrupt mid-conversation.

3. **Independent scaling**: Diagnosis is CPU/LLM-intensive (multiple tool calls per incident). Remediation is I/O-intensive (waiting for approvals, executing K8s commands). Different scaling requirements.

### Why Risk Levels Instead of "Just Ask for Approval"?

Not all actions are equally dangerous:

| Risk | Examples | Why This Level |
|------|----------|----------------|
| **LOW** | Restart one pod, clear cache | Zero downtime if replicas exist. Worst case: 10-second blip. Auto-execute. |
| **MEDIUM** | Rolling restart, scale deployment | Brief capacity reduction. Could take down the service if misconfigured. 5-min approval window. |
| **HIGH** | Rollback deployment, drain node | Reverts code changes (potential regressions), evicts all pods (capacity crisis). No timeout — human must respond. |

If every action needed approval, you'd lose the speed advantage of automation. A pod restart at 3 AM shouldn't wake someone up. A deployment rollback absolutely should.

### Why Redis for Approval State (Not PostgreSQL)?

Approval tokens are **ephemeral** — they have TTLs (5 minutes for medium, no expiry for high) and are polled frequently. Redis is perfect for this because:

1. **Native TTL support**: `SET key value EX 300` automatically expires the token after 5 minutes. PostgreSQL would need a background cleanup job.
2. **Low-latency polling**: The remediation agent polls every 2 seconds. Redis responds in <1ms. PostgreSQL would take 2-5ms per poll.
3. **Already in the stack**: We're already using Redis for dedup and caching. No new infrastructure.

### Why a Polling Loop Instead of WebSockets/Webhooks?

The remediation agent uses a simple `while` loop that checks Redis every 2 seconds:

```python
while elapsed < timeout:
    token = await redis.get_approval_token(tenant_id, request_id)
    if token["status"] in ("approved", "rejected"):
        return token["status"]
    await asyncio.sleep(2)
```

Why not use Redis pub/sub or WebSockets instead?

1. **Simplicity**: A polling loop is 10 lines of code. WebSocket handling is 50+ lines with connection management, reconnection, heartbeats.
2. **Reliability**: If the agent restarts mid-poll, it picks up exactly where it left off (the token is still in Redis). WebSocket state would be lost.
3. **Scale**: At our scale (tens of incidents per hour), polling every 2 seconds adds negligible Redis load (0.5 RPS per active approval).

In production with thousands of concurrent incidents, you'd switch to Redis pub/sub with a polling fallback.

### Why Claude Generates the Plan (Not Hardcoded Rules)?

We could have written `if root_cause contains "connection pool" then restart_deployment`. But:

1. **Root causes are diverse**: There are thousands of possible root causes. Hardcoding rules for each is impossible.
2. **Context matters**: "Connection pool exhausted" might need a restart, OR it might need a scale-up, OR it might need a config change. Claude reads the evidence and picks the right action.
3. **Cross-referencing**: Claude considers the severity, the service name, the environment, and the evidence. A P1 on a payment service gets treated differently than a P3 on a logging service.

### Why a Fallback Plan on LLM Failure?

If Claude's API is down or returns an error, we still need to do *something*. The fallback is always a LOW-risk action:

```python
# Fallback when LLM fails
Action(
    tool_fn="restart_pod",
    parameters={"namespace": "default", "pod_name": f"{service}-0"},
    risk_level=RiskLevel.LOW,
    reasoning="LLM plan generation failed. Attempting safe pod restart as fallback."
)
```

This is the **safest possible action** (restarting one pod), and it's LOW risk so it executes without approval. It won't fix every problem, but it fixes the most common ones (process leaks, stale connections, OOM kills) without human intervention.

---

## 3. How It Works Under The Hood

### The Full Remediation Flow

```
                    incidents.active (Kafka)
                           │
                           ▼
              ┌─── Remediation Agent ───┐
              │                         │
              │ 1. Reconstruct incident │
              │ 2. Call Claude with:    │
              │    - root_cause         │
              │    - evidence           │
              │    - available tools    │
              │                         │
              │ Claude returns:         │
              │ [                       │
              │   {restart_deployment,  │
              │    risk: medium},       │
              │   {clear_cache,         │
              │    risk: low}           │
              │ ]                       │
              └────────┬────────────────┘
                       │
              ┌────────▼────────────────┐
              │ For each Action:        │
              │                         │
              │ LOW risk?               │
              │  └── Execute immediately│───▶ remediation-mcp
              │                         │         │
              │ MEDIUM risk?            │         ├── restart_pod()
              │  ├── Create approval    │         ├── restart_deployment()
              │  │   token in Redis     │         ├── scale_deployment()
              │  ├── Log request_id     │         └── ...
              │  └── Poll Redis...      │
              │      ├── approved → exec│
              │      ├── rejected → esc │
              │      └── expired → esc  │
              │                         │
              │ HIGH risk?              │
              │  └── Same as MEDIUM     │
              │      but no timeout     │
              └────────┬────────────────┘
                       │
              ┌────────▼────────────────┐
              │ All actions done:       │
              │                         │
              │ All succeeded?          │
              │  └── status = resolved  │
              │                         │
              │ Any rejected/expired?   │
              │  └── status = escalated │
              │                         │
              │ Persist to PostgreSQL   │
              │ Publish to Kafka        │
              └─────────────────────────┘
```

### The Approval Gate — Step by Step

When a MEDIUM-risk action needs approval:

```
Remediation Agent                    Redis                         Human (via curl/Slack)
     │                                │                                │
     │── SET approval:token:default:  │                                │
     │   abc-123 = {status: pending}  │                                │
     │   EX 300 (5-min TTL)          │                                │
     │                                │                                │
     │ (logs: "Approve via POST       │                                │
     │  /approvals/abc-123/approve")  │                                │
     │                                │                                │
     │── GET approval:token:..abc-123 │                                │
     │   → {status: pending}         │                                │
     │   sleep 2s                     │                                │
     │                                │                                │
     │── GET approval:token:..abc-123 │                                │
     │   → {status: pending}         │                                │
     │   sleep 2s                     │                  curl POST .../approve
     │                                │◄────────── resolve_approval_token()
     │                                │   {status: approved}           │
     │                                │                                │
     │── GET approval:token:..abc-123 │                                │
     │   → {status: approved} ✅     │                                │
     │                                │                                │
     │── Execute action via MCP ────►│                                │
```

### How the Remediation MCP Tools Work

Each tool uses the tenant's K8s credentials (same as k8s-mcp in Phase 4):

**`restart_pod`** (LOW risk):
```python
# Internally calls:
v1.delete_namespaced_pod(name=pod_name, namespace=ns)
# Kubernetes controller sees the pod is missing → creates a new one
# Time: ~5 seconds for the new pod to be Running
```

**`restart_deployment`** (MEDIUM risk):
```python
# Internally patches the deployment with a restart annotation:
patch = {"spec": {"template": {"metadata": {"annotations": {
    "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
}}}}}
apps_v1.patch_namespaced_deployment(name=deployment, namespace=ns, body=patch)
# Kubernetes performs a rolling update: new pods up before old pods down
# Time: 30-120 seconds depending on replica count and readiness probes
```

**`rollback_deployment`** (HIGH risk):
```python
# Annotates the deployment with rollback metadata, then triggers restart
# In production, you'd use the ReplicaSet history to restore the exact previous spec
# Time: 30-120 seconds, risk of regressions if previous version had bugs
```

**`drain_node`** (HIGH risk):
```python
# Step 1: Cordon — mark node as unschedulable
v1.patch_node(name=node_name, body={"spec": {"unschedulable": True}})

# Step 2: Evict — remove all pods (except kube-system)
for pod in v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}"):
    v1.create_namespaced_pod_eviction(name=pod.name, namespace=pod.namespace, ...)
# Pods get rescheduled onto other healthy nodes
# Time: 1-5 minutes depending on PodDisruptionBudgets
```

### Incident Status State Machine

After Phase 6, the full status lifecycle is:

```
triaging → diagnosing → remediating → awaiting_approval → resolved
                                    │                   └→ escalated (rejected/expired)
                                    └→ escalated (errors)
```

---

## 4. How to Test It

### Step 1: Start Everything
```bash
# Terminal 1: Infrastructure
cd infra && docker compose up -d

# Terminal 2: Alert Ingestor (with approvals API)
cd services/alert-ingestor
source ../../venv/bin/activate
PYTHONPATH=$(pwd)/../.. uvicorn main:app --port 8000

# Terminal 3: Triage Agent
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/triage-agent/main.py

# Terminal 4: Diagnosis Agent
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/diagnosis-agent/main.py

# Terminal 5: Remediation Agent
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/remediation-agent/main.py
```

### Step 2: Fire an Alert
```bash
curl -X POST http://localhost:8000/alerts/manual \
  -H "Content-Type: application/json" \
  -d '{
    "name": "HighErrorRate",
    "service": "checkout-service",
    "environment": "production",
    "summary": "Error rate above 10% for the last 5 minutes"
  }'
```

### Step 3: Watch the Pipeline

The alert flows through all 4 agents:
1. **Ingestor** → publishes to `alerts.raw`
2. **Triage Agent** → classifies severity, publishes to `alerts.triaged`
3. **Diagnosis Agent** → investigates (or falls back), publishes to `incidents.active`
4. **Remediation Agent** → generates plan, executes or requests approval

### Step 4: Test Approval Flow (when Anthropic API is configured)

If Claude generates a MEDIUM-risk action, you'll see in the remediation agent logs:
```
HUMAN_ACTION_REQUIRED  message="Approve via: POST /approvals/abc-123/approve"
```

Then approve it:
```bash
# Check pending
curl http://localhost:8000/approvals/pending

# Approve
curl -X POST http://localhost:8000/approvals/abc-123/approve?approved_by=prahlad

# Reject (alternative)
curl -X POST http://localhost:8000/approvals/abc-123/reject?approved_by=prahlad
```

### Step 5: Verify in PostgreSQL
```bash
source venv/bin/activate && PYTHONPATH=$(pwd) python3 -c "
import asyncio
from sqlalchemy import text
from shared.pg_client import PostgresClient

async def check():
    pg = PostgresClient('postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db')
    async with pg.session() as sess:
        result = await sess.execute(text(
            'SELECT status, resolution_summary FROM incidents ORDER BY updated_at DESC LIMIT 1'
        ))
        row = result.fetchone()
        print(f'Status: {row[0]}')
        print(f'Resolution: {row[1]}')

asyncio.run(check())
"
```

---

## 5. Key Concepts to Remember

### What is Human-in-the-Loop (HITL)?
A design pattern where an AI system pauses before taking high-impact actions and waits for a human to approve or reject. This is critical in incident response because:
- **Safety**: AI can misdiagnose. A wrong rollback could make things worse.
- **Compliance**: Some organizations require human authorization for production changes.
- **Learning**: Rejections teach the system what plans humans disagree with.

### What is a Remediation Plan?
A structured list of concrete actions to fix an incident. Each action has:
- **tool_fn**: Which remediation tool to call (e.g., `restart_deployment`)
- **parameters**: Specific inputs (namespace, deployment name)
- **risk_level**: Determines the approval requirement
- **reasoning**: Why this action should fix the root cause

### What is Risk-Based Access Control?
The idea that different actions require different levels of authorization based on their potential impact. This is the same principle as:
- AWS IAM: `s3:PutObject` vs `s3:DeleteBucket`
- Unix permissions: read vs write vs execute
- Our platform: LOW (auto-execute) vs MEDIUM (5-min approval) vs HIGH (manual approval)

### What is the Escalation Pattern?
When automation can't resolve an incident, it **escalates** to a human rather than retrying forever. Escalation happens when:
- An approval times out (human didn't respond in 5 minutes)
- An approval is rejected (human disagrees with the plan)
- A remediation action fails (K8s returns an error)
- All retries are exhausted

The escalated incident stays in PostgreSQL with all evidence and the attempted plan, giving the human everything they need to investigate manually.

### Why "Plan First, Execute Later"?
The agent generates the ENTIRE plan before executing ANY action. This is deliberate:
1. **Reviewability**: The human can see the full plan before approving individual actions
2. **Atomicity**: If Action 2 is rejected, Action 1's result can be rolled back
3. **Auditability**: The plan is logged before execution, creating a before/after record
4. **Safety**: Claude might generate a plan where steps depend on each other. Executing one step before seeing the full plan could be dangerous.
