-- Migration 020: Add auto_approved, auto_dismissed, auto_closed to audit queue status constraint
-- Required for: auto-approve/dismiss thresholds wired into atlas_service.py

SET search_path = helpdesk, public;

-- Drop both possible constraint names (original + auto-generated)
ALTER TABLE ticket_audit_queue DROP CONSTRAINT IF EXISTS audit_queue_status_check;
ALTER TABLE ticket_audit_queue DROP CONSTRAINT IF EXISTS ticket_audit_queue_status_check;

ALTER TABLE ticket_audit_queue ADD CONSTRAINT audit_queue_status_check
  CHECK (status IN ('pending', 'reviewed', 'approved', 'dismissed', 'auto_closed', 'auto_approved', 'auto_dismissed'));
