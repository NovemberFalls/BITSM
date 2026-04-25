-- Migration 032: Visual Automation Builder
-- Adds automations, automation_nodes, automation_edges, automation_runs tables
-- Plus automations.manage permission

BEGIN;

SET search_path TO helpdesk, public;

-- ── Automations (workflow definitions) ───────────────────────────────

CREATE TABLE IF NOT EXISTS automations (
    id             SERIAL PRIMARY KEY,
    tenant_id      INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    description    TEXT DEFAULT '',
    trigger_type   TEXT NOT NULL,
    trigger_config JSONB DEFAULT '{}',
    is_active      BOOLEAN DEFAULT false,
    created_by     INT REFERENCES users(id) ON DELETE SET NULL,
    updated_by     INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ DEFAULT now(),
    updated_at     TIMESTAMPTZ DEFAULT now(),
    run_count      INT DEFAULT 0,
    last_run_at    TIMESTAMPTZ
);

CREATE INDEX idx_automations_tenant ON automations(tenant_id) WHERE is_active = true;
CREATE INDEX idx_automations_trigger ON automations(tenant_id, trigger_type) WHERE is_active = true;

-- ── Automation Nodes (canvas nodes) ──────────────────────────────────

CREATE TABLE IF NOT EXISTS automation_nodes (
    id             TEXT NOT NULL,
    automation_id  INT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    node_type      TEXT NOT NULL,
    node_subtype   TEXT NOT NULL,
    position_x     FLOAT DEFAULT 0,
    position_y     FLOAT DEFAULT 0,
    config         JSONB DEFAULT '{}',
    label          TEXT DEFAULT '',
    PRIMARY KEY (automation_id, id)
);

-- ── Automation Edges (connections) ───────────────────────────────────

CREATE TABLE IF NOT EXISTS automation_edges (
    id             TEXT NOT NULL,
    automation_id  INT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    source_node    TEXT NOT NULL,
    target_node    TEXT NOT NULL,
    source_handle  TEXT DEFAULT 'default',
    PRIMARY KEY (automation_id, id)
);

-- ── Automation Runs (execution log) ──────────────────────────────────

CREATE TABLE IF NOT EXISTS automation_runs (
    id              SERIAL PRIMARY KEY,
    automation_id   INT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    ticket_id       INT REFERENCES tickets(id) ON DELETE SET NULL,
    tenant_id       INT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    trigger_type    TEXT NOT NULL,
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    duration_ms     INT,
    nodes_executed  INT DEFAULT 0,
    actions_taken   JSONB DEFAULT '[]',
    error           TEXT,
    ticket_snapshot JSONB DEFAULT '{}'
);

CREATE INDEX idx_runs_automation ON automation_runs(automation_id, started_at DESC);
CREATE INDEX idx_runs_tenant ON automation_runs(tenant_id, started_at DESC);

-- ── Permission ───────────────────────────────────────────────────────

INSERT INTO permissions (slug, label, category, description)
VALUES ('automations.manage', 'Manage Automations', 'automations',
        'Create, edit, and manage workflow automations')
ON CONFLICT (slug) DO NOTHING;

-- Assign to default admin/manager groups (same pattern as 031)
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE p.slug = 'automations.manage'
  AND g.name IN ('Administrators', 'Managers')
  AND NOT EXISTS (
      SELECT 1 FROM group_permissions gp
      WHERE gp.group_id = g.id AND gp.permission_id = p.id
  );

COMMIT;
