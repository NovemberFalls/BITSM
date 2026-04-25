-- Migration 004: Tenant-created KB articles + auth improvements
-- Run: sudo -u postgres psql -d helpdesk -f migrations/004_tenant_articles_and_auth.sql

BEGIN;

-- Tenant-created KB articles
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenants(id) ON DELETE CASCADE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_published BOOLEAN DEFAULT true;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_by INT REFERENCES users(id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id) WHERE tenant_id IS NOT NULL;

-- Make module_id nullable (tenant articles have no module)
ALTER TABLE documents ALTER COLUMN module_id DROP NOT NULL;

-- Auth: case-insensitive email lookups + prevent duplicate NULL tenant users
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_null_tenant ON users(LOWER(email)) WHERE tenant_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_lower ON users(LOWER(email));

-- Auth: domain-based tenant lookup
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS domain VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_tenants_domain ON tenants(LOWER(domain)) WHERE domain IS NOT NULL;

COMMIT;
