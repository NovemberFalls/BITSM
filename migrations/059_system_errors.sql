-- 059: System error log
-- Captures unhandled exceptions from the Flask error handler for operational visibility.
-- Scoped to super_admin only — no tenant data returned cross-tenant without explicit filter.

SET search_path TO helpdesk, public;

CREATE TABLE IF NOT EXISTS helpdesk.system_errors (
    id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity VARCHAR(20) NOT NULL DEFAULT 'error',  -- 'error' | 'warning' | 'critical'
    route VARCHAR(500),           -- request path
    method VARCHAR(10),           -- GET/POST/etc
    error_type VARCHAR(200),      -- exception class name
    message TEXT,                 -- exception message
    stack_trace TEXT,             -- full traceback
    tenant_id INTEGER REFERENCES helpdesk.tenants(id) ON DELETE SET NULL,
    user_id INTEGER REFERENCES helpdesk.users(id) ON DELETE SET NULL,
    request_id VARCHAR(100),      -- for correlation
    resolved BOOLEAN DEFAULT false,
    resolved_at TIMESTAMPTZ,
    notes TEXT                    -- admin notes on the error
);

CREATE INDEX IF NOT EXISTS idx_system_errors_occurred ON helpdesk.system_errors(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_errors_tenant ON helpdesk.system_errors(tenant_id);
CREATE INDEX IF NOT EXISTS idx_system_errors_resolved ON helpdesk.system_errors(resolved);
