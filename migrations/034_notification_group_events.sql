-- Migration 034: Notification group event subscriptions
--
-- Adds the notification_group_events table, which allows a notification
-- group to subscribe to specific events and channels rather than receiving
-- every event that targets any group.
--
-- Prior to this table, notification groups were all-or-nothing: a group
-- was either a member of a delivery list or it wasn't.  This table gives
-- tenant admins fine-grained control — e.g. "send ticket_created via email,
-- but not sla_breach" — without changing any existing group or member rows.
--
-- The table starts empty.  All groups implicitly receive all events until
-- an admin creates rows here (application logic must treat an empty table
-- as "no filtering applied" during the transition period).
--
-- Idempotent: safe to re-run (IF NOT EXISTS guards on all DDL).

-- ============================================================
-- UP MIGRATION
-- ============================================================

SET search_path TO helpdesk, public;

-- ------------------------------------------------------------
-- notification_group_events
-- Rows represent "group G subscribes to event E on channel C".
-- The enabled column lets admins soft-disable a subscription
-- without deleting the row (preserves audit history).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS helpdesk.notification_group_events (
    id          SERIAL PRIMARY KEY,
    group_id    INT  NOT NULL
                    REFERENCES helpdesk.notification_groups(id)
                    ON DELETE CASCADE,
    event       TEXT NOT NULL,              -- e.g. 'ticket_created', 'ticket_resolved', 'sla_breach'
    channel     TEXT NOT NULL DEFAULT 'email',
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (group_id, event, channel)       -- prevent duplicate subscriptions
);

COMMENT ON TABLE helpdesk.notification_group_events IS
    'Per-group event subscriptions.  Each row opts a notification group in to '
    'receiving a specific (event, channel) combination.  enabled=false soft-disables '
    'the subscription without dropping the row.  The table starts empty — no rows '
    'means no per-group filtering is configured (application logic decides default '
    'behaviour).  Admins configure this via the Notifications tab in Admin Panel.';

COMMENT ON COLUMN helpdesk.notification_group_events.event IS
    'Event slug matching notification_preferences.event — e.g. ticket_created, '
    'ticket_resolved, ticket_assigned, sla_warning, sla_breach, agent_reply, requester_reply.';

COMMENT ON COLUMN helpdesk.notification_group_events.channel IS
    'Delivery channel — mirrors notification_preferences.channel values: '
    '''email'', ''teams_webhook'', ''in_app''.  Defaults to ''email''.';

COMMENT ON COLUMN helpdesk.notification_group_events.enabled IS
    'Soft toggle.  false = subscription exists but is paused.  '
    'Allows admins to temporarily disable without losing configuration.';

-- ------------------------------------------------------------
-- Index on group_id for join performance.
-- Queries will always filter/join by group_id; the UNIQUE
-- constraint creates an index on (group_id, event, channel)
-- but a single-column index on group_id alone is still useful
-- for DELETE CASCADE lookups and membership queries that do
-- not specify event or channel.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_notif_group_events_group_id
    ON helpdesk.notification_group_events (group_id);


-- ============================================================
-- DOWN MIGRATION  (reverse the up migration, cleanly)
-- ============================================================

-- To roll back, run the following block manually or via a
-- migration runner that supports down migrations:
--
-- BEGIN;
--
-- DROP INDEX IF EXISTS helpdesk.idx_notif_group_events_group_id;
-- DROP TABLE IF EXISTS helpdesk.notification_group_events;
--
-- COMMIT;
--
-- No other tables are touched by this migration, so there is
-- nothing else to undo.
