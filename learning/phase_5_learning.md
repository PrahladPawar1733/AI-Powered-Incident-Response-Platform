# Phase 5 Learning: The Diagnosis Agent (Multi-Turn Agentic Loop)

## 1. What We Built in Phase 5

Phase 5 introduced the **Diagnosis Agent** — the most technically sophisticated component in the entire platform. While the Triage Agent (Phase 3) makes a single LLM call to classify severity, the Diagnosis Agent runs an **autonomous investigation loop** where Claude decides what to investigate, calls diagnostic tools, examines results, and keeps digging until it finds the root cause.

### Files Created

| File | Purpose |
|------|---------|
| `services/diagnosis-agent/main.py` | Kafka consumer that reads from `alerts.triaged`, filters by severity, and orchestrates the full diagnosis lifecycle |
| `services/diagnosis-agent/agent.py` | The agentic loop — sends tools to Claude, processes `tool_use` responses, feeds results back, repeats |
| `services/diagnosis-agent/tools.py` | 15 tool JSON schemas that Claude sees + `importlib`-based executor that calls MCP server functions |
| `services/diagnosis-agent/config.py` | System prompt defining Claude's persona as an expert SRE investigator |
| `services/diagnosis-agent/Dockerfile` | Container definition |

---

## 2. Why We Built It This Way

### Why a Multi-Turn Loop Instead of a Single LLM Call?

The Triage Agent asks Claude a simple question: "What severity is this alert?" That's a classification task — one call is enough.

But diagnosis is fundamentally different. When a human SRE investigates an incident, they:
1. Look at pod status → see a CrashLoopBackOff
2. Check the crash logs → see "connection refused to database"
3. Check DB connections → see the pool is exhausted
4. Check slow queries → find a long-running migration lock
5. **Conclusion**: The deployment ran a migration that locked the table, exhausting the connection pool

Each step depends on the previous step's findings. You can't ask all the questions upfront because you don't know what questions to ask until you see the first answer. This is exactly what the **agentic loop** does — Claude investigates iteratively, just like a human would.

### Why Define Tools as JSON Schemas?

Claude's API has native support for "tool use." You send tool definitions (name, description, input schema) alongside your message, and Claude can respond with `tool_use` content blocks saying "I want to call this tool with these parameters."

```python
# What we send to Claude:
tools = [{
    "name": "get_pod_status",
    "description": "Get pod status for a service...",
    "input_schema": {
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "namespace": {"type": "string"},
            "service": {"type": "string"},
        },
        "required": ["tenant_id", "namespace", "service"]
    }
}]

# What Claude responds with:
response.content = [
    ToolUseBlock(type="tool_use", id="call_123", name="get_pod_status",
                 input={"tenant_id": "default", "namespace": "default", "service": "payment-api"})
]
```

This is far better than asking Claude to "generate a kubectl command" because:
- Claude can't hallucinate tool names — they must match our definitions
- Input validation happens automatically via the JSON schema
- We control execution — Claude proposes, we execute

### Why `importlib` for Tool Execution?

Our MCP server directories have hyphens (`mcp-servers`), which Python can't import normally with `from` statements. We use `importlib.util.spec_from_file_location()` to dynamically load the modules by filesystem path:

```python
# Problem: Python can't handle hyphens
from services.mcp-servers.k8s.main import get_pod_status  # ❌ SyntaxError

# Solution: importlib loads by file path
spec = importlib.util.spec_from_file_location("_mcp_k8s", "/path/to/mcp-servers/k8s/main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
func = getattr(module, "get_pod_status")  # ✅ Works perfectly
```

We cache loaded modules in `sys.modules` so each MCP server is imported only once, regardless of how many tool calls Claude makes.

### Why P3/P4 Auto-Resolve?

Running the full diagnostic loop costs:
- **Time**: 10-30 seconds for multiple LLM calls + tool executions
- **Money**: ~$0.10-$0.50 per diagnosis (Claude API costs)
- **Cognitive load**: Creates noise in the incident dashboard

P3 (warning) and P4 (informational) alerts don't justify this cost. A warning about "disk usage at 75%" doesn't need an SRE investigation. We auto-resolve them with a simple message and save the expensive analysis for P1/P2 incidents that actually impact users.

### Why Cap at 10 Tool Calls?

