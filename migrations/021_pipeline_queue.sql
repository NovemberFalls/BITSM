-- Migration 021: Pipeline Queue System — replaces n8n orchestration
-- PostgreSQL-backed job queue with retry, priority, cron scheduling, and execution logging

SET search_path = helpdesk, public;

-- ============================================================
-- 1. Job Queue
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_queue (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES tenants(id) ON DELETE SET NULL,
    ticket_id       INT REFERENCES tickets(id) ON DELETE CASCADE,
    step_name       TEXT NOT NULL,
    priority        INT NOT NULL DEFAULT 10,          -- 1=P1 ... 4=P4, 10=cron (lower = higher)
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending, running, completed, failed, cancelled
    uses_llm        BOOLEAN NOT NULL DEFAULT false,
    payload         JSONB DEFAULT '{}',
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    last_error      TEXT,
    next_run_at     TIMESTAMPTZ DEFAULT now(),
    locked_by       TEXT,
    locked_at       TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    duration_ms     INT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Dispatch query: pending tasks ordered by priority, ready to run
CREATE INDEX idx_pq_dispatch ON pipeline_queue (priority, created_at)
    WHERE status = 'pending';
-- Fast lookup by ticket
CREATE INDEX idx_pq_ticket ON pipeline_queue (ticket_id);
-- Monitor running tasks
CREATE INDEX idx_pq_running ON pipeline_queue (status) WHERE status = 'running';
-- Stale task recovery
CREATE INDEX idx_pq_locked ON pipeline_queue (locked_at) WHERE status = 'running';
-- Cleanup old completed tasks
CREATE INDEX idx_pq_completed ON pipeline_queue (completed_at) WHERE status = 'completed';

-- ============================================================
-- 2. Execution Log (every run, success or failure)
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_execution_log (
    id              SERIAL PRIMARY KEY,
    queue_id        INT REFERENCES pipeline_queue(id) ON DELETE SET NULL,
    tenant_id       INT REFERENCES tenants(id) ON DELETE SET NULL,
    ticket_id       INT REFERENCES tickets(id) ON DELETE SET NULL,
    step_name       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'success',   -- success, error, skipped
    error_message   TEXT,
    duration_ms     INT,
    attempts        INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pel_created ON pipeline_execution_log (created_at DESC);
CREATE INDEX idx_pel_errors  ON pipeline_execution_log (status) WHERE status = 'error';
CREATE INDEX idx_pel_ticket  ON pipeline_execution_log (ticket_id);

-- ============================================================
-- 3. Cron Schedules
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_schedules (
    id                  SERIAL PRIMARY KEY,
    step_name           TEXT UNIQUE NOT NULL,
    cron_expression     TEXT NOT NULL,          -- 5-field cron (minute hour dom month dow)
    enabled             BOOLEAN DEFAULT true,
    last_enqueued_at    TIMESTAMPTZ,
    payload             JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Seed cron schedules
INSERT INTO pipeline_schedules (step_name, cron_expression, payload) VALUES
    ('sla_breach_check',  '*/15 * * * *', '{}'),        -- Every 15 min
    ('sla_risk_check',    '0 * * * *',    '{}'),         -- Hourly
    ('escalation_check',  '0 * * * *',    '{}'),         -- Hourly
    ('audit_auto_close',  '0 6 * * *',    '{}'),         -- Daily 6am
    ('tenant_health',     '0 7 * * *',    '{}'),         -- Daily 7am
    ('knowledge_gaps',    '0 3 * * 1',    '{"tenant_id": 1}'),  -- Weekly Mon 3am
    ('kb_freshness',      '0 4 * * 1',    '{}')          -- Weekly Mon 4am
ON CONFLICT (step_name) DO NOTHING;
