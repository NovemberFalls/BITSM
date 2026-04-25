-- Migration 026: Phone IVR, bilingual support, cost tracking, source fix
-- Applied: 2026-03-26

-- IVR / bilingual columns on phone_configs
ALTER TABLE helpdesk.phone_configs
  ADD COLUMN IF NOT EXISTS el_agent_id_es  TEXT,
  ADD COLUMN IF NOT EXISTS voice_id_es     TEXT DEFAULT 'f18RlRJGEw0TaGYwmk8B',
  ADD COLUMN IF NOT EXISTS agent_name_es   TEXT DEFAULT 'Sofia',
  ADD COLUMN IF NOT EXISTS tts_speed       NUMERIC(3,2) DEFAULT 1.15,
  ADD COLUMN IF NOT EXISTS ivr_enabled     BOOLEAN DEFAULT FALSE;

-- Cost tracking columns on phone_sessions
ALTER TABLE helpdesk.phone_sessions
  ADD COLUMN IF NOT EXISTS el_cost_credits      INTEGER,
  ADD COLUMN IF NOT EXISTS el_llm_input_tokens  INTEGER,
  ADD COLUMN IF NOT EXISTS el_llm_output_tokens INTEGER,
  ADD COLUMN IF NOT EXISTS twilio_cost_cents     NUMERIC(8,2);

-- Allow 'phone' as a ticket source (phone helpdesk creates tickets)
ALTER TABLE helpdesk.tickets DROP CONSTRAINT IF EXISTS tickets_source_check;
ALTER TABLE helpdesk.tickets ADD CONSTRAINT tickets_source_check
  CHECK (source = ANY (ARRAY[
    'web','email','voice','chat','api','teams','portal','chat_widget','phone'
  ]));
