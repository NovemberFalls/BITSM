-- Migration 022: Pipeline phases — sequenced execution lanes
-- Adds phase column to pipeline_queue for ordered step execution.
-- Phase 0 (immediate): notify, auto_tag, enrich
-- Phase 1 (after enrich): engage
-- Phase 2 (after engage): route

SET search_path TO helpdesk, public;

ALTER TABLE pipeline_queue ADD COLUMN IF NOT EXISTS phase INT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_pq_phase ON pipeline_queue (phase);
