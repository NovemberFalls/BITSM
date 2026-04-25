-- 035: notification_templates — per-tenant email template overrides
-- Admins can customise subject, headline, and intro paragraph per event.
-- If no row exists for a tenant+event, the hardcoded defaults in
-- email_templates.py are used (full backward-compatibility).

CREATE TABLE helpdesk.notification_templates (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    event           VARCHAR(64) NOT NULL,
    subject_template TEXT NOT NULL,
    body_headline   TEXT NOT NULL,
    body_intro      TEXT NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, event)
);

CREATE INDEX idx_notification_templates_tenant ON helpdesk.notification_templates(tenant_id);
