-- 071: Custom Forms System
--
-- Adds:
--   1. "custom" ticket type: workflow, permission, notification events
--   2. form_templates table: service catalog items (templates for ticket creation)
--   3. form_template_id FK on tickets
--   4. Bug built-in fields: steps_to_reproduce, expected_behavior, actual_behavior on tickets
--   5. Nested/conditional custom fields: parent_field_id + show_when + nesting_depth on custom_field_definitions
--   6. "done" status flagging for tenant-defined custom workflows

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. Custom ticket type: workflow defaults
-- ============================================================

-- System default workflow for "custom" type — same shape as support
INSERT INTO ticket_status_workflows (tenant_id, ticket_type, statuses) VALUES
(NULL, 'custom', '[
    {"key":"open","label":"Open","category":"active"},
    {"key":"in_progress","label":"In Progress","category":"active"},
    {"key":"pending","label":"Pending","category":"active"},
    {"key":"resolved","label":"Resolved","category":"done"},
    {"key":"closed_not_resolved","label":"Closed (Not Resolved)","category":"done"}
]'::jsonb)
ON CONFLICT DO NOTHING;

-- ============================================================
-- 2. Custom ticket type: permission
-- ============================================================

INSERT INTO permissions (slug, label, category, description) VALUES
    ('tickets.create.custom', 'Create Custom Tickets', 'Tickets', 'Can create Custom form tickets')
ON CONFLICT (slug) DO NOTHING;

-- Grant to all staff groups (same pattern as other types)
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name IN ('Admins', 'Managers', 'Senior Agents', 'Agents')
  AND p.slug = 'tickets.create.custom'
ON CONFLICT DO NOTHING;

-- End-users also get custom (they need it for service catalog)
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name IN ('End Users', 'end_user')
  AND p.slug = 'tickets.create.custom'
ON CONFLICT DO NOTHING;

-- ============================================================
-- 3. Custom ticket type: notification event
-- ============================================================

INSERT INTO notification_preferences (tenant_id, event, channel, role_target, enabled)
SELECT t.id, 'custom_created', 'email', 'assignee', true
FROM tenants t
ON CONFLICT (tenant_id, event, channel, role_target) DO NOTHING;

-- ============================================================
-- 4. Expand status CHECK constraint (add custom default statuses)
--    "open", "in_progress", "pending", "resolved" already allowed.
--    No change needed — custom type reuses existing status keys.
-- ============================================================

-- ============================================================
-- 5. Form Templates table (service catalog items)
-- ============================================================

CREATE TABLE IF NOT EXISTS form_templates (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT,
    icon                TEXT,                           -- icon identifier for catalog display
    catalog_category    TEXT,                           -- grouping label in service catalog (e.g. "Corporate", "OLO")
    ticket_type         TEXT NOT NULL DEFAULT 'custom', -- which ticket type this template creates
    field_ids           INTEGER[] DEFAULT '{}',         -- ordered refs to custom_field_definitions
    default_category_id INTEGER REFERENCES problem_categories(id) ON DELETE SET NULL,
    default_priority    TEXT CHECK (default_priority IN ('p1', 'p2', 'p3', 'p4')),
    is_active           BOOLEAN DEFAULT true,
    is_customer_facing  BOOLEAN DEFAULT true,
    sort_order          INTEGER DEFAULT 0,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_form_templates_tenant ON form_templates(tenant_id);
CREATE INDEX IF NOT EXISTS idx_form_templates_tenant_active ON form_templates(tenant_id, is_active)
    WHERE is_active = true;

-- ============================================================
-- 6. form_template_id FK on tickets
-- ============================================================

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS form_template_id INTEGER
    REFERENCES form_templates(id) ON DELETE SET NULL;

-- ============================================================
-- 7. Bug built-in fields on tickets
-- ============================================================

ALTER TABLE tickets ADD COLUMN IF NOT EXISTS steps_to_reproduce TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS expected_behavior TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS actual_behavior TEXT;

-- ============================================================
-- 8. Nested / conditional custom fields
--    parent_field_id: which field is this nested under
--    show_when: JSONB condition for visibility, e.g.
--      {"value": "Option A"} — show when parent equals "Option A"
--      {"values": ["A","B"]} — show when parent equals any of these
--    nesting_depth: enforced max 5 per chain
-- ============================================================

ALTER TABLE custom_field_definitions
    ADD COLUMN IF NOT EXISTS parent_field_id INTEGER
        REFERENCES custom_field_definitions(id) ON DELETE SET NULL;

ALTER TABLE custom_field_definitions
    ADD COLUMN IF NOT EXISTS show_when JSONB;

ALTER TABLE custom_field_definitions
    ADD COLUMN IF NOT EXISTS nesting_depth INTEGER DEFAULT 0
        CHECK (nesting_depth >= 0 AND nesting_depth <= 5);

CREATE INDEX IF NOT EXISTS idx_custom_field_definitions_parent
    ON custom_field_definitions(parent_field_id)
    WHERE parent_field_id IS NOT NULL;

-- ============================================================
-- 9. Validate nesting depth function
--    Prevents chains deeper than 5 levels
-- ============================================================

CREATE OR REPLACE FUNCTION check_nesting_depth()
RETURNS TRIGGER AS $$
DECLARE
    depth INTEGER := 0;
    current_parent INTEGER;
BEGIN
    IF NEW.parent_field_id IS NULL THEN
        NEW.nesting_depth := 0;
        RETURN NEW;
    END IF;

    current_parent := NEW.parent_field_id;
    WHILE current_parent IS NOT NULL AND depth < 6 LOOP
        depth := depth + 1;
        SELECT parent_field_id INTO current_parent
        FROM custom_field_definitions
        WHERE id = current_parent;
    END LOOP;

    IF depth > 5 THEN
        RAISE EXCEPTION 'Nesting depth exceeds maximum of 5 levels';
    END IF;

    NEW.nesting_depth := depth;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_nesting_depth ON custom_field_definitions;
CREATE TRIGGER trg_check_nesting_depth
    BEFORE INSERT OR UPDATE ON custom_field_definitions
    FOR EACH ROW
    EXECUTE FUNCTION check_nesting_depth();
