-- SOC 2 CC6.1: API key expiry + rotation tracking
SET search_path TO helpdesk;

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_by INT REFERENCES users(id);

-- Expire existing keys 90 days from creation (retroactive policy)
UPDATE api_keys SET expires_at = created_at + interval '90 days' WHERE expires_at IS NULL;
