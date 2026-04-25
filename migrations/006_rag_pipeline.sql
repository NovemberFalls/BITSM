-- Migration 006: RAG pipeline support
-- Adds content_hash for incremental updates, pipeline_runs for audit trail

SET search_path = helpdesk, public;

BEGIN;

-- ============================================================
-- content_hash on document_chunks for dedup/incremental updates
-- ============================================================
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_hash ON document_chunks (document_id, content_hash);

-- ============================================================
-- pipeline_runs: audit trail for KB pipeline executions
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              SERIAL PRIMARY KEY,
    module_id       INTEGER REFERENCES knowledge_modules(id),
    status          TEXT NOT NULL DEFAULT 'running',   -- running, completed, failed
    documents_processed INTEGER DEFAULT 0,
    chunks_created  INTEGER DEFAULT 0,
    chunks_skipped  INTEGER DEFAULT 0,
    chunks_deleted  INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_module ON pipeline_runs (module_id, started_at DESC);

-- ============================================================
-- IVFFlat index for vector similarity search
-- Run AFTER first data load when document_chunks has rows:
--
--   CREATE INDEX idx_chunks_embedding_ivfflat
--   ON document_chunks USING ivfflat (embedding vector_cosine_ops)
--   WITH (lists = 100);
-- ============================================================

COMMIT;
