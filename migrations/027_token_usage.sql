-- Migration 027: Per-tenant LLM token usage tracking
-- Every complete() call in llm_provider.py inserts a row here (fire-and-forget).
-- Enables cost dashboards, quota enforcement, and per-feature billing visibility.

SET search_path TO helpdesk, public;

CREATE TABLE IF NOT EXISTS tenant_token_usage (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    ticket_id    INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
    provider     TEXT NOT NULL DEFAULT 'anthropic',   -- 'anthropic' | 'openai'
    model        TEXT NOT NULL,                       -- full model name
    caller       TEXT NOT NULL DEFAULT '',            -- e.g. 'auto_tag', 'l1_chat', 'enrich'
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(12, 6) NOT NULL DEFAULT 0,   -- pre-computed at insert time
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Primary query pattern: monthly rollup per tenant
CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_month
    ON tenant_token_usage (tenant_id, date_trunc('month', created_at));

-- Secondary: all usage for a specific ticket
CREATE INDEX IF NOT EXISTS idx_token_usage_ticket
    ON tenant_token_usage (ticket_id)
    WHERE ticket_id IS NOT NULL;

-- Tertiary: recent rows across all tenants (super_admin dashboard)
CREATE INDEX IF NOT EXISTS idx_token_usage_created
    ON tenant_token_usage (created_at DESC);
