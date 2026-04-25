-- Migration 077: Fix stale domain reference in column comment
-- The inbound email domain is now configurable per tenant (INBOUND_EMAIL_DOMAIN env var
-- + per-tenant inbound_email_domain setting), so the comment should not reference a
-- specific domain.

SET search_path TO helpdesk, public;

COMMENT ON COLUMN helpdesk.tenants.inbound_email_enabled
  IS 'When true, inbound emails for this tenant create or update tickets via the configured email domain.';
