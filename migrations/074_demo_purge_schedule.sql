-- 074_demo_purge_schedule.sql
-- Register the demo_purge cron job in pipeline_schedules.
--
-- Runs daily at 03:00 UTC.  Hard-deletes all data for demo tenants whose
-- plan_expires_at has passed (privacy + cost measure for BYOK demo mode).
-- The step is handled by _cron_demo_purge() in services/queue_service.py.

SET search_path TO helpdesk, public;

INSERT INTO pipeline_schedules (step_name, cron_expression, enabled, payload)
VALUES ('demo_purge', '0 3 * * *', true, '{}')
ON CONFLICT (step_name) DO NOTHING;
