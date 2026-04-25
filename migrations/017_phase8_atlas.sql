-- Migration 017: Phase 8 — Advanced Atlas Intelligence schema changes
-- Adds: SLA risk column, FCR/ROI tracking columns, high_effort flag, n8n execution log

BEGIN;

-- ============================================================
-- 1. SLA Risk column on tickets (for hourly risk prediction)
-- ============================================================
ALTER TABLE helpdesk.tickets
    ADD COLUMN IF NOT EXISTS sla_risk TEXT DEFAULT 'normal';

-- Add check constraint separately (idempotent pattern)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tickets_sla_risk_check'
    ) THEN
        ALTER TABLE helpdesk.tickets
            ADD CONSTRAINT tickets_sla_risk_check
            CHECK (sla_risk IN ('normal', 'at_risk', 'critical'));
    END IF;
END;
$$;

-- ============================================================
-- 2. FCR / ROI tracking columns on ticket_metrics
-- ============================================================
ALTER TABLE helpdesk.ticket_metrics
    ADD COLUMN IF NOT EXISTS resolution_type TEXT,
    ADD COLUMN IF NOT EXISTS ai_turns_before_resolve INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS was_escalated_from_ai BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS high_effort BOOLEAN DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ticket_metrics_resolution_type_check'
    ) THEN
        ALTER TABLE helpdesk.ticket_metrics
            ADD CONSTRAINT ticket_metrics_resolution_type_check
            CHECK (resolution_type IN ('ai_l1', 'ai_l2', 'human', 'hybrid'));
    END IF;
END;
$$;

-- ============================================================
-- 3. n8n execution log (for error tracking + latency benchmarks)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.n8n_execution_log (
    id              SERIAL PRIMARY KEY,
    workflow_id     TEXT,
    workflow_name   TEXT,
    node_name       TEXT,
    tenant_id       INT REFERENCES helpdesk.tenants(id) ON DELETE SET NULL,
    ticket_id       INT REFERENCES helpdesk.tickets(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'error',  -- 'success', 'error', 'warning'
    error_message   TEXT,
    duration_ms     INT,
    payload         JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_n8n_log_tenant    ON helpdesk.n8n_execution_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_n8n_log_created   ON helpdesk.n8n_execution_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_n8n_log_status    ON helpdesk.n8n_execution_log(status) WHERE status = 'error';
CREATE INDEX IF NOT EXISTS idx_n8n_log_workflow  ON helpdesk.n8n_execution_log(workflow_id);

-- ============================================================
-- 4. Add auto_closed status to audit queue
-- ============================================================
-- The existing check constraint on ticket_audit_queue.status needs updating
-- to include 'auto_closed' as a valid status
DO $$
BEGIN
    -- Drop old constraint if it exists
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ticket_audit_queue_status_check'
    ) THEN
        ALTER TABLE helpdesk.ticket_audit_queue
            DROP CONSTRAINT ticket_audit_queue_status_check;
    END IF;

    -- Add updated constraint with auto_closed
    ALTER TABLE helpdesk.ticket_audit_queue
        ADD CONSTRAINT ticket_audit_queue_status_check
        CHECK (status IN ('pending', 'reviewed', 'approved', 'dismissed', 'auto_closed'));
EXCEPTION
    WHEN others THEN NULL;  -- Constraint may not exist in all environments
END;
$$;

COMMIT;
