-- 008_chat_refactor.sql
-- Add updated_at, status, turn_count to ai_conversations for
-- inactivity timeout, archival, and turn-aware system prompts.

-- Track last activity for timeout
ALTER TABLE helpdesk.ai_conversations
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- Active vs archived status
ALTER TABLE helpdesk.ai_conversations
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'
        CHECK (status IN ('active', 'archived'));

-- Server-side turn counter
ALTER TABLE helpdesk.ai_conversations
    ADD COLUMN IF NOT EXISTS turn_count INT DEFAULT 0;

-- Backfill updated_at from created_at
UPDATE helpdesk.ai_conversations
    SET updated_at = created_at
    WHERE updated_at IS NULL;

-- Index for timeout queries (find stale active conversations)
CREATE INDEX IF NOT EXISTS idx_ai_conversations_status_updated
    ON helpdesk.ai_conversations(status, updated_at);
