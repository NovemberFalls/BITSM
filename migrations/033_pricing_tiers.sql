-- Migration 033: Pricing tier billing infrastructure
--
-- Adds the monthly cost-rollup table and BYOK key columns needed for the
-- 6-tier pricing model (free / trial / starter / pro / business / enterprise).
--
-- NOTE: Per-call usage logging is already handled by tenant_token_usage
-- (migrations 027-029). This migration does NOT recreate that table.
-- It adds:
--   1. api_usage_monthly  — fast monthly rollup for cap checks (upserted after
--                           each recorded call); avoids full-table aggregation
--                           on the hot path.
--   2. BYOK key columns   — encrypted AI provider keys for Enterprise tier
--                           tenants who bring their own Anthropic/OpenAI/Voyage
--                           credentials.
--
-- Idempotent: safe to run multiple times.

SET search_path TO helpdesk, public;

-- ============================================================
-- API USAGE MONTHLY ROLLUP
-- Fast cap-check table. Upserted after every tenant_token_usage
-- INSERT. Composite PK (tenant_id, month) is the upsert key.
-- "month" format: 'YYYY-MM' (CHAR(7)), matching the index on
-- tenant_token_usage via to_char(created_at, 'YYYY-MM').
-- ============================================================
CREATE TABLE IF NOT EXISTS api_usage_monthly (
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    month           CHAR(7) NOT NULL,           -- 'YYYY-MM'
    total_cost_usd  NUMERIC(10,4) NOT NULL DEFAULT 0,
    call_count      INT NOT NULL DEFAULT 0,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, month)
);

COMMENT ON TABLE api_usage_monthly IS
    'Monthly cost rollup per tenant. Upserted on every AI call. '
    'Primary use: fast cap enforcement without full-table aggregation.';

COMMENT ON COLUMN api_usage_monthly.month IS
    'Calendar month in YYYY-MM format, matching to_char(created_at, ''YYYY-MM'') '
    'on tenant_token_usage.';

-- ============================================================
-- BYOK KEY COLUMNS ON TENANTS
-- Enterprise-tier tenants supply their own AI provider keys.
-- Keys are Fernet-encrypted before storage (same pattern as
-- connectors.config_encrypted). NULL means "not set" — fall
-- back to platform keys or raise an error, per billing_service.
-- ============================================================
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS byok_anthropic_key TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS byok_openai_key    TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS byok_voyage_key    TEXT;

COMMENT ON COLUMN tenants.byok_anthropic_key IS
    'Fernet-encrypted Anthropic API key for Enterprise BYOK tenants. NULL = use platform key.';
COMMENT ON COLUMN tenants.byok_openai_key IS
    'Fernet-encrypted OpenAI API key for Enterprise BYOK tenants. NULL = use platform key.';
COMMENT ON COLUMN tenants.byok_voyage_key IS
    'Fernet-encrypted Voyage AI API key for Enterprise BYOK tenants. NULL = use platform key.';

-- ============================================================
-- DATA MIGRATION: map legacy 'paid' tier to 'starter'
-- Any tenant with plan_tier = 'paid' (the old default paid tier)
-- is mapped to 'starter' ($49.99/user/mo, $20/user/mo AI cap).
-- This is safe to run multiple times (WHERE clause is a no-op
-- if no 'paid' rows remain).
-- ============================================================
UPDATE tenants SET plan_tier = 'starter' WHERE plan_tier = 'paid';
