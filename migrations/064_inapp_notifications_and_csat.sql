-- 064: In-app notification bell + CSAT email support
-- Adds read_at column for tracking dismissed in-app notifications
-- and csat_surveys table for tracking satisfaction survey responses.

-- Update channel constraint: add 'slack_webhook', keep 'n8n' for backward compat
ALTER TABLE helpdesk.notifications DROP CONSTRAINT IF EXISTS notifications_channel_check;
ALTER TABLE helpdesk.notifications ADD CONSTRAINT notifications_channel_check
    CHECK (channel IN ('teams_webhook', 'email', 'in_app', 'n8n', 'slack_webhook'));

-- read_at for in-app notification dismissal
ALTER TABLE helpdesk.notifications ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;

-- Index for efficient unread in-app queries
CREATE INDEX IF NOT EXISTS idx_notifications_inapp_unread
    ON helpdesk.notifications(tenant_id, created_at DESC)
    WHERE channel = 'in_app' AND read_at IS NULL;

-- CSAT survey tracking
CREATE TABLE IF NOT EXISTS helpdesk.csat_surveys (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES helpdesk.tenants(id),
    ticket_id       INT NOT NULL REFERENCES helpdesk.tickets(id),
    requester_id    INT REFERENCES helpdesk.users(id),
    rating          INT CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT,
    token           TEXT NOT NULL UNIQUE,
    email_sent_at   TIMESTAMPTZ,
    responded_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_csat_token ON helpdesk.csat_surveys(token);
CREATE INDEX IF NOT EXISTS idx_csat_ticket ON helpdesk.csat_surveys(ticket_id);
