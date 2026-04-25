-- 051: Expand tickets_status_check to include dev ticket statuses
--
-- The original CHECK constraint only allowed support statuses (open, pending,
-- resolved, closed_not_resolved). Dev tickets (task, bug, feature) use a
-- different workflow with statuses: backlog, todo, in_progress, in_review,
-- testing, done, cancelled.
--
-- NOTE: DROP + ADD CONSTRAINT requires table ownership (helpdesk_app owns tickets).

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS tickets_status_check;
ALTER TABLE tickets ADD CONSTRAINT tickets_status_check CHECK (
    status IN (
        'open', 'pending', 'resolved', 'closed_not_resolved',
        'backlog', 'todo', 'in_progress', 'in_review', 'testing', 'done', 'cancelled'
    )
);
