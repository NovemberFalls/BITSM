-- Migration 007: AI Chat module, conversation feedback, indexes
-- Run: sudo -u postgres psql -d helpdesk -f migrations/007_ai_chat_module.sql

BEGIN;

-- ============================================================
-- AI CHAT as an enableable module
-- ============================================================
INSERT INTO helpdesk.knowledge_modules (slug, name, description, icon)
VALUES ('ai_chat', 'AI Chat', 'RAG-powered AI assistant with knowledge base search', 'message-circle')
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- Per-message feedback on conversations
-- ============================================================
-- feedback stores: [{message_index, rating, comment, created_at}]
ALTER TABLE helpdesk.ai_conversations
  ADD COLUMN IF NOT EXISTS feedback JSONB DEFAULT '[]';

-- ============================================================
-- Performance indexes for conversation queries
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_ai_conversations_user
  ON helpdesk.ai_conversations(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_tenant
  ON helpdesk.ai_conversations(tenant_id);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_ticket
  ON helpdesk.ai_conversations(ticket_id)
  WHERE ticket_id IS NOT NULL;

COMMIT;
