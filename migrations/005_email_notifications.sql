-- Migration 005: Email notification preferences + tenant email config
-- Adds per-tenant notification preferences (which events trigger which channels)
-- and email configuration columns on tenants for Resend integration.

-- Per-tenant notification preferences (which events trigger which channels)
CREATE TABLE IF NOT EXISTS notification_preferences (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    event       TEXT NOT NULL,      -- e.g. 'ticket_created', 'ticket_resolved', 'agent_reply'
    channel     TEXT NOT NULL,      -- 'email', 'teams_webhook', 'in_app'
    role_target TEXT NOT NULL,      -- 'requester', 'assignee', 'group', 'all_agents'
    enabled     BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, event, channel, role_target)
);
CREATE INDEX IF NOT EXISTS idx_notif_prefs_tenant ON notification_preferences(tenant_id);

-- Seed default email preferences for existing tenants
INSERT INTO notification_preferences (tenant_id, event, channel, role_target)
SELECT t.id, e.event, 'email', e.role_target
FROM tenants t
CROSS JOIN (VALUES
    ('ticket_created',   'requester'),
    ('ticket_created',   'assignee'),
    ('ticket_assigned',  'assignee'),
    ('ticket_resolved',  'requester'),
    ('ticket_closed',    'requester'),
    ('agent_reply',      'requester'),
    ('requester_reply',  'assignee'),
    ('sla_warning',      'assignee'),
    ('sla_breach',       'assignee')
) AS e(event, role_target)
ON CONFLICT DO NOTHING;

-- Add email config columns to tenants (Resend from-address, reply-to)
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email_from_address TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email_from_name TEXT;
