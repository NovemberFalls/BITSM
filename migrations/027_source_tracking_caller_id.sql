-- Migration 027: Source tracking for users/locations + phone caller identification
-- Applied: 2026-03-26

-- ─────────────────────────────────────────────────────────
-- Source tracking: users
-- ─────────────────────────────────────────────────────────
ALTER TABLE helpdesk.users
  ADD COLUMN IF NOT EXISTS created_via TEXT DEFAULT 'oauth';

DO $$ BEGIN
  ALTER TABLE helpdesk.users
    ADD CONSTRAINT users_created_via_check
    CHECK (created_via IN ('oauth', 'invite', 'import', 'phone', 'api', 'dev_auto'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backfill: invited users
UPDATE helpdesk.users SET created_via = 'invite'
  WHERE invited_by IS NOT NULL AND created_via = 'oauth';

-- Backfill: dev auto-provision user
UPDATE helpdesk.users SET created_via = 'dev_auto'
  WHERE email = 'dev@localhost' AND provider = 'dev';

-- ─────────────────────────────────────────────────────────
-- Source tracking: locations
-- ─────────────────────────────────────────────────────────
ALTER TABLE helpdesk.locations
  ADD COLUMN IF NOT EXISTS created_via TEXT DEFAULT 'manual';

DO $$ BEGIN
  ALTER TABLE helpdesk.locations
    ADD CONSTRAINT locations_created_via_check
    CHECK (created_via IN ('manual', 'db_sync', 'import', 'api'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─────────────────────────────────────────────────────────
-- Phone sessions: link identified caller
-- ─────────────────────────────────────────────────────────
ALTER TABLE helpdesk.phone_sessions
  ADD COLUMN IF NOT EXISTS caller_user_id INT REFERENCES helpdesk.users(id);

CREATE INDEX IF NOT EXISTS idx_phone_sessions_caller_user
  ON helpdesk.phone_sessions(caller_user_id);
