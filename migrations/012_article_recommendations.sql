-- 012_article_recommendations.sql
-- Track which KB articles are recommended per conversation turn.
-- Foundation for the feedback loop: identify high-performing vs dead-weight articles.

CREATE TABLE IF NOT EXISTS helpdesk.article_recommendations (
    id              SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES helpdesk.ai_conversations(id),
    document_id     INTEGER NOT NULL REFERENCES helpdesk.documents(id),
    tenant_id       INTEGER NOT NULL REFERENCES helpdesk.tenants(id),
    turn_number     INTEGER NOT NULL DEFAULT 1,
    layer           INTEGER NOT NULL DEFAULT 1,     -- 1 = Haiku L1, 2 = Sonnet L2
    resolved        BOOLEAN,                         -- NULL = unknown, true = issue resolved after this rec, false = didn't help
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Query patterns: "which articles for this conversation", "article hit rate", "unused articles"
CREATE INDEX IF NOT EXISTS idx_article_recs_conversation
    ON helpdesk.article_recommendations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_article_recs_document
    ON helpdesk.article_recommendations(document_id);
CREATE INDEX IF NOT EXISTS idx_article_recs_tenant
    ON helpdesk.article_recommendations(tenant_id, created_at DESC);

-- Add l2_analysis column to ai_conversations for L2→L1 handback
-- Stores L2's full response so subsequent L1 turns can reference it
ALTER TABLE helpdesk.ai_conversations
    ADD COLUMN IF NOT EXISTS l2_analysis TEXT;
