-- 076_byok_resend.sql
-- Add BYOK column for Resend API key on the tenants table.
--
-- Enterprise-tier tenants who bring their own Resend account supply their
-- API key through the BYOK key management UI (/api/billing/byok).  The
-- backend stores it Fernet-encrypted, identical to the existing BYOK
-- columns added in migrations 033 (Anthropic/OpenAI/Voyage) and 075
-- (Twilio/ElevenLabs).
--
-- NULL means "not set" — callers fall back to the platform Resend key
-- (RESEND_API_KEY env var).
--
-- Idempotent: safe to run multiple times (IF NOT EXISTS on the ALTER).

SET search_path TO helpdesk, public;

-- ============================================================
-- RESEND BYOK COLUMN
-- Tenants supply their own Resend API key for transactional
-- email delivery.  NULL falls back to RESEND_API_KEY platform
-- credential.
-- ============================================================
ALTER TABLE helpdesk.tenants ADD COLUMN IF NOT EXISTS byok_resend_api_key TEXT;

COMMENT ON COLUMN helpdesk.tenants.byok_resend_api_key IS
    'Fernet-encrypted Resend API key for BYOK tenants. NULL = use platform Resend key.';
