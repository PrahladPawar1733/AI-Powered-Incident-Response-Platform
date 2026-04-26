# Phase 3 Learning: The Triage Agent

## 1. What We Built in Phase 3

In Phase 3, we successfully created the **Triage Agent** (`services/triage-agent/`), which serves as the first fully autonomous AI worker in our incident response platform. 

Unlike the Alert Ingestor from Phase 2 (which is an HTTP FastAPI server), the Triage Agent has **no web interface**. It is a pure background daemon that listens to Kafka, performs context-gathering, queries a Large Language Model (LLM), updates a database, and emits new Kafka messages.

### Key Components

| File | Purpose |
|------|---------|
| `main.py` | The infinite `asyncio` loop running the Kafka consumer. It wraps the core agent logic with strict try/except blocks to prevent Kafka offset mismatches if the AI fails. |
| `agent.py` | The "brain". It defines the `process_alert` workflow: initialize incident → grab embeddings → semantic search runbooks/incidents → build LLM Prompt → parse JSON response. |
| `embeddings.py` | Converts unstructured alert text into a strict 1,536-dimensional float vector. We mocked this out via deterministic hashing since we don't have an active embedding API key yet. |
| `config.py` | Houses the `system_prompt` describing the persona (Elite SRE) and the required output JSON schema. |

---

## 2. Why We Built It This Way

### Why Not Just Use an HTTP Microservice?
If the Alert Ingestor simply sent an HTTP POST to the Triage Agent, it would create **temporal coupling**. 

If the Triage Agent went down (or if the Anthropic API took >30 seconds to reply), the HTTP request would time out. The Alert Ingestor would then fail, causing the monitoring system (e.g., Prometheus) to start dropping alerts. 

Instead, by utilizing **Kafka Consumer Groups**, we achieve supreme resiliency:
1. The `alert-ingestor` drops the message in Kafka in <5ms and forgets about it.
2. The `triage-agent` pulls messages from Kafka whenever it's free.
3. If the Triage Agent crashes (like it did when we sent a bad API key), it catches the error and gracefully resolves it using defaults without breaking the rest of the queue.

### Why Use `pgvector` Before Calling the LLM? (RAG Pattern)
Large Language Models like Claude are incredibly smart, but they do **not** know your company's proprietary systems. If a `checkout-service` alert fires, Claude has no idea what that means internally. 

We used **RAG** (Retrieval-Augmented Generation):
1. Convert the alert text into math (Embeddings).
2. Ask PostgreSQL (`pgvector`): "Give me the top 2 runbooks and top 3 past incidents that are mathematically closest to this new alert."
3. Send those runbooks dynamically to Claude in the specific prompt so it has "memory".

### Why Output Strict JSON?
If Claude responds with conversational text (*"Hello! I see an alert. Here's my thoughts..."*), it breaks automated programming flows. We engineered the `system_prompt` to demand strict JSON formatting (`{"severity": "P1" ...}`), allowing our python application to reliably `json.loads()` the output and inject it mathematically back into PostgreSQL and Kafka to pass to the next system.

---

## 3. How It Works Under The Hood

#### 1. The Consumer Loop
In `main.py`, the agent spins up using `KafkaConsumer` on the `"triage-agent-group"`. The group acts as a load-balancer concept. If you spin up 5 Docker containers of the Triage Agent, Kafka uses the group ID to ensure they evenly split the messages without stepping on toes.

#### 2. Model Initialization (Multi-Tenancy)
When a raw alert is consumed off the `alerts.raw` topic, we dynamically pull the `tenant_id`. Every single subsequent database query explicitly restricts search spaces using `WHERE tenant_id = ...`.

#### 3. LLM Parsing
Inside `agent.py:_classify_with_llm`, we send the raw alert alongside the JSON-dumped contextual runbooks. If the API key is unauthorized, or Claude hallucinates invalid JSON, the code catches the exception (`except Exception as e:`) and safely provides a hard-coded fallback mechanism (e.g., automatically assigning Severity `P2`).

#### 4. Upserting into PostgreSQL
Because PostgreSQL sits at the center of the system, we execute an `INSERT ... ON CONFLICT DO UPDATE`. This means if an incident already exists in the system (e.g., it is being updated later in the pipeline), we update the fields instead of blindly creating rows.

#### 5. Publishing to the Next Phase
Finally, the agent pushes the triaged incident data to `alerts.triaged` where the upcoming Diagnosis Agent (Phase 5) will read it, pulling live diagnostics from the MCP servers.

---

## 4. How to Test It

Because the Triage Agent consumes passively from Kafka, you simply test it by using the components of Phase 2!

### Step 1: Start PostgreSQL, Kafka, and Redis
```bash
cd infra
docker compose up -d
```

### Step 2: Start the Alert Ingestor
```bash
# In Terminal 1
source venv/bin/activate
cd services/alert-ingestor
uvicorn main:app --port 8000
```

### Step 3: Start the Triage Agent
```bash
# In Terminal 2
source venv/bin/activate
cd services/triage-agent
python main.py
```

### Step 4: Fire an Alert
The moment you fire a raw POST payload to the ingestor, the system handles the entire pipeline autonomously!
```bash
curl -X POST http://localhost:8000/alerts/manual \
  -H "Content-Type: application/json" \
  -d '{
        "name": "CheckoutAPIError",
        "service": "checkout",
        "environment": "production",
        "summary": "The checkout API is throwing 500s"
      }'
```

Watch the terminal logs closely! You will see the Alert Ingestor log `202 Accepted`, and immediately after, you'll see the Triage Agent Terminal log `message_received` and ultimately `incident_fully_triaged_and_persisted`!
