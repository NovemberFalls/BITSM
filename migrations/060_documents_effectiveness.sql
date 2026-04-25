-- Migration 060: Add effectiveness_score and rating_count to documents
-- Required by audit metrics and RAG feedback scoring

ALTER TABLE helpdesk.documents
    ADD COLUMN IF NOT EXISTS effectiveness_score float,
    ADD COLUMN IF NOT EXISTS rating_count integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_documents_effectiveness
    ON helpdesk.documents (effectiveness_score DESC NULLS LAST)
    WHERE is_published = true;
