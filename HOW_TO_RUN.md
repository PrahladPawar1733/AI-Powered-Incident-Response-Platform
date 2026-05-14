# 🚀 How to Run the Incident Response Platform (Start to Finish)

## Prerequisites
- **Docker Desktop** running (for Kafka, Redis, Postgres, Prometheus, Grafana, Loki)
- **Python 3.12+** with `venv` activated
- **Node.js 18+** (for the frontend dashboard)

---

## Step 1: Start Infrastructure (Docker)

```bash
cd infra
docker compose up -d
```

This starts: **Kafka**, **Zookeeper**, **Redis**, **PostgreSQL**, **Prometheus**, **Grafana**, **Loki**, **Promtail**

Verify:
```bash
docker compose ps   # All containers should be "running"
```

Wait ~10 seconds for Kafka and Postgres to fully initialize.

---

## Step 2: Activate Python Environment

```bash
cd incident-response-platform
source venv/bin/activate
```

---

## Step 3: Start the Alert Ingestor (API Gateway)

```bash
cd services/alert-ingestor
PYTHONPATH=$(pwd)/../.. uvicorn main:app --port 8000
```

### Verify:
```bash
curl http://localhost:8000/health
# {"status":"healthy","service":"unknown","redis":true,"kafka":true}
```

**Endpoints available:**
| Endpoint | Purpose |
|----------|---------|
| `POST /alerts/manual` | Fire alerts manually |
| `GET /dashboard/incidents` | List all incidents |
| `GET /dashboard/incidents/{id}` | Incident detail |
| `GET /dashboard/stats` | Platform statistics |
| `GET /credentials/` | View registered credentials |
| `PUT /credentials/prometheus` | Register Prometheus |
| `PUT /credentials/loki` | Register Loki |
| `PUT /credentials/kubernetes` | Register K8s |
| `GET /approvals/pending` | Pending approvals |
| `POST /approvals/{id}/approve` | Approve an action |
| `POST /approvals/{id}/reject` | Reject an action |

---

## Step 4: Register Infrastructure Credentials

```bash
# Register your local Prometheus
curl -X PUT http://localhost:8000/credentials/prometheus \
  -H "Content-Type: application/json" \
  -d '{"base_url": "http://localhost:9090", "auth_type": "none"}'

# Register your local Loki
curl -X PUT http://localhost:8000/credentials/loki \
  -H "Content-Type: application/json" \
  -d '{"base_url": "http://localhost:3100", "auth_type": "none"}'

# Verify
curl http://localhost:8000/credentials/
```

---

## Step 5: Start the Triage Agent (Terminal 2)

```bash
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/triage-agent/main.py
```

You should see: `consumer_started`

---

## Step 6: Start the Diagnosis Agent (Terminal 3)

```bash
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/diagnosis-agent/main.py
```

You should see: `diagnosis_agent_starting`

---

## Step 7: Start the Remediation Agent (Terminal 4)

```bash
source venv/bin/activate
PYTHONPATH=$(pwd) python3 services/remediation-agent/main.py
```

You should see: `remediation_agent_starting`

---

## Step 8: Start the Frontend Dashboard (Terminal 5)

```bash
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

---

## Step 9: Fire an Alert and Watch the Pipeline!

### Option A: Via the Dashboard
1. Open http://localhost:5173
2. Click **🔥 Fire Alert** in the sidebar
3. Click a preset (e.g. "🔴 P1 — Database Down")
4. Click **🔥 Fire Alert**
5. Switch to **🚨 Incidents** to watch it flow through the pipeline

### Option B: Via curl
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

---

## Step 10: Watch the Pipeline Flow

The alert flows through 4 agents in order:

```
Alert Ingestor → Triage Agent → Diagnosis Agent → Remediation Agent
     (8000)          (Kafka)        (Kafka)           (Kafka)
```

### In the Dashboard:
1. **Incidents page**: Shows the incident with status changing in real-time
2. **Click an incident**: See full detail — triage summary, root cause, evidence, remediation plan

### In the terminal logs:
- **Triage Agent**: `triage_completed severity=P2`
- **Diagnosis Agent**: `tool_call tool=get_connection_count` → `diagnosis_completed`
- **Remediation Agent**: `executing_action tool=restart_pod` → `remediation_completed`

---

## Step 11: Test the Approval Flow (when MEDIUM/HIGH risk actions are generated)

If the remediation agent generates a MEDIUM or HIGH risk action, you'll see:
```
HUMAN_ACTION_REQUIRED  message="Approve via: POST /approvals/{request_id}/approve"
```

### In the Dashboard:
1. Click **✅ Approvals** in the sidebar
2. You'll see the pending approval with risk level
3. Click **✅ Approve** or **❌ Reject**

### Via curl:
```bash
# Check pending
curl http://localhost:8000/approvals/pending

# Approve
curl -X POST http://localhost:8000/approvals/{request_id}/approve?approved_by=prahlad
```

---

## Ports Summary

| Service | Port | URL |
|---------|------|-----|
| **Frontend Dashboard** | 5173 | http://localhost:5173 |
| **Alert Ingestor API** | 8000 | http://localhost:8000 |
| **K8s MCP** | 8001 | http://localhost:8001/sse |
| **DB MCP** | 8002 | http://localhost:8002/sse |
| **Logs MCP** | 8003 | http://localhost:8003/sse |
| **Metrics MCP** | 8004 | http://localhost:8004/sse |
| **Remediation MCP** | 8005 | http://localhost:8005/sse |
| **Kafka** | 29092 | localhost:29092 |
| **Redis** | 6379 | localhost:6379 |
| **PostgreSQL** | 5432 | localhost:5432 |
| **Prometheus** | 9090 | http://localhost:9090 |
| **Grafana** | 3000 | http://localhost:3000 |
| **Loki** | 3100 | http://localhost:3100 |

---

## Environment Variables (.env)

Make sure these are set (create a `.env` file in the project root):

```env
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-2.5-flash
KAFKA_BOOTSTRAP_SERVERS=localhost:29092
POSTGRES_URL=postgresql+asyncpg://agent_user:changeme@localhost:5432/incident_db
REDIS_URL=redis://localhost:6379/0
```

---

## Troubleshooting

### "Address already in use"
```bash
lsof -ti:8000 | xargs kill -9   # Kill process on port 8000
```

### "Kafka consumer not receiving messages"
Make sure the alert-ingestor published successfully first. Check with:
```bash
curl http://localhost:8000/health
```

### "API error from Gemini"
Set a valid `GEMINI_API_KEY` in your `.env` file. Get one free at https://aistudio.google.com/.

### "Database connection failed"
```bash
docker compose -f infra/docker-compose.yml ps postgres   # Check if running
```
