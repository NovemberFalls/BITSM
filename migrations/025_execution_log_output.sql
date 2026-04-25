-- Migration 025: Add output_summary to pipeline_execution_log
-- Stores a brief human-readable summary of what each step did (tags found,
-- KB articles matched, agent routed to, etc.) for display in the pipeline UI.

SET search_path TO helpdesk, public;

ALTER TABLE pipeline_execution_log
  ADD COLUMN IF NOT EXISTS output_summary TEXT;
