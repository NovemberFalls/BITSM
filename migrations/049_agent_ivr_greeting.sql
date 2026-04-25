-- Migration 049: Move IVR greeting from phone_configs (hardcoded en/es) to phone_agents
-- Each agent now owns its own IVR greeting text, enabling N-language IVR menus
-- TwiML auto-composes from active agents ordered by ivr_digit

BEGIN;

-- Add ivr_greeting column to phone_agents
ALTER TABLE helpdesk.phone_agents
  ADD COLUMN IF NOT EXISTS ivr_greeting TEXT;

-- Migrate existing greetings from phone_configs into matching agents
-- English greeting → agent with language='en'
UPDATE helpdesk.phone_agents pa
  SET ivr_greeting = pc.ivr_greeting_en
  FROM helpdesk.phone_configs pc
  WHERE pa.tenant_id = pc.tenant_id
    AND pa.language = 'en'
    AND pc.ivr_greeting_en IS NOT NULL
    AND pc.ivr_greeting_en != '';

-- Spanish greeting → agent with language='es'
UPDATE helpdesk.phone_agents pa
  SET ivr_greeting = pc.ivr_greeting_es
  FROM helpdesk.phone_configs pc
  WHERE pa.tenant_id = pc.tenant_id
    AND pa.language = 'es'
    AND pc.ivr_greeting_es IS NOT NULL
    AND pc.ivr_greeting_es != '';

COMMIT;