Without a cap, Claude could theoretically loop forever:
1. Check pods → nothing wrong
2. Check logs → nothing wrong
3. Check metrics → nothing wrong
4. Check DB → nothing wrong
5. Check pods again with different parameters...
6. (repeating forever)

The 10-call cap forces Claude to work efficiently. If it hasn't found the root cause after 10 diagnostic calls, we ask it to provide its best hypothesis with whatever evidence it has. In practice, most incidents are diagnosed in 3-5 tool calls.

---

## 3. How It Works Under The Hood

### The Agentic Loop — Step by Step

Here's what happens when a P2 `DatabaseConnectionError` incident arrives:

```
Step 1: Build the prompt
┌──────────────────────────────────────────────┐
│ "Incident: DatabaseConnectionError           │
│  Service: payment-api                        │
│  Severity: P2                                │
│  Triage: Connection timeout to database      │
│  Please investigate using diagnostic tools." │
└──────────────────────────────────────────────┘
         │
         ▼
Step 2: Send to Claude (with 15 tool definitions)
         │
         ▼
Step 3: Claude responds → tool_use: get_connection_count(db_name="incident_db")
         │
         ▼
Step 4: We execute the tool against DB MCP
         │
         ├── get_connection_count → "idle: 6, active: 1, TOTAL: 7"
         │
         ▼
Step 5: Store result as Evidence on IncidentContext
         │
         ▼
Step 6: Send tool result back to Claude
         │
         ▼
Step 7: Claude responds → tool_use: search_logs(service="payment-api", query="connection")
         │
         ▼
Step 8: We execute → Loki returns log lines with timeout errors
         │
         ▼
Step 9: Store as Evidence, send back to Claude
         │
         ▼
Step 10: Claude responds → end_turn with JSON diagnosis:
         {
           "root_cause": "Connection pool exhaustion due to leaked connections",
           "affected_services": ["payment-api"],
           "diagnosis_summary": "DB connections show 847 idle connections...",
           "confidence": 0.92
         }
         │
         ▼
Step 11: Apply diagnosis to IncidentContext, set status=remediating
```

### The Message History Pattern

The key to the agentic loop is how messages accumulate:

```python
messages = [
    # Initial request
    {"role": "user", "content": "Investigate this incident..."},

    # Claude's first response (tool call)
    {"role": "assistant", "content": [ToolUseBlock(name="get_connection_count", ...)]},

    # Our tool result
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "idle: 6..."}]},

    # Claude's second response (another tool call)
    {"role": "assistant", "content": [ToolUseBlock(name="search_logs", ...)]},

    # Our tool result
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_2", "content": "[ERROR] timeout..."}]},

    # Claude's final response (diagnosis JSON)
    {"role": "assistant", "content": [TextBlock(text='{"root_cause": "..."}')]}
]
```

Each loop iteration adds 2 messages: Claude's tool request and our tool result. Claude can see the entire conversation history, so it can cross-reference earlier findings.

### Stop Reason Handling

Claude's response includes a `stop_reason` that tells us what to do next:

| `stop_reason` | Meaning | Our Action |
|---------------|---------|------------|
| `tool_use` | Claude wants to call tools | Execute tools, send results back, loop again |
| `end_turn` | Claude is done investigating | Extract the JSON diagnosis, apply to incident |
| `max_tokens` | Response was too long | Treat as end_turn, extract what we can |

### Evidence Collection

Every tool call result is stored as an `Evidence` object on the `IncidentContext`:

```python
incident.add_evidence(
    source="db-mcp",                    # Which MCP server
    tool="get_connection_count",        # Which tool
    content="idle: 847, active: 3...",  # The actual data (capped at 2KB)
    relevance="Called during investigation (call #1)"
)
```

This evidence chain is critical for:
1. **Audit trail**: Humans can see exactly what the AI investigated
2. **Learning loop** (Phase 8): Past evidence helps future diagnoses
3. **Accountability**: If the diagnosis is wrong, you can trace the faulty reasoning

### Graceful Error Handling

The agent has 3 layers of error handling:

```
Layer 1: Individual tool calls
  └── If a tool fails → returns error string to Claude
      Claude sees "ERROR: Cannot connect to Prometheus" and adapts its strategy

Layer 2: The agentic loop
  └── If Claude's API fails (401, rate limit, timeout) →
      Catches exception, sets fallback root cause, continues pipeline

Layer 3: The Kafka consumer
  └── If the entire handler throws →
      Does NOT commit offset, message will be retried
```

