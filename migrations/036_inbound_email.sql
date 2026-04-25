-- Migration 036: Inbound email-to-ticket support
-- Adds per-tenant toggle for the email ingestion channel.

ALTER TABLE helpdesk.tenants
  ADD COLUMN IF NOT EXISTS inbound_email_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN helpdesk.tenants.inbound_email_enabled
  IS 'When true, emails sent to {slug}@<configured-inbound-domain> create or update tickets.';
