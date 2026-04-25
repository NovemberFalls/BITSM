-- Migration 003: Add webhook_token_hash to connectors for location DB sync
-- Run: sudo -u postgres psql -d helpdesk -f migrations/003_location_db_sync.sql

ALTER TABLE helpdesk.connectors
    ADD COLUMN IF NOT EXISTS webhook_token_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_connectors_webhook_token_hash
    ON helpdesk.connectors(webhook_token_hash)
    WHERE webhook_token_hash IS NOT NULL;
