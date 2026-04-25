-- Migration 054: One-time backfill — reconcile token usage data
--
-- Context (2026-03-28):
--   ~1000 existing rows in tenant_token_usage from before today's fixes.
--   Issues addressed:
--     1. rate_input / rate_output NULL for rows predating migration 028
--     2. cost_usd may have been computed with stale or wrong rates
--     3. api_usage_monthly (migration 033) never populated for older calls
--     4. Phone session costs never rolled into api_usage_monthly
--
--   Embedding query-time calls were never tracked — those are lost and
--   cannot be backfilled.
--
-- Idempotent: safe to run multiple times. Uses ON CONFLICT and
-- conditional updates (WHERE rate_input IS NULL).

SET search_path TO helpdesk, public;

BEGIN;

-- ==========================================================================
-- 2a. Backfill rate columns (USD per 1M tokens)
-- ==========================================================================

-- Haiku models
UPDATE tenant_token_usage
SET rate_input = 0.80, rate_output = 4.00
WHERE model LIKE '%haiku%' AND (rate_input IS NULL OR rate_input = 0);

-- Sonnet models
UPDATE tenant_token_usage
SET rate_input = 3.00, rate_output = 15.00
WHERE model LIKE '%sonnet%' AND (rate_input IS NULL OR rate_input = 0);

-- Voyage-3 (embedding)
UPDATE tenant_token_usage
SET rate_input = 0.06, rate_output = 0
WHERE model = 'voyage-3' AND (rate_input IS NULL OR rate_input = 0);

-- Voyage-3-lite (embedding)
UPDATE tenant_token_usage
SET rate_input = 0.02, rate_output = 0
WHERE model = 'voyage-3-lite' AND (rate_input IS NULL OR rate_input = 0);

-- OpenAI text-embedding-3-small (embedding)
UPDATE tenant_token_usage
SET rate_input = 0.02, rate_output = 0
WHERE model = 'text-embedding-3-small' AND (rate_input IS NULL OR rate_input = 0);

-- OpenAI gpt-4o-mini (failover)
UPDATE tenant_token_usage
SET rate_input = 0.15, rate_output = 0.60
WHERE model = 'gpt-4o-mini' AND (rate_input IS NULL OR rate_input = 0);

-- OpenAI gpt-4o (failover)
UPDATE tenant_token_usage
SET rate_input = 5.00, rate_output = 15.00
WHERE model = 'gpt-4o' AND (rate_input IS NULL OR rate_input = 0);

-- Catch-all: any remaining unknown models get zero rates
-- (better than leaving NULL — makes the cost_usd recalculation safe)
UPDATE tenant_token_usage
SET rate_input = 0, rate_output = 0
WHERE rate_input IS NULL OR rate_input = 0;


-- ==========================================================================
-- 2b. Recalculate cost_usd from tokens × rates for ALL rows
-- ==========================================================================

UPDATE tenant_token_usage
SET cost_usd = (input_tokens * rate_input + output_tokens * rate_output) / 1000000.0
WHERE rate_input IS NOT NULL;


-- ==========================================================================
-- 2c. Rebuild api_usage_monthly from tenant_token_usage
-- ==========================================================================

TRUNCATE api_usage_monthly;

INSERT INTO api_usage_monthly (tenant_id, month, total_cost_usd, call_count, last_updated)
SELECT
    tenant_id,
    to_char(created_at, 'YYYY-MM') AS month,
    SUM(cost_usd),
    COUNT(*),
    MAX(created_at)
FROM tenant_token_usage
WHERE tenant_id IS NOT NULL
GROUP BY tenant_id, to_char(created_at, 'YYYY-MM');


-- ==========================================================================
-- 2d. Backfill phone session costs into api_usage_monthly
--     ElevenLabs credits / 10000 = USD (per 025_phone_cost_tracking.sql)
-- ==========================================================================

INSERT INTO api_usage_monthly (tenant_id, month, total_cost_usd, call_count, last_updated)
SELECT
    tenant_id,
    to_char(created_at, 'YYYY-MM') AS month,
    SUM(el_cost_credits / 10000.0),
    COUNT(*),
    MAX(created_at)
FROM phone_sessions
WHERE tenant_id IS NOT NULL
  AND el_cost_credits IS NOT NULL
  AND el_cost_credits > 0
GROUP BY tenant_id, to_char(created_at, 'YYYY-MM')
ON CONFLICT (tenant_id, month) DO UPDATE
    SET total_cost_usd = api_usage_monthly.total_cost_usd + EXCLUDED.total_cost_usd,
        call_count     = api_usage_monthly.call_count + EXCLUDED.call_count,
        last_updated   = GREATEST(api_usage_monthly.last_updated, EXCLUDED.last_updated);

COMMIT;
