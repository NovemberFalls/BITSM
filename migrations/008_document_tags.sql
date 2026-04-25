-- 008: Add tags to documents for KB categorization
BEGIN;
SET search_path = helpdesk, public;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING GIN (tags);

COMMIT;
