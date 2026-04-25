-- Migration 024: Expand tickets source check constraint
-- Adds 'portal' and 'chat_widget' to the allowed source values.
-- These were added live in the previous session to unblock chat-to-case.

SET search_path TO helpdesk, public;

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS tickets_source_check;
ALTER TABLE tickets ADD CONSTRAINT tickets_source_check
  CHECK (source = ANY(ARRAY['web','email','voice','chat','api','teams','portal','chat_widget']));
