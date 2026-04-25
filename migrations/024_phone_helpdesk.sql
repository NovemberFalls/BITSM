-- 024: Phone Helpdesk — ElevenLabs Conversational AI + Twilio
-- Tenant supplies their own credentials; we provision the ElevenLabs agent on their behalf.

BEGIN;

-- Per-tenant phone configuration (credentials stored Fernet-encrypted)
CREATE TABLE IF NOT EXISTS helpdesk.phone_configs (
    id                          SERIAL      PRIMARY KEY,
    tenant_id                   INTEGER     NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    is_active                   BOOLEAN     DEFAULT FALSE,
    -- Encrypted JSON: {twilio_account_sid, twilio_auth_token, twilio_phone_number, elevenlabs_api_key}
    credentials_encrypted       TEXT,
    -- ElevenLabs identifiers (populated after provisioning)
    elevenlabs_agent_id         TEXT,
    elevenlabs_phone_number_id  TEXT,
    -- Voice / persona
    voice_id                    TEXT        DEFAULT 'EXAVITQu4vr4xnSDxMaL',
    agent_name                  TEXT        DEFAULT 'Atlas',
    greeting_message            TEXT,
    -- On-call human transfer number (E.164 format, e.g. +14155552671)
    oncall_number               TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id)
);

-- One row per inbound call
CREATE TABLE IF NOT EXISTS helpdesk.phone_sessions (
    id                          SERIAL      PRIMARY KEY,
    tenant_id                   INTEGER     NOT NULL REFERENCES helpdesk.tenants(id),
    twilio_call_sid             TEXT,
    elevenlabs_conversation_id  TEXT        UNIQUE,
    caller_phone                TEXT,
    caller_email                TEXT,
    ticket_id                   INTEGER     REFERENCES helpdesk.tickets(id),
    -- active | resolved | transferred | email_collected | abandoned
    status                      TEXT        DEFAULT 'active',
    transfer_attempted          BOOLEAN     DEFAULT FALSE,
    transfer_succeeded          BOOLEAN     DEFAULT FALSE,
    transcript                  JSONB       DEFAULT '[]',
    summary                     TEXT,
    duration_seconds            INTEGER,
    started_at                  TIMESTAMPTZ DEFAULT NOW(),
    ended_at                    TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

-- Individual transfer attempts within a session
CREATE TABLE IF NOT EXISTS helpdesk.phone_transfer_attempts (
    id                  SERIAL      PRIMARY KEY,
    session_id          INTEGER     NOT NULL REFERENCES helpdesk.phone_sessions(id) ON DELETE CASCADE,
    oncall_number       TEXT        NOT NULL,
    outbound_call_sid   TEXT,
    -- pending | answered | timeout | failed
    status              TEXT        DEFAULT 'pending',
    attempted_at        TIMESTAMPTZ DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX ON helpdesk.phone_sessions(tenant_id);
CREATE INDEX ON helpdesk.phone_sessions(twilio_call_sid);
CREATE INDEX ON helpdesk.phone_sessions(elevenlabs_conversation_id);
CREATE INDEX ON helpdesk.phone_sessions(ticket_id);
CREATE INDEX ON helpdesk.phone_sessions(started_at DESC);
CREATE INDEX ON helpdesk.phone_transfer_attempts(session_id);

COMMIT;