This means the platform never drops an incident. The worst case is a degraded diagnosis ("Diagnosis failed: ...") that still reaches the remediation stage for human review.

---

## 4. How to Test It

### Step 1: Start All Infrastructure
```bash
cd infra && docker compose up -d
```

### Step 2: Start the Alert Ingestor
```bash
cd services/alert-ingestor
source ../../venv/bin/activate
PYTHONPATH=$(pwd)/../.. uvicorn main:app --port 8000
```

### Step 3: Start the Triage Agent
```bash
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/triage-agent/main.py
```

### Step 4: Start the Diagnosis Agent
```bash
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/diagnosis-agent/main.py
```

### Step 5: Fire a P2 Alert
```bash
curl -X POST http://localhost:8000/alerts/manual \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DatabaseConnectionError",
    "service": "payment-api",
    "environment": "production",
    "summary": "Connection pool exhausted, all connections in use"
  }'
```

### What You'll See in the Logs

**Triage Agent Terminal:**
```
triage_started         alert_id=abc-123
triage_completed       severity=P2 runbook=None
```

**Diagnosis Agent Terminal:**
```
incident_received_for_diagnosis  severity=P2 alert_name=DatabaseConnectionError
diagnosis_started                service=payment-api
llm_call                         loop_iteration=0
tool_call                        tool=get_connection_count call_number=1
tool_executed                    tool=get_connection_count result_length=85
llm_call                         loop_iteration=1
tool_call                        tool=search_logs call_number=2
...
diagnosis_completed              root_cause="Connection pool exhaustion..."
incident_diagnosis_persisted     status=remediating evidence_count=3
```

### Step 6: Verify in PostgreSQL
```bash
source venv/bin/activate && PYTHONPATH=$(pwd) python3 -c "
import asyncio
from sqlalchemy import text
from shared.pg_client import PostgresClient

async def check():
    pg = PostgresClient('postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db')
    async with pg.session() as sess:
        result = await sess.execute(text(
            'SELECT incident_id, status, root_cause FROM incidents ORDER BY created_at DESC LIMIT 1'
        ))
        row = result.fetchone()
        print(f'Status: {row[1]}')
        print(f'Root Cause: {row[2]}')

asyncio.run(check())
"
```

---

## 5. Key Concepts to Remember

### What is an "Agentic Loop"?
An agentic loop is when an AI model controls its own workflow by deciding which actions to take at each step. Unlike a "chain" (predefined sequence of steps), an agent dynamically chooses its path based on what it discovers. The loop continues until the agent decides it has accomplished its goal.

### What is `stop_reason` in Anthropic's API?
When Claude responds, the `stop_reason` field tells you why it stopped generating:
- `end_turn`: Claude finished its response naturally (done thinking)
- `tool_use`: Claude wants to call one or more tools before continuing
- `max_tokens`: Claude hit the token limit mid-response
- `stop_sequence`: A stop sequence was hit

The agentic loop hinges on checking `stop_reason == "tool_use"` to know when to keep looping.

### What are Tool Use Blocks?
Claude's response can contain multiple content blocks of different types:
- `TextBlock`: Regular text output
- `ToolUseBlock`: A request to call a specific tool with specific parameters

A single response can contain multiple `ToolUseBlock`s — Claude might want to check pod status AND search logs simultaneously. We execute all of them and send all results back together.

### Why Manual Tool Execution Instead of `mcp_servers`?
Anthropic's SDK has an `mcp_servers` parameter that lets Claude call MCP servers directly. But this requires Claude's servers (at `api.anthropic.com`) to reach our MCP servers over the internet. Since our MCP servers run on `localhost`, Claude can't reach them. So we implement the loop ourselves:
1. **We** define tools as JSON schemas
2. **Claude** proposes tool calls
3. **We** execute them locally
4. **We** send results back

This is functionally identical and gives us full control over execution, error handling, and evidence collection.

### The Evidence Chain — Why It Matters
Every tool call result stored as `Evidence` creates an audit trail. This is critical for:
- **Post-incident reviews**: "Why did the AI think the root cause was X?"
- **Trust building**: Humans can verify the AI's reasoning step-by-step
- **Legal compliance**: Some regulated industries require full audit trails for incident response
- **Learning loop**: Phase 8 will use past evidence to improve future diagnoses
