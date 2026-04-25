-- Migration 016: RBAC — Groups + Permissions + Overrides
-- Replaces flat 4-role system with granular permission model.
-- Resolution: user_permission_overrides > group_permissions > role defaults
-- super_admin bypasses all checks. end_user gets fixed set.

BEGIN;

-- ============================================================
-- 1. System-defined permission slugs (not tenant-scoped)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.permissions (
    id          SERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 2. Tenant-scoped groups
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.groups (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    is_default  BOOLEAN DEFAULT false,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_groups_tenant ON helpdesk.groups(tenant_id);

-- ============================================================
-- 3. Group ↔ Permission join
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.group_permissions (
    group_id      INT NOT NULL REFERENCES helpdesk.groups(id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES helpdesk.permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, permission_id)
);

-- ============================================================
-- 4. User ↔ Group membership (many-to-many)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.user_group_memberships (
    user_id   INT NOT NULL REFERENCES helpdesk.users(id) ON DELETE CASCADE,
    group_id  INT NOT NULL REFERENCES helpdesk.groups(id) ON DELETE CASCADE,
    added_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_ugm_user  ON helpdesk.user_group_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_ugm_group ON helpdesk.user_group_memberships(group_id);

-- ============================================================
-- 5. Per-user permission overrides (grant or deny)
-- ============================================================
CREATE TABLE IF NOT EXISTS helpdesk.user_permission_overrides (
    user_id       INT NOT NULL REFERENCES helpdesk.users(id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES helpdesk.permissions(id) ON DELETE CASCADE,
    granted       BOOLEAN NOT NULL,
    reason        TEXT,
    set_by        INT REFERENCES helpdesk.users(id),
    set_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_upo_user ON helpdesk.user_permission_overrides(user_id);

-- ============================================================
-- 6. Seed 14 permission slugs
-- ============================================================
INSERT INTO helpdesk.permissions (slug, label, category, description) VALUES
    ('tickets.view',       'View Tickets',                       'Tickets',       'Can view ticket list and details'),
    ('tickets.create',     'Create Tickets',                     'Tickets',       'Can create new tickets'),
    ('tickets.close',      'Close / Resolve Tickets',            'Tickets',       'Can resolve or close tickets'),
    ('tickets.assign',     'Assign Tickets',                     'Tickets',       'Can assign tickets to agents'),
    ('categories.manage',  'Manage Problem Categories',          'Configuration', 'Can create/edit/delete problem categories'),
    ('locations.manage',   'Manage Location Hierarchy',          'Configuration', 'Can create/edit/delete locations'),
    ('audit.view',         'View AI Audit Queue',                'AI',            'Can view the audit queue and knowledge gaps'),
    ('audit.review',       'Approve / Reject Audit Items',       'AI',            'Can approve or dismiss audit queue items'),
    ('audit.kba',          'Create KBAs from Audit Queue',       'AI',            'Can create knowledge base articles from audit candidates'),
    ('metrics.view',       'View SLA Risk & Performance Metrics','Analytics',     'SLA risk predictions, routing metrics, agent performance (manager-only)'),
    ('atlas.chat',         'Use Atlas Chat',                     'AI',            'Can use Atlas in-ticket chat and sidebar chat'),
    ('atlas.admin',        'Manage Atlas Settings',              'AI',            'Can manage Atlas AI configuration and sub-features'),
    ('users.invite',       'Invite Users',                       'Admin',         'Can send user invitations to the tenant'),
    ('users.manage',       'Manage Users & Groups',              'Admin',         'Can manage user roles, groups, and permission overrides')
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- 7. Helper function: create default groups for a tenant
--    Called manually or from admin when onboarding a tenant.
-- ============================================================
CREATE OR REPLACE FUNCTION helpdesk.create_default_groups(p_tenant_id INT)
RETURNS void AS $$
DECLARE
    v_agents_id   INT;
    v_senior_id   INT;
    v_managers_id INT;
    v_admins_id   INT;
BEGIN
    -- Create 4 default groups
    INSERT INTO helpdesk.groups (tenant_id, name, description, is_default)
    VALUES (p_tenant_id, 'Agents', 'Frontline support agents', true)
    ON CONFLICT (tenant_id, name) DO NOTHING
    RETURNING id INTO v_agents_id;

    INSERT INTO helpdesk.groups (tenant_id, name, description, is_default)
    VALUES (p_tenant_id, 'Senior Agents', 'Experienced agents with close/assign privileges', false)
    ON CONFLICT (tenant_id, name) DO NOTHING
    RETURNING id INTO v_senior_id;

    INSERT INTO helpdesk.groups (tenant_id, name, description, is_default)
    VALUES (p_tenant_id, 'Managers', 'Team leads with metrics and audit access', false)
    ON CONFLICT (tenant_id, name) DO NOTHING
    RETURNING id INTO v_managers_id;

    INSERT INTO helpdesk.groups (tenant_id, name, description, is_default)
    VALUES (p_tenant_id, 'Admins', 'Full administrative access', false)
    ON CONFLICT (tenant_id, name) DO NOTHING
    RETURNING id INTO v_admins_id;

    -- If groups already existed, fetch their IDs
    IF v_agents_id IS NULL THEN
        SELECT id INTO v_agents_id FROM helpdesk.groups WHERE tenant_id = p_tenant_id AND name = 'Agents';
    END IF;
    IF v_senior_id IS NULL THEN
        SELECT id INTO v_senior_id FROM helpdesk.groups WHERE tenant_id = p_tenant_id AND name = 'Senior Agents';
    END IF;
    IF v_managers_id IS NULL THEN
        SELECT id INTO v_managers_id FROM helpdesk.groups WHERE tenant_id = p_tenant_id AND name = 'Managers';
    END IF;
    IF v_admins_id IS NULL THEN
        SELECT id INTO v_admins_id FROM helpdesk.groups WHERE tenant_id = p_tenant_id AND name = 'Admins';
    END IF;

    -- Agents: tickets.view, tickets.create, atlas.chat
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_agents_id, id FROM helpdesk.permissions WHERE slug IN ('tickets.view', 'tickets.create', 'atlas.chat')
    ON CONFLICT DO NOTHING;

    -- Senior Agents: Agents + tickets.close, tickets.assign, audit.view
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_senior_id, id FROM helpdesk.permissions WHERE slug IN (
        'tickets.view', 'tickets.create', 'atlas.chat',
        'tickets.close', 'tickets.assign', 'audit.view'
    ) ON CONFLICT DO NOTHING;

    -- Managers: Senior Agents + metrics, audit.review, audit.kba, categories, locations
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_managers_id, id FROM helpdesk.permissions WHERE slug IN (
        'tickets.view', 'tickets.create', 'atlas.chat',
        'tickets.close', 'tickets.assign', 'audit.view',
        'metrics.view', 'audit.review', 'audit.kba',
        'categories.manage', 'locations.manage'
    ) ON CONFLICT DO NOTHING;

    -- Admins: all permissions
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_admins_id, id FROM helpdesk.permissions
    ON CONFLICT DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 8. Create default groups for all existing tenants
-- ============================================================
DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT id FROM helpdesk.tenants LOOP
        PERFORM helpdesk.create_default_groups(t.id);
    END LOOP;
END;
$$;

-- ============================================================
-- 9. Auto-assign existing agents/tenant_admins to appropriate groups
-- ============================================================
-- Agents → "Agents" group
INSERT INTO helpdesk.user_group_memberships (user_id, group_id)
SELECT u.id, g.id
FROM helpdesk.users u
JOIN helpdesk.groups g ON g.tenant_id = u.tenant_id AND g.name = 'Agents'
WHERE u.role = 'agent' AND u.tenant_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Tenant admins → "Admins" group
INSERT INTO helpdesk.user_group_memberships (user_id, group_id)
SELECT u.id, g.id
FROM helpdesk.users u
JOIN helpdesk.groups g ON g.tenant_id = u.tenant_id AND g.name = 'Admins'
WHERE u.role = 'tenant_admin' AND u.tenant_id IS NOT NULL
ON CONFLICT DO NOTHING;

COMMIT;
