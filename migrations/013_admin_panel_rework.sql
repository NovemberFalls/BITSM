-- 013: Admin panel rework — feature modules, user invite, category severity
-- Run: sudo -u postgres psql -d helpdesk -f migrations/013_admin_panel_rework.sql

SET search_path = helpdesk, public;

-- ============================================================
-- 1. Feature Modules — add module_type to knowledge_modules
-- ============================================================

ALTER TABLE knowledge_modules
  ADD COLUMN IF NOT EXISTS module_type TEXT NOT NULL DEFAULT 'knowledge';

-- Only add constraint if not exists
DO $$ BEGIN
  ALTER TABLE knowledge_modules
    ADD CONSTRAINT knowledge_modules_type_check CHECK (module_type IN ('knowledge', 'feature'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Mark AI Chat as a feature module
UPDATE knowledge_modules SET module_type = 'feature' WHERE slug = 'ai_chat';

-- Seed new feature modules
INSERT INTO knowledge_modules (slug, name, description, icon, module_type) VALUES
  ('customer_portal', 'Customer Portal', 'Self-service portal for end users', 'layout', 'feature'),
  ('phone_support', 'Phone Support', 'Voice call support channel', 'phone', 'feature'),
  ('asset_management', 'Asset Management', 'IT asset tracking and management', 'monitor', 'feature')
ON CONFLICT (slug) DO UPDATE SET module_type = 'feature';

-- ============================================================
-- 2. User Invite Fields
-- ============================================================

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS first_name TEXT,
  ADD COLUMN IF NOT EXISTS last_name TEXT,
  ADD COLUMN IF NOT EXISTS phone TEXT,
  ADD COLUMN IF NOT EXISTS invite_status TEXT DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS invited_by INT REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS invited_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Add check constraint separately to handle idempotency
DO $$ BEGIN
  ALTER TABLE users
    ADD CONSTRAINT users_invite_status_check CHECK (invite_status IN ('invited', 'active', 'expired', 'revoked'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Migrate existing name data into first_name / last_name
UPDATE users SET
  first_name = CASE WHEN POSITION(' ' IN COALESCE(name,'')) > 0
    THEN SUBSTRING(name FROM 1 FOR POSITION(' ' IN name) - 1) ELSE name END,
  last_name = CASE WHEN POSITION(' ' IN COALESCE(name,'')) > 0
    THEN SUBSTRING(name FROM POSITION(' ' IN name) + 1) ELSE NULL END
WHERE first_name IS NULL AND name IS NOT NULL;

-- ============================================================
-- 3. Category Severity (default_priority on problem_categories)
-- ============================================================

ALTER TABLE problem_categories
  ADD COLUMN IF NOT EXISTS default_priority TEXT;

DO $$ BEGIN
  ALTER TABLE problem_categories
    ADD CONSTRAINT problem_categories_priority_check CHECK (default_priority IN ('p1', 'p2', 'p3', 'p4'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
