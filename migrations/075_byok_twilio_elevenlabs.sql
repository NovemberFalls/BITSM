-- 075_byok_twilio_elevenlabs.sql
-- Add BYOK columns for Twilio and ElevenLabs credentials on the tenants table.
--
-- Enterprise-tier tenants who bring their own Twilio account and/or ElevenLabs
-- API key supply those credentials through the BYOK key management UI
-- (/api/billing/byok).  The backend stores them Fernet-encrypted, identical to
-- the existing byok_anthropic_key / byok_openai_key / byok_voyage_key columns
-- added in migration 033.
--
-- NULL means "not set" — callers fall back to platform credentials
-- (PLATFORM_TWILIO_* / PLATFORM_ELEVENLABS_API_KEY env vars).
--
-- Idempotent: safe to run multiple times (IF NOT EXISTS on every ALTER).

SET search_path TO helpdesk, public;

-- ============================================================
-- TWILIO BYOK COLUMNS
-- Tenants supply their own Twilio account for SMS, WhatsApp,
-- and voice services.  All three values are required together
-- for a usable Twilio credential set; individual NULLs fall
-- back to the platform pool.
-- ============================================================
ALTER TABLE helpdesk.tenants ADD COLUMN IF NOT EXISTS byok_twilio_account_sid  TEXT;
ALTER TABLE helpdesk.tenants ADD COLUMN IF NOT EXISTS byok_twilio_auth_token   TEXT;
ALTER TABLE helpdesk.tenants ADD COLUMN IF NOT EXISTS byok_twilio_phone_number TEXT;

COMMENT ON COLUMN helpdesk.tenants.byok_twilio_account_sid IS
    'Fernet-encrypted Twilio Account SID for BYOK tenants. NULL = use platform Twilio account.';
COMMENT ON COLUMN helpdesk.tenants.byok_twilio_auth_token IS
    'Fernet-encrypted Twilio Auth Token for BYOK tenants. NULL = use platform Twilio account.';
COMMENT ON COLUMN helpdesk.tenants.byok_twilio_phone_number IS
    'Fernet-encrypted Twilio phone number (E.164) for BYOK tenants. NULL = use platform number.';

-- ============================================================
-- ELEVENLABS BYOK COLUMN
-- Tenants supply their own ElevenLabs API key for voice agent
-- synthesis.  NULL falls back to PLATFORM_ELEVENLABS_API_KEY.
-- ============================================================
ALTER TABLE helpdesk.tenants ADD COLUMN IF NOT EXISTS byok_elevenlabs_api_key TEXT;

COMMENT ON COLUMN helpdesk.tenants.byok_elevenlabs_api_key IS
    'Fernet-encrypted ElevenLabs API key for BYOK tenants. NULL = use platform ElevenLabs key.';
