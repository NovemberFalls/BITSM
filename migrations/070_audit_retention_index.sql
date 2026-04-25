-- SOC 2 CC7.2: Audit log retention support
-- Retention policy: 2 years (730 days). Cleanup via scheduled job.
SET search_path TO helpdesk;

-- Efficient range deletion by date (already have idx on created_at DESC,
-- but adding explicit comment for audit trail)
COMMENT ON TABLE audit_events IS 'SOC 2 audit trail. Retention: 2 years. Insert-only (no UPDATE/DELETE by app code).';
