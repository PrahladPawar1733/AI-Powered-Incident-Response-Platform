-- infra/postgres/init.sql
-- Runs once on first container start

-- ── Extensions ────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- trigram index for text search

-- ── Incidents ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS incidents (
    id                 SERIAL PRIMARY KEY,
    incident_id        TEXT UNIQUE NOT NULL,
    tenant_id          TEXT NOT NULL DEFAULT 'default',  -- multi-tenant isolation
    status             TEXT NOT NULL DEFAULT 'triaging',
    alert_name         TEXT NOT NULL,
    service            TEXT NOT NULL,
    environment        TEXT NOT NULL DEFAULT 'production',
    severity           TEXT,
    root_cause         TEXT,
    resolution_summary TEXT,
    mttr_seconds       INTEGER,
    trace_id           TEXT,
    raw_context        JSONB NOT NULL DEFAULT '{}',
    embedding          vector(1536),    -- populated by learning-loop after resolution
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at        TIMESTAMPTZ
);

-- ── Runbooks ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runbooks (
    id          SERIAL PRIMARY KEY,
    runbook_id  TEXT UNIQUE NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',  -- multi-tenant isolation
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    services    TEXT[] NOT NULL DEFAULT '{}',
    alert_names TEXT[] NOT NULL DEFAULT '{}',
    severity    TEXT NOT NULL,
    steps       JSONB NOT NULL DEFAULT '[]',
    tags        TEXT[] NOT NULL DEFAULT '{}',
    embedding   vector(1536),    -- populated when runbook is created/updated
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Audit log ─────────────────────────────────────────────────────
-- Every agent action, approval, and system event
CREATE TABLE IF NOT EXISTS audit_events (
    id          SERIAL PRIMARY KEY,
    event_id    TEXT UNIQUE NOT NULL DEFAULT gen_random_uuid()::text,
    incident_id TEXT REFERENCES incidents(incident_id),
    agent       TEXT NOT NULL,    -- 'triage', 'diagnosis', 'remediation', 'system'
    action      TEXT NOT NULL,    -- 'alert_received', 'runbook_found', 'pod_restarted'
    details     JSONB NOT NULL DEFAULT '{}',
    trace_id    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Approval requests ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approval_requests (
    id            SERIAL PRIMARY KEY,
    request_id    TEXT UNIQUE NOT NULL,
    incident_id   TEXT REFERENCES incidents(incident_id),
    action        JSONB NOT NULL,    -- the Action object awaiting approval
    risk_level    TEXT NOT NULL,
    slack_ts      TEXT,              -- Slack message timestamp for updating
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|expired
    approved_by   TEXT,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);

-- ── Indexes ───────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_incidents_status     ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_service    ON incidents(service);
CREATE INDEX IF NOT EXISTS idx_incidents_severity   ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_created    ON incidents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_incident       ON audit_events(incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_created        ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_status     ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant     ON incidents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_runbooks_tenant      ON runbooks(tenant_id);

-- pgvector HNSW index — much faster than exact search at scale
-- HNSW = Hierarchical Navigable Small World graph
-- ef_construction=128, m=16 are good defaults for < 1M vectors
CREATE INDEX IF NOT EXISTS idx_incidents_embedding ON incidents
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

CREATE INDEX IF NOT EXISTS idx_runbooks_embedding ON runbooks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- ── Auto-update updated_at ────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER incidents_touch
    BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TRIGGER runbooks_touch
    BEFORE UPDATE ON runbooks
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ── Seed data — starter runbooks ──────────────────────────────────
INSERT INTO runbooks (runbook_id, title, description, services, alert_names, severity, steps, tags)
VALUES
(
    'rb-001',
    'High error rate — application service',
    'Handles sustained error rate above 5% on any application service',
    ARRAY['payment-service', 'order-api', 'user-service'],
    ARRAY['HighErrorRate', 'ErrorRateCritical'],
    'P1',
    '[
        {"order":1,"description":"Check pod logs for error pattern","command":"kubectl logs -l app={service} --tail=100 | grep ERROR","automated":true},
        {"order":2,"description":"Check recent deployments","command":"kubectl rollout history deploy/{service}","automated":true},
        {"order":3,"description":"If recent deploy, rollback","command":"kubectl rollout undo deploy/{service}","automated":false},
        {"order":4,"description":"Scale up if resource-related","command":"kubectl scale deploy/{service} --replicas=5","automated":false}
    ]'::jsonb,
    ARRAY['error-rate', 'application', 'k8s']
),
(
    'rb-002',
    'Pod crash looping',
    'One or more pods are in CrashLoopBackOff state',
    ARRAY['*'],
    ARRAY['PodCrashLooping', 'KubePodCrashLooping'],
    'P2',
    '[
        {"order":1,"description":"Get crash reason from logs","command":"kubectl logs {pod} --previous","automated":true},
        {"order":2,"description":"Check resource limits","command":"kubectl describe pod {pod}","automated":true},
        {"order":3,"description":"Restart deployment if config issue","command":"kubectl rollout restart deploy/{service}","automated":false}
    ]'::jsonb,
    ARRAY['k8s', 'pod', 'crash']
),
(
    'rb-003',
    'Database connection pool exhausted',
    'Service cannot acquire DB connections — pool at max capacity',
    ARRAY['payment-service', 'order-api'],
    ARRAY['DBConnectionPoolExhausted', 'HighDBConnections'],
    'P1',
    '[
        {"order":1,"description":"Check current connection count","automated":true},
        {"order":2,"description":"Identify connection-leaking queries","automated":true},
        {"order":3,"description":"Restart app service to release leaked connections","automated":false},
        {"order":4,"description":"Increase pool size in config if recurring","automated":false}
    ]'::jsonb,
    ARRAY['database', 'connections', 'postgres']
)
ON CONFLICT (runbook_id) DO NOTHING;