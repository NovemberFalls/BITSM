-- Migration 044: RBAC Audit — Seed granular permission slugs for Phase A overhaul
--
-- Adds 6 new permissions that split out capabilities previously implied by
-- broader parent permissions (users.manage, categories.manage, tickets.create).
-- Existing groups that hold the parent permission are auto-granted the children.
-- create_default_groups() is updated so new tenants get correct defaults.
--
-- Idempotent: safe to run multiple times (ON CONFLICT DO NOTHING everywhere).

BEGIN;

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. Seed 6 new permission slugs
-- ============================================================
INSERT INTO permissions (slug, label, category, description) VALUES
    ('teams.manage',         'Manage Teams',              'Admin',         'Create, edit, delete teams and manage team membership'),
    ('kb.manage',            'Manage Knowledge Base',     'Configuration', 'Create, edit, delete KB articles, manage collections, upload files'),
    ('phone.manage',         'Manage Phone Settings',     'Configuration', 'Configure phone service AI settings and provisioning'),
    ('sprints.manage',       'Manage Sprints',            'Tickets',       'Create and manage development sprints'),
    ('connectors.manage',    'Manage Connectors',         'Configuration', 'Create, edit, delete external system integrations'),
    ('notifications.manage', 'Manage Notifications',      'Configuration', 'Manage notification groups, templates, and delivery rules')
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- 2. Auto-grant new permissions to existing groups
--    based on parent permission ownership
-- ============================================================

-- Groups with users.manage → also get teams.manage, phone.manage,
-- connectors.manage, notifications.manage
INSERT INTO group_permissions (group_id, permission_id)
SELECT gp.group_id, p.id
FROM group_permissions gp
JOIN permissions parent ON parent.id = gp.permission_id AND parent.slug = 'users.manage'
CROSS JOIN permissions p
WHERE p.slug IN ('teams.manage', 'phone.manage', 'connectors.manage', 'notifications.manage')
ON CONFLICT DO NOTHING;

-- Groups with categories.manage → also get kb.manage
INSERT INTO group_permissions (group_id, permission_id)
SELECT gp.group_id, p.id
FROM group_permissions gp
JOIN permissions parent ON parent.id = gp.permission_id AND parent.slug = 'categories.manage'
CROSS JOIN permissions p
WHERE p.slug = 'kb.manage'
ON CONFLICT DO NOTHING;

-- Groups with tickets.create → also get sprints.manage
INSERT INTO group_permissions (group_id, permission_id)
SELECT gp.group_id, p.id
FROM group_permissions gp
JOIN permissions parent ON parent.id = gp.permission_id AND parent.slug = 'tickets.create'
CROSS JOIN permissions p
WHERE p.slug = 'sprints.manage'
ON CONFLICT DO NOTHING;

-- ============================================================
-- 3. Update create_default_groups() for new tenants
--    Same signature, CREATE OR REPLACE. Adds new permissions
--    to Senior Agents and Managers; Admins already get ALL.
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

    -- Agents: tickets.view, tickets.create, atlas.chat (unchanged)
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_agents_id, id FROM helpdesk.permissions WHERE slug IN ('tickets.view', 'tickets.create', 'atlas.chat')
    ON CONFLICT DO NOTHING;

    -- Senior Agents: Agents + tickets.close, tickets.assign, audit.view, sprints.manage
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_senior_id, id FROM helpdesk.permissions WHERE slug IN (
        'tickets.view', 'tickets.create', 'atlas.chat',
        'tickets.close', 'tickets.assign', 'audit.view',
        'sprints.manage'
    ) ON CONFLICT DO NOTHING;

    -- Managers: Senior Agents + metrics, audit.review, audit.kba, categories, locations,
    --           reports.view, automations.manage, teams.manage, kb.manage, sprints.manage, notifications.manage
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_managers_id, id FROM helpdesk.permissions WHERE slug IN (
        'tickets.view', 'tickets.create', 'atlas.chat',
        'tickets.close', 'tickets.assign', 'audit.view',
        'metrics.view', 'audit.review', 'audit.kba',
        'categories.manage', 'locations.manage',
        'reports.view', 'automations.manage',
        'teams.manage', 'kb.manage', 'sprints.manage', 'notifications.manage'
    ) ON CONFLICT DO NOTHING;

    -- Admins: all permissions
    INSERT INTO helpdesk.group_permissions (group_id, permission_id)
    SELECT v_admins_id, id FROM helpdesk.permissions
    ON CONFLICT DO NOTHING;
END;
$$ LANGUAGE plpgsql;

COMMIT;
