-- Migration 045: Phone config AI parameter columns + phone_support module sub-features
--
-- Part 1 — phone_configs AI columns
--   Surfaces four LLM parameters that are currently hardcoded in provision_agent().
--   NULL = "use backend default", so existing rows keep working unchanged.
--   tts_speed already has a column (added in 026); not re-added here.
--
-- Part 2 — module_features rows for the phone_support knowledge module
--   Gives the Phone Support card in the tenant admin grid the same expandable
--   sub-feature toggles that the AI card has.  The phone_support module was
--   seeded in migration 013.
--
-- Idempotent: safe to run multiple times.
--   ADD COLUMN IF NOT EXISTS  →  no-op if column exists
--   ON CONFLICT DO NOTHING    →  no-op if feature row exists

BEGIN;

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. Add AI parameter columns to phone_configs
-- ============================================================

ALTER TABLE helpdesk.phone_configs
    ADD COLUMN IF NOT EXISTS llm_model    TEXT           DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS temperature  NUMERIC(3,2)   DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS turn_timeout NUMERIC(4,1)   DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS audio_format TEXT           DEFAULT NULL;

COMMENT ON COLUMN helpdesk.phone_configs.llm_model IS
    'ElevenLabs LLM model identifier (e.g. claude-haiku-4-5@20251001). NULL = use backend default.';

COMMENT ON COLUMN helpdesk.phone_configs.temperature IS
    'LLM temperature 0.00-1.00. NULL = use backend default (0.7).';

COMMENT ON COLUMN helpdesk.phone_configs.turn_timeout IS
    'Seconds the agent waits before considering a turn complete. NULL = use backend default (10.0).';

COMMENT ON COLUMN helpdesk.phone_configs.audio_format IS
    'Audio codec for the ElevenLabs websocket stream (e.g. ulaw_8000, pcm_16000). NULL = use backend default.';

-- ============================================================
-- 2. Seed phone.manage permission
--
--    Routes/phone.py uses @require_permission("phone.manage") for all admin
--    endpoints.  Previously these used users.manage which was overloaded.
-- ============================================================

INSERT INTO helpdesk.permissions (slug, label, category, description)
VALUES ('phone.manage', 'Manage Phone Settings', 'Configuration',
        'Can configure phone helpdesk settings, credentials, and view call logs')
ON CONFLICT (slug) DO NOTHING;

-- Grant phone.manage to every existing "Admins" group (full admin access)
INSERT INTO helpdesk.group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM helpdesk.groups g
CROSS JOIN helpdesk.permissions p
WHERE g.name = 'Admins' AND g.is_active = true AND p.slug = 'phone.manage'
ON CONFLICT DO NOTHING;

-- Grant phone.manage to every existing "Managers" group
INSERT INTO helpdesk.group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM helpdesk.groups g
CROSS JOIN helpdesk.permissions p
WHERE g.name = 'Managers' AND g.is_active = true AND p.slug = 'phone.manage'
ON CONFLICT DO NOTHING;

-- ============================================================
-- 3. Seed module_features rows for the phone_support module
--
--    The phone_support module (slug='phone_support', type='feature')
--    was created in migration 013.  We add sub-feature toggles that
--    mirror the per-tenant toggle pattern established for the AI module
--    in migration 014.
-- ============================================================

INSERT INTO helpdesk.module_features (module_id, slug, name, description, icon, sort_order, is_active)
SELECT km.id, f.slug, f.name, f.description, f.icon, f.sort_order, true
FROM helpdesk.knowledge_modules km
CROSS JOIN (VALUES
    ('ivr_routing',          'IVR Bilingual Routing',    'Greet callers and route to English or Spanish agent based on key-press selection',  'git-branch',   0),
    ('phone_kb_search',      'KB Search During Calls',   'Agent searches the knowledge base in real-time to answer caller questions',          'search',       1),
    ('phone_ticket_create',  'Auto Ticket Creation',     'Automatically open a helpdesk ticket when a call ends without resolution',           'plus-circle',  2),
    ('phone_transfer',       'Human Transfer Fallback',  'Warm-transfer the caller to an on-call agent when the AI cannot resolve the issue',  'phone-forward', 3),
    ('phone_email_collect',  'Caller Email Collection',  'Collect caller email address mid-call for follow-up and contact profile matching',   'mail',         4)
) AS f(slug, name, description, icon, sort_order)
WHERE km.slug = 'phone_support'
ON CONFLICT (module_id, slug) DO NOTHING;

COMMIT;
