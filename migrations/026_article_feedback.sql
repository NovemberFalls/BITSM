-- Migration 026: Article-level user feedback after AI resolution
-- Adds user_helpful to article_recommendations so end-users can rate
-- which KB article actually solved their issue.

SET search_path TO helpdesk, public;

ALTER TABLE article_recommendations
    ADD COLUMN IF NOT EXISTS user_helpful BOOLEAN DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS rated_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;

-- Index for aggregation queries (which articles are most helpful)
CREATE INDEX IF NOT EXISTS idx_article_recs_helpful
    ON article_recommendations (document_id, user_helpful)
    WHERE user_helpful IS NOT NULL;
