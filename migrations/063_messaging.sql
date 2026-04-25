-- 063_messaging.sql — SMS & WhatsApp messaging channels
-- Extends phone_configs with messaging toggles, adds conversation/message/template tables

-- ============================================================
-- 1. Extend phone_configs with messaging channel settings
-- ============================================================

ALTER TABLE helpdesk.phone_configs
    ADD COLUMN IF NOT EXISTS sms_enabled             BOOLEAN   DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS whatsapp_enabled         BOOLEAN   DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS whatsapp_phone_number    TEXT,          -- E.164, if different from voice number
    ADD COLUMN IF NOT EXISTS whatsapp_status          TEXT      DEFAULT 'not_configured',  -- not_configured | sandbox | pending | approved
    ADD COLUMN IF NOT EXISTS messaging_auto_reply     BOOLEAN   DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS messaging_auto_reply_msg TEXT,
    ADD COLUMN IF NOT EXISTS messaging_auto_create_ticket BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS messaging_default_language   TEXT  DEFAULT 'en';

-- ============================================================
-- 2. Messaging conversations (threaded by contact + channel)
-- ============================================================

CREATE TABLE IF NOT EXISTS helpdesk.messaging_conversations (
    id                SERIAL       PRIMARY KEY,
    tenant_id         INTEGER      NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    channel           TEXT         NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    contact_phone     TEXT         NOT NULL,   -- E.164
    contact_name      TEXT,
    contact_email     TEXT,
    user_id           INTEGER      REFERENCES helpdesk.users(id),  -- linked BITSM user if identified
    language          TEXT         DEFAULT 'en',
    ticket_id         INTEGER      REFERENCES helpdesk.tickets(id),
    status            TEXT         DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'archived')),
    last_message_at   TIMESTAMPTZ,
    last_inbound_at   TIMESTAMPTZ,   -- WhatsApp 24h session window tracking
    message_count     INTEGER      DEFAULT 0,
    created_at        TIMESTAMPTZ  DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE(tenant_id, channel, contact_phone)
);

-- ============================================================
-- 3. Individual messages
-- ============================================================

CREATE TABLE IF NOT EXISTS helpdesk.messages (
    id                SERIAL       PRIMARY KEY,
    conversation_id   INTEGER      NOT NULL REFERENCES helpdesk.messaging_conversations(id) ON DELETE CASCADE,
    tenant_id         INTEGER      NOT NULL REFERENCES helpdesk.tenants(id),
    direction         TEXT         NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel           TEXT         NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    body              TEXT,
    media_url         TEXT,                    -- MMS / WhatsApp media
    twilio_message_sid TEXT,
    status            TEXT         DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'delivered', 'read', 'failed', 'received')),
    error_code        TEXT,
    error_message     TEXT,
    segments          INTEGER      DEFAULT 1,  -- SMS segment count
    cost_cents        NUMERIC(10,4),
    language          TEXT,
    template_name     TEXT,                    -- WhatsApp template if used
    sender_user_id    INTEGER      REFERENCES helpdesk.users(id),  -- agent who sent outbound
    created_at        TIMESTAMPTZ  DEFAULT NOW()
);

-- ============================================================
-- 4. WhatsApp message templates
-- ============================================================

CREATE TABLE IF NOT EXISTS helpdesk.messaging_templates (
    id                  SERIAL       PRIMARY KEY,
    tenant_id           INTEGER      NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    name                TEXT         NOT NULL,
    language            TEXT         NOT NULL DEFAULT 'en',
    body                TEXT         NOT NULL,
    category            TEXT         DEFAULT 'utility',   -- utility | marketing | authentication
    status              TEXT         DEFAULT 'draft' CHECK (status IN ('draft', 'pending', 'approved', 'rejected')),
    twilio_template_sid TEXT,
    variables           JSONB        DEFAULT '[]',        -- placeholder variable definitions
    created_at          TIMESTAMPTZ  DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE(tenant_id, name, language)
);

-- ============================================================
-- 5. Permission
-- ============================================================

INSERT INTO helpdesk.permissions (slug, label, category, description)
VALUES ('messaging.manage', 'Manage Messaging', 'Configuration',
        'Can configure SMS/WhatsApp messaging, view conversations and send messages')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO helpdesk.group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM helpdesk.groups g
CROSS JOIN helpdesk.permissions p
WHERE g.name IN ('Admins', 'Managers') AND g.is_active = true AND p.slug = 'messaging.manage'
ON CONFLICT DO NOTHING;

-- ============================================================
-- 6. Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_msg_conv_tenant        ON helpdesk.messaging_conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv_phone          ON helpdesk.messaging_conversations(contact_phone);
CREATE INDEX IF NOT EXISTS idx_msg_conv_ticket          ON helpdesk.messaging_conversations(ticket_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv_status          ON helpdesk.messaging_conversations(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_msg_conv_last_msg        ON helpdesk.messaging_conversations(tenant_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation    ON helpdesk.messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_tenant          ON helpdesk.messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_messages_twilio_sid      ON helpdesk.messages(twilio_message_sid);
CREATE INDEX IF NOT EXISTS idx_messages_created         ON helpdesk.messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_templates_tenant     ON helpdesk.messaging_templates(tenant_id);
