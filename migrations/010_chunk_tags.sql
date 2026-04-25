-- 010: Add tags to document_chunks, clean stale pipeline_runs, backfill chunk tags
-- See docs/knowledge-pipeline-architecture.md for full rationale.

BEGIN;
SET search_path = helpdesk, public;

-- 1. Add tags column to document_chunks (native TEXT[] for GIN-indexed array queries)
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_document_chunks_tags ON document_chunks USING GIN (tags);

-- 2. Clean stale pipeline_runs stuck in "running" (likely crashed threads)
UPDATE pipeline_runs
SET status = 'failed',
    error_message = 'Stale run cleaned by migration 010',
    completed_at = now()
WHERE status = 'running'
  AND started_at < now() - INTERVAL '1 hour';

-- 3. Backfill: propagate existing document tags to their chunks
UPDATE document_chunks dc
SET tags = d.tags
FROM documents d
WHERE dc.document_id = d.id
  AND d.tags IS NOT NULL
  AND array_length(d.tags, 1) > 0
  AND (dc.tags IS NULL OR dc.tags = '{}');

COMMIT;
