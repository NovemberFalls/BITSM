-- 039: Tenant collections — tenant-scoped article groupings (like mini-modules)
-- e.g. an "Acme Corp Policies" collection holds all of that tenant's uploaded policy docs

CREATE TABLE IF NOT EXISTS tenant_collections (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    doc_count   INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_collections_tenant ON tenant_collections(tenant_id);

-- Unique name per tenant
ALTER TABLE tenant_collections ADD CONSTRAINT uq_tenant_collection_name UNIQUE (tenant_id, name);

-- Link documents to a collection (NULL = uncollected / hand-typed article)
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_collection_id INTEGER REFERENCES tenant_collections(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(tenant_collection_id);
