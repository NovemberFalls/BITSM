-- ============================================================
-- Migration 002: Ticket System Overhaul
-- - 4 statuses (open, pending, resolved, closed_not_resolved)
-- - P-number priorities (p1, p2, p3, p4)
-- - Nestable location hierarchy
-- - Configurable problem categories (tenant-named, unlimited nesting)
-- - LLM auto-tag suggestions
-- ============================================================

-- ============================================================
-- NEW TABLE: LOCATIONS (self-referencing tree — any depth)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.locations (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    parent_id   INT REFERENCES helpdesk.locations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    level_label TEXT,          -- e.g. 'Company', 'Country', 'State', 'City', 'Location'
    sort_order  INT DEFAULT 0,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_locations_tenant ON helpdesk.locations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON helpdesk.locations(parent_id);

-- ============================================================
-- NEW TABLE: PROBLEM CATEGORIES (self-referencing tree)
-- Tenant-configurable label via tenants.settings->>'problem_field_label'
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.problem_categories (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    parent_id   INT REFERENCES helpdesk.problem_categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INT DEFAULT 0,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_problem_categories_tenant ON helpdesk.problem_categories(tenant_id);
CREATE INDEX IF NOT EXISTS idx_problem_categories_parent ON helpdesk.problem_categories(parent_id);

-- ============================================================
-- NEW TABLE: TAG SUGGESTIONS (LLM-generated, agent-approvable)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.tag_suggestions (
    id          SERIAL PRIMARY KEY,
    ticket_id   INT NOT NULL REFERENCES helpdesk.tickets(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    confidence  REAL,
    accepted    BOOLEAN,       -- NULL = pending, true = accepted, false = rejected
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tag_suggestions_ticket ON helpdesk.tag_suggestions(ticket_id);

-- ============================================================
-- ALTER TICKETS: Add new columns
-- ============================================================
ALTER TABLE helpdesk.tickets ADD COLUMN IF NOT EXISTS location_id INT REFERENCES helpdesk.locations(id);
ALTER TABLE helpdesk.tickets ADD COLUMN IF NOT EXISTS problem_category_id INT REFERENCES helpdesk.problem_categories(id);

-- ============================================================
-- DATA MIGRATION: Statuses (7 → 4)
--   in_progress  → open
--   escalated    → open
--   waiting_on_customer → pending
--   closed → closed_not_resolved
--   (open, pending, resolved remain as-is)
-- ============================================================
UPDATE helpdesk.tickets SET status = 'open' WHERE status IN ('in_progress', 'escalated');
UPDATE helpdesk.tickets SET status = 'pending' WHERE status = 'waiting_on_customer';
UPDATE helpdesk.tickets SET status = 'closed_not_resolved' WHERE status = 'closed';

-- ============================================================
-- DATA MIGRATION: Priorities (word → P-number)
-- ============================================================
UPDATE helpdesk.tickets SET priority = 'p1' WHERE priority = 'urgent';
UPDATE helpdesk.tickets SET priority = 'p2' WHERE priority = 'high';
UPDATE helpdesk.tickets SET priority = 'p3' WHERE priority = 'medium';
UPDATE helpdesk.tickets SET priority = 'p4' WHERE priority = 'low';

-- ============================================================
-- REPLACE CHECK CONSTRAINTS
-- ============================================================

-- Drop old status constraint (name may vary)
DO $$
BEGIN
    ALTER TABLE helpdesk.tickets DROP CONSTRAINT IF EXISTS tickets_status_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

ALTER TABLE helpdesk.tickets ADD CONSTRAINT tickets_status_check
    CHECK (status IN ('open', 'pending', 'resolved', 'closed_not_resolved'));

-- Drop old priority constraint
DO $$
BEGIN
    ALTER TABLE helpdesk.tickets DROP CONSTRAINT IF EXISTS tickets_priority_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

ALTER TABLE helpdesk.tickets ADD CONSTRAINT tickets_priority_check
    CHECK (priority IN ('p1', 'p2', 'p3', 'p4'));

-- ============================================================
-- UPDATE DEFAULTS
-- ============================================================
ALTER TABLE helpdesk.tickets ALTER COLUMN status SET DEFAULT 'open';
ALTER TABLE helpdesk.tickets ALTER COLUMN priority SET DEFAULT 'p3';

-- ============================================================
-- REMOVE escalated_to column (escalation is now an internal note)
-- ============================================================
ALTER TABLE helpdesk.tickets DROP COLUMN IF EXISTS escalated_to;

-- ============================================================
-- MIGRATE SLA POLICIES priority references
-- ============================================================
UPDATE helpdesk.sla_policies SET priority = 'p1' WHERE priority = 'urgent';
UPDATE helpdesk.sla_policies SET priority = 'p2' WHERE priority = 'high';
UPDATE helpdesk.sla_policies SET priority = 'p3' WHERE priority = 'medium';
UPDATE helpdesk.sla_policies SET priority = 'p4' WHERE priority = 'low';

-- ============================================================
-- INDEX for new ticket columns
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_tickets_location ON helpdesk.tickets(location_id);
CREATE INDEX IF NOT EXISTS idx_tickets_problem_category ON helpdesk.tickets(problem_category_id);
