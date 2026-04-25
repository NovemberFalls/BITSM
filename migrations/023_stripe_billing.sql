-- Migration 023: Stripe billing columns + trial expiry index
-- Adds stripe tracking columns to tenants and ensures api_usage_monthly is indexed.

ALTER TABLE helpdesk.tenants
    ADD COLUMN IF NOT EXISTS stripe_customer_id      TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id  TEXT,
    ADD COLUMN IF NOT EXISTS stripe_price_id         TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS tenants_stripe_customer_id_idx
    ON helpdesk.tenants (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS tenants_plan_expires_at_idx
    ON helpdesk.tenants (plan_expires_at)
    WHERE plan_expires_at IS NOT NULL;
