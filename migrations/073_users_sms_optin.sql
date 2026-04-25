-- Migration 073: SMS opt-in fields on users
-- Adds the three columns required for A2P 10DLC compliance:
--   phone_number    — the number the user wants SMS sent to (separate from
--                     any number stored on phone_configs / locations, which
--                     are operational numbers, not consent targets)
--   sms_opted_in    — explicit boolean consent flag; defaults false so all
--                     existing rows start in an un-opted state
--   sms_opted_in_at — TIMESTAMPTZ audit stamp written at the moment of
--                     consent; NULL means the user has never opted in
--
-- All three columns are nullable or carry defaults so this migration is safe
-- against existing rows and requires no back-fill before the app ships the
-- opt-in UI.

SET search_path TO helpdesk, public;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS phone_number    VARCHAR(20),
    ADD COLUMN IF NOT EXISTS sms_opted_in    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS sms_opted_in_at TIMESTAMPTZ;

-- Partial index: only indexes the opted-in population, keeping index size
-- small while making "find all opted-in users for tenant X" fast.
CREATE INDEX IF NOT EXISTS idx_users_sms_optin
    ON users (tenant_id, sms_opted_in)
    WHERE sms_opted_in = true;
