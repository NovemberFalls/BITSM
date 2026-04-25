-- Migration 015: Incident linking + tenant plan tiers
-- Adds parent_ticket_id for incident grouping (never merge, always group)
-- Adds plan_tier + plan_expires_at for paid feature gating
-- Adds pg_trgm for similar ticket text search

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. Incident linking on tickets
-- ============================================================

-- Parent ticket for incident grouping (child tickets reference the parent)
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS parent_ticket_id INT REFERENCES tickets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_parent_id ON tickets(parent_ticket_id) WHERE parent_ticket_id IS NOT NULL;

-- ============================================================
-- 2. Tenant plan tiers
-- ============================================================

-- Plan tier: 'free' (default), 'trial', 'paid'
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_tier TEXT NOT NULL DEFAULT 'free';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_extended_by INT REFERENCES users(id);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_extended_at TIMESTAMPTZ;

-- Index for quick tier lookups
CREATE INDEX IF NOT EXISTS idx_tenants_plan_tier ON tenants(plan_tier);

-- ============================================================
-- 3. pg_trgm for fuzzy text search (similar ticket detection)
-- ============================================================

-- Enable trigram extension (for similarity matching)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN trigram index on ticket subject for fast similarity queries
CREATE INDEX IF NOT EXISTS idx_tickets_subject_trgm ON tickets USING gin (subject gin_trgm_ops);

-- ============================================================
-- 4. Atlas engagement enhancements
-- ============================================================

-- Track KB articles referenced in auto-engage analysis
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS kb_articles_referenced TEXT[];
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS similar_ticket_ids INT[];
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS suggested_category_id INT REFERENCES problem_categories(id);
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS category_confidence FLOAT;
