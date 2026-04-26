# Phase 2 Learning: Alert Ingestor & Multi-Tenancy

## 1. What We Built in Phase 2

In Phase 2, we built the **Alert Ingestor** service (`services/alert-ingestor/`). 

The Alert Ingestor is a FastAPI HTTP application that acts as the "front door" for all incoming alerts. Its primary responsibilities are:
1. **Authentication & Multi-Tenancy:** Securely authenticating incoming requests via JWTs and extracting the `tenant_id`.
2. **Normalization:** Taking varying JSON schemas from different monitoring tools (Prometheus, Grafana, custom webhooks) and transforming them into a standard, unified `AlertEvent` object.
3. **Deduplication:** Preventing "flapping" alerts (alerts that trigger continuously every 30 seconds) from creating hundreds of duplicate incidents.
4. **Publishing:** Pushing the normalized, deduplicated alerts onto the `alerts.raw` Kafka queue for asynchronous processing by the AI agents.

### The Files Created

| File | Purpose |
|------|---------|
| `shared/auth.py` | Shared JWT middleware to extract `tenant_id` from the Authorization header. |
| `main.py` | The FastAPI application entrypoint and lifespan manager (connecting to Redis/Kafka). |
| `publisher.py` | Handles Redis deduplication and Kafka publishing logic. |
| `normalizers.py` | The transformation logic that converts third-party JSON to `AlertEvent` models. |
| `routes/*.py` | The actual HTTP endpoints (`/alerts/prometheus`, `/alerts/grafana`, etc.). |
| `Dockerfile` | Defines how the service is built and deployed as a lightweight container. |

---

## 2. Why We Built It This Way

### Why Separate Ingestion from Processing?
You might wonder: *why not just have Prometheus directly trigger the Triage Agent?*

In a major production incident, entire data centers can go down, causing **thousands of alerts to fire simultaneously** within seconds (an alert storm). 
- If the Triage Agent (which makes slow LLM calls to Claude) was directly exposed to this webhook, the system would immediately crash or time out. 
- By using an Alert Ingestor, we synchronously accept the webhook (returning `202 Accepted` immediately) and durably persist it to **Kafka**. The AI agents then safely consume from Kafka at their own pace without dropping data.

### Why Redis Deduplication?
Monitoring tools like Prometheus are stateless evaluators. If a database is down, Prometheus evaluates the `DatabaseDown` rule every 15 seconds and sends an HTTP POST Every. Single. Time. 

Without deduplication, a 10-minute outage would spawn 40+ identical incidents, confusing the engineering team and wasting money on AI API calls. Redis provides a centralized, ultra-fast cache to remember what we've seen recently.

### Why Enforce Multi-Tenancy at the API Boundary?
Multi-tenancy (isolating Tenant A's data from Tenant B) is the hardest part of building B2B software.
If Tenant A sends a payload: `{"tenant_id": "tenant_B", "service": "payment"}`, we CANNOT trust the body of the request. 

We built the `shared/auth.py` middleware to extract the `tenant_id` **exclusively from the cryptographically signed JWT token**. We then override the `tenant_id` on the `AlertEvent` object. This ensures mathematical certainty that a tenant cannot spoof alerts into another tenant's workspace.

---

## 3. How It Works Under the Hood

### The Request Lifecycle

1. **The Request Arrives:** A `POST /alerts/prometheus` request hits the FastAPI router.
2. **Authentication (`shared/auth.py`):** 
   - The `extract_tenant` dependency looks at the `Authorization: Bearer <token>` header.
   - It decodes the JWT using our `settings.jwt_secret_key`.
   - If successful, it extracts the `tenant_id` and attaches it to the FastAPI Request state. (In development, it falls back to a `default` tenant if no token is provided).
3. **Normalization (`normalizers.py`):**
   - The payload is parsed into an array of `AlertEvent` Pydantic models.
4. **Boundary Enforcement:**
   - The code explicitly does: `alert.tenant_id = tenant_id` to overwrite any malicious data from the client.
5. **Deduplication (`publisher.py`):**
   - The system computes the fingerprint: `HighLatency:checkout-service:production`.
   - Note: *Because the fingerprint method on the model was updated to include `tenant_id`, the output is now `acme_corp:HighLatency:checkout-service:production`.*
   - It asks Redis: `EXISTS alert:dedup:{tenant_id}:{fingerprint}`. 
   - If yes, we skip and return `200 OK` (Duplicate).
   - If no, we set the Redis key with a 10-minute Expiration TTL.
6. **Kafka Publishing:**
   - The `AlertEvent` is serialized to JSON.
   - We publish to `alerts.raw`. 
   - **Crucial Multi-Tenancy Step:** We use `{tenant_id}:{alert_id}` as the Kafka message `key`. Kafka guarantees that all messages with the same key are written to the exact same partition, ensuring ordered processing per tenant per alert. We also attach `{"tenant_id": "acme_corp"}` to the Kafka Headers.

---

## 4. How to Test It

Because this is a microservice architecture, we must run the infrastructure (Kafka, Redis, Postgres) alongside the Python application.

### Step 1: Start the Infrastructure
Open a terminal at the project root:
```bash
cd infra
docker compose up -d
```
*Wait a few seconds for Zookeeper and Kafka to report healthy statuses.*

### Step 2: Start the Alert Ingestor
Open a **new** terminal window, activate your isolated Python environment, and start the FastAPI web server:
```bash
source venv/bin/activate
cd services/alert-ingestor
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
You should see `INFO: Application startup complete.`

### Step 3: Test Health & Initialization
In another terminal, ensure the service is running and connected to Redis and Kafka:
```bash
curl http://localhost:8000/health
```
*Expected: `{"status":"healthy", "redis":true, "kafka":true}`*

### Step 4: Test Ingestion (First Attempt)
Send a fake manual alert to the API:
```bash
curl -X POST http://localhost:8000/alerts/manual \
  -H "Content-Type: application/json" \
  -d '{
        "name": "HighLatencyAlert",
        "service": "checkout-service",
        "environment": "production",
        "summary": "Database queries are slow"
      }'
```
*Expected Response (`202 Accepted`):* `{"message":"Manual alert processed","alert_id":"<uuid>"}`

### Step 5: Test Deduplication (Second Attempt)
Instantly run the exact same `curl` command again.
*Expected Response (`200 OK`):* `{"message":"Duplicate manual alert skipped","fingerprint":"default:HighLatencyAlert:checkout-service:production"}`

**Why did this happen?** Because Redis cached the fingerprint. The Alert Ingestor realizes it has seen this exact alert in the last 10 minutes and intentionally drops it to protect the downstream AI pipeline.

### Step 6: Verify in Kafka
Open your browser to the local Kafka UI at [http://localhost:8080](http://localhost:8080).
Navigate to Topics -> `alerts.raw` -> Messages.
You will see exactly 1 message (because the duplicate was dropped). If you inspect the message, you will see the `tenant_id` header and the full JSON payload ready for Phase 3!
