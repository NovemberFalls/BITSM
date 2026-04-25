-- 030: Article effectiveness scoring from user feedback
-- Adds effectiveness_score column to documents for feedback-boosted RAG retrieval

ALTER TABLE documents ADD COLUMN IF NOT EXISTS effectiveness_score FLOAT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS rating_count INT DEFAULT 0;

-- Index for fast lookup during RAG retrieval
CREATE INDEX IF NOT EXISTS idx_documents_effectiveness
    ON documents (effectiveness_score)
    WHERE effectiveness_score IS NOT NULL;

-- Materialized view for quick aggregate stats (refreshed on feedback submission)
-- We use a simple column update approach instead for real-time accuracy
