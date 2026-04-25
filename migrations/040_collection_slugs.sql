-- 040: Add slug column to tenant_collections for URL-safe identifiers
-- URLs use slugs instead of numeric IDs: ?collection=dev-test not ?collection=1

ALTER TABLE tenant_collections ADD COLUMN IF NOT EXISTS slug TEXT;

-- Backfill existing collections with slugified names
UPDATE tenant_collections
SET slug = lower(regexp_replace(regexp_replace(name, '[^a-zA-Z0-9\s-]', '', 'g'), '\s+', '-', 'g'))
WHERE slug IS NULL;

-- Ensure unique per tenant
ALTER TABLE tenant_collections DROP CONSTRAINT IF EXISTS uq_tenant_collection_slug;
ALTER TABLE tenant_collections ADD CONSTRAINT uq_tenant_collection_slug UNIQUE (tenant_id, slug);
