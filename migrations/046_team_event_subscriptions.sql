-- Migration 046: Team event subscriptions
--
-- Adds team_event_subscriptions table so that teams (not just notification
-- groups) can subscribe to email notification events. Team members receive
-- the email when a subscribed event fires.
--
-- Mirrors the notification_group_events pattern from migration 034.
-- Default behaviour: all teams subscribed to all events (enabled=true when
-- no row exists).
--
-- Idempotent: safe to re-run (IF NOT EXISTS guards on all DDL).

SET search_path TO helpdesk, public;

CREATE TABLE IF NOT EXISTS helpdesk.team_event_subscriptions (
    id          SERIAL PRIMARY KEY,
    team_id     INT  NOT NULL
                    REFERENCES helpdesk.teams(id)
                    ON DELETE CASCADE,
    event       TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'email',
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (team_id, event, channel)
);

CREATE INDEX IF NOT EXISTS idx_team_event_subs_team
    ON helpdesk.team_event_subscriptions (team_id);

COMMENT ON TABLE helpdesk.team_event_subscriptions IS
    'Per-team event subscriptions. Each row opts a team in to receiving a '
    'specific (event, channel) combination. All team members get the email. '
    'Default when no row exists = subscribed (enabled=true).';
