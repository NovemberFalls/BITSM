-- Migration 047: Phone agents — multi-agent support
--
-- Introduces phone_agents as first-class entities.  Each tenant can have N
-- agents, each with its own persona, voice, AI settings, system prompt, and
-- routing rules.  phone_configs remains the tenant-level settings table
-- (credentials, phone number, credential mode, IVR greeting).
--
-- Atlas (EN) and Astra (ES) are migrated from phone_configs columns into
-- phone_agents rows so existing deployments keep working.
--
-- Idempotent: safe to run multiple times.

BEGIN;

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. Create phone_agents table
-- ============================================================

CREATE TABLE IF NOT EXISTS helpdesk.phone_agents (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES helpdesk.tenants(id),
    slug                TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT 'Atlas',
    language            TEXT NOT NULL DEFAULT 'en',

    -- ElevenLabs
    el_agent_id         TEXT,

    -- Persona
    voice_id            TEXT,
    greeting_message    TEXT,
    system_prompt       TEXT,               -- NULL = use platform default template

    -- AI Settings (NULL = use PHONE_DEFAULTS)
    llm_model           TEXT,
    temperature         NUMERIC(3,2),
    turn_timeout        NUMERIC(4,1),
    audio_format        TEXT,
    tts_speed           NUMERIC(3,2),

    -- Routing
    ivr_digit           TEXT,               -- which digit routes here (NULL = default/fallback)
    oncall_number       TEXT,               -- E.164 for human transfer (per-agent)

    -- Status
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    is_deployed         BOOLEAN NOT NULL DEFAULT FALSE,
    is_number_linked    BOOLEAN NOT NULL DEFAULT FALSE,

    -- Tools
    tools_enabled       TEXT[] DEFAULT ARRAY['search_kb','create_ticket','identify_caller','attempt_transfer','collect_email'],

    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_phone_agents_tenant
    ON helpdesk.phone_agents(tenant_id);

COMMENT ON TABLE helpdesk.phone_agents IS
    'Per-tenant voice agents.  Each row = one ElevenLabs ConvAI agent with its own persona, voice, and routing rules.';

COMMENT ON COLUMN helpdesk.phone_agents.system_prompt IS
    'Custom system prompt override.  NULL = use platform default template (_atlas_system_prompt / _astra_system_prompt).';

COMMENT ON COLUMN helpdesk.phone_agents.ivr_digit IS
    'IVR digit that routes to this agent (e.g. "1" for English, "2" for Spanish).  NULL = fallback/default agent.';


-- ============================================================
-- 2. Add phone_agent_id FK to phone_sessions
-- ============================================================

ALTER TABLE helpdesk.phone_sessions
    ADD COLUMN IF NOT EXISTS phone_agent_id INTEGER REFERENCES helpdesk.phone_agents(id);

COMMENT ON COLUMN helpdesk.phone_sessions.phone_agent_id IS
    'Which phone agent handled this call session.';


-- ============================================================
-- 3. Migrate existing Atlas/Astra data into phone_agents
--
--    For each phone_configs row that has an elevenlabs_agent_id,
--    create an Atlas (EN) agent.  If el_agent_id_es exists, also
--    create an Astra (ES) agent.
--
--    ON CONFLICT DO NOTHING ensures re-running is safe.
-- ============================================================

-- Atlas (English) — from existing phone_configs
INSERT INTO helpdesk.phone_agents (
    tenant_id, slug, name, language,
    el_agent_id, voice_id, greeting_message,
    llm_model, temperature, turn_timeout, audio_format, tts_speed,
    oncall_number,
    ivr_digit, is_active, is_deployed, is_number_linked,
    sort_order
)
SELECT
    pc.tenant_id,
    'atlas',
    COALESCE(pc.agent_name, 'Atlas'),
    'en',
    pc.elevenlabs_agent_id,
    pc.voice_id,
    pc.greeting_message,
    pc.llm_model,
    pc.temperature,
    pc.turn_timeout,
    pc.audio_format,
    pc.tts_speed,
    pc.oncall_number,
    '1',
    pc.is_active AND pc.elevenlabs_agent_id IS NOT NULL,
    pc.elevenlabs_agent_id IS NOT NULL,
    pc.elevenlabs_phone_number_id IS NOT NULL,
    0
FROM helpdesk.phone_configs pc
ON CONFLICT (tenant_id, slug) DO NOTHING;

-- Astra (Spanish) — from existing phone_configs.el_agent_id_es
INSERT INTO helpdesk.phone_agents (
    tenant_id, slug, name, language,
    el_agent_id, voice_id,
    llm_model, temperature, turn_timeout, audio_format, tts_speed,
    oncall_number,
    ivr_digit, is_active, is_deployed, is_number_linked,
    sort_order
)
SELECT
    pc.tenant_id,
    'astra',
    COALESCE(pc.agent_name_es, 'Astra'),
    'es',
    pc.el_agent_id_es,
    pc.voice_id_es,
    pc.llm_model,
    pc.temperature,
    pc.turn_timeout,
    pc.audio_format,
    pc.tts_speed,
    pc.oncall_number,
    '2',
    pc.is_active AND pc.el_agent_id_es IS NOT NULL,
    pc.el_agent_id_es IS NOT NULL,
    pc.elevenlabs_phone_number_id IS NOT NULL,
    1
FROM helpdesk.phone_configs pc
WHERE pc.el_agent_id_es IS NOT NULL
ON CONFLICT (tenant_id, slug) DO NOTHING;

COMMIT;
