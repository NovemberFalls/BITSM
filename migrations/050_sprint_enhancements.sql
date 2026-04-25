-- 050: Sprint enhancements — tenant-configurable work item types, completion tracking
--
-- Adds a configurable work item hierarchy so tenants can define their own
-- item types (Epic, Story, Task, Bug, Sub-task) with icons and colors,
-- replacing the hard-coded ticket_type enum for dev workflows:
--   - work_item_types: tenant-scoped type definitions with icon, color, sort order
--   - System defaults (tenant_id = NULL) seeded for Epic, Story, Task, Bug, Sub-task
--   - tickets.work_item_type_id: FK to the new table
--   - tickets.completed_at / completed_by: explicit completion tracking for timeline queries

-- ── Work item types ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS work_item_types (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT,
    icon        TEXT,           -- emoji or icon name
    color       TEXT,           -- hex color for badges
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_default  BOOLEAN NOT NULL DEFAULT false,
    created_by  INTEGER REFERENCES users(id),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE(tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_work_item_types_tenant ON work_item_types(tenant_id);

-- System defaults (tenant_id = NULL) — used when tenant has no custom types
INSERT INTO work_item_types (tenant_id, name, slug, description, icon, color, sort_order, is_default) VALUES
(NULL, 'Epic',     'epic',     'Large body of work that spans multiple sprints',        '🏔️', '#8B5CF6', 0, true),
(NULL, 'Story',    'story',    'User-facing feature or requirement',                    '📖', '#3B82F6', 1, true),
(NULL, 'Task',     'task',     'Unit of work to be completed',                          '✅', '#10B981', 2, true),
(NULL, 'Bug',      'bug',      'Defect or unexpected behavior to be fixed',             '🐛', '#EF4444', 3, true),
(NULL, 'Sub-task', 'sub-task', 'Smaller piece of work within a parent item',            '📌', '#6B7280', 4, true)
ON CONFLICT DO NOTHING;

-- ── Ticket completion + work item type columns ──────────────────────────────
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS work_item_type_id INTEGER REFERENCES work_item_types(id) ON DELETE SET NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS completed_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_tickets_work_item_type ON tickets(work_item_type_id);
CREATE INDEX IF NOT EXISTS idx_tickets_completed_at ON tickets(completed_at);
