-- 028: Add rate columns to tenant_token_usage for auditable billing
-- Records the per-1M-token rates in effect at insert time

SET search_path = helpdesk, public;

ALTER TABLE tenant_token_usage
  ADD COLUMN IF NOT EXISTS rate_input  NUMERIC(10, 4) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS rate_output NUMERIC(10, 4) DEFAULT 0;

COMMENT ON COLUMN tenant_token_usage.rate_input  IS 'USD per 1M input tokens at time of call';
COMMENT ON COLUMN tenant_token_usage.rate_output IS 'USD per 1M output tokens at time of call';
