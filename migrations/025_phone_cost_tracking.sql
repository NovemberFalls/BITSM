-- Phone session cost tracking
ALTER TABLE helpdesk.phone_sessions
  ADD COLUMN IF NOT EXISTS el_cost_credits   INTEGER,        -- ElevenLabs credits (divide by 10000 for USD)
  ADD COLUMN IF NOT EXISTS el_llm_input_tokens  INTEGER,
  ADD COLUMN IF NOT EXISTS el_llm_output_tokens INTEGER,
  ADD COLUMN IF NOT EXISTS twilio_cost_cents    NUMERIC(8,2); -- Twilio call cost in cents
