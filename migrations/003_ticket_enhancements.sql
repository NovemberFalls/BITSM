-- Migration 003: Ticket enhancements — sorting indexes, SLA tracking, notification groups
-- Run: sudo -u postgres psql -d helpdesk -f migrations/003_ticket_enhancements.sql

SET search_path TO helpdesk, public;

-- ============================================================
-- Indexes for sort/filter performance
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_tickets_priority_created
    ON tickets(priority, created_at);

CREATE INDEX IF NOT EXISTS idx_tickets_sla_due
    ON tickets(sla_due_at)
    WHERE status NOT IN ('resolved', 'closed_not_resolved');

CREATE INDEX IF NOT EXISTS idx_tickets_tags
    ON tickets USING GIN(tags);

CREATE INDEX IF NOT EXISTS idx_tickets_tenant_created
    ON tickets(tenant_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tickets_assignee
    ON tickets(assignee_id)
    WHERE assignee_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tickets_requester
    ON tickets(requester_id);

CREATE INDEX IF NOT EXISTS idx_tickets_location
    ON tickets(location_id)
    WHERE location_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tickets_problem_category
    ON tickets(problem_category_id)
    WHERE problem_category_id IS NOT NULL;

-- ============================================================
-- First response tracking
-- ============================================================

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS first_response_at TIMESTAMPTZ;

-- ============================================================
-- Notification groups
-- ============================================================

CREATE TABLE IF NOT EXISTS notification_groups (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification_group_members (
    id          SERIAL PRIMARY KEY,
    group_id    INT NOT NULL REFERENCES notification_groups(id) ON DELETE CASCADE,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_notif_groups_tenant ON notification_groups(tenant_id);
CREATE INDEX IF NOT EXISTS idx_notif_group_members_group ON notification_group_members(group_id);

-- ============================================================
-- Default SLA policies function
-- ============================================================

CREATE OR REPLACE FUNCTION ensure_default_sla_policies(p_tenant_id INT)
RETURNS void AS $$
BEGIN
    -- Only insert if tenant has no policies
    IF NOT EXISTS (SELECT 1 FROM sla_policies WHERE tenant_id = p_tenant_id) THEN
        INSERT INTO sla_policies (tenant_id, name, priority, first_response_minutes, resolution_minutes, business_hours_only)
        VALUES
            (p_tenant_id, 'P1 — Urgent', 'p1', 15, 60, false),
            (p_tenant_id, 'P2 — High', 'p2', 30, 240, false),
            (p_tenant_id, 'P3 — Medium', 'p3', 120, 480, true),
            (p_tenant_id, 'P4 — Low', 'p4', 480, 1440, true);
    END IF;
END;
$$ LANGUAGE plpgsql;
