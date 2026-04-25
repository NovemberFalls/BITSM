-- 057: Ticket type creation permissions + per-type notification events
-- Adds granular tickets.create.{type} permissions so admins can control
-- which roles/groups can create support/task/bug/feature tickets.
-- Also adds per-type notification events for task/bug/feature creation.

SET search_path TO helpdesk, public;

-- ============================================================
-- 1. New permissions
-- ============================================================

INSERT INTO permissions (slug, label, category, description) VALUES
    ('tickets.create.support', 'Create Support Cases',    'Tickets', 'Can create Support tickets'),
    ('tickets.create.task',    'Create Tasks',             'Tickets', 'Can create Task work items'),
    ('tickets.create.bug',     'Create Bug Reports',       'Tickets', 'Can create Bug Report work items'),
    ('tickets.create.feature', 'Create Feature Requests',  'Tickets', 'Can create Feature Request work items')
ON CONFLICT (slug) DO NOTHING;

-- ============================================================
-- 2. Assign to default groups (all tenants)
-- ============================================================

-- Admins group gets all 4 type permissions
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name = 'Admins'
  AND p.slug IN ('tickets.create.support', 'tickets.create.task', 'tickets.create.bug', 'tickets.create.feature')
ON CONFLICT DO NOTHING;

-- Managers group gets all 4
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name = 'Managers'
  AND p.slug IN ('tickets.create.support', 'tickets.create.task', 'tickets.create.bug', 'tickets.create.feature')
ON CONFLICT DO NOTHING;

-- Senior Agents get all 4
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name = 'Senior Agents'
  AND p.slug IN ('tickets.create.support', 'tickets.create.task', 'tickets.create.bug', 'tickets.create.feature')
ON CONFLICT DO NOTHING;

-- Agents get all 4
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name = 'Agents'
  AND p.slug IN ('tickets.create.support', 'tickets.create.task', 'tickets.create.bug', 'tickets.create.feature')
ON CONFLICT DO NOTHING;

-- End-users only get support (NOT task/bug/feature)
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g
CROSS JOIN permissions p
WHERE g.name IN ('End Users', 'end_user')
  AND p.slug = 'tickets.create.support'
ON CONFLICT DO NOTHING;

-- ============================================================
-- 3. Notification events for per-type ticket creation
-- ============================================================

INSERT INTO notification_preferences (tenant_id, event, channel, role_target, enabled)
SELECT t.id, ev.event, 'email', ev.role_target, true
FROM tenants t
CROSS JOIN (VALUES
    ('task_created',    'assignee'),
    ('bug_created',     'assignee'),
    ('bug_created',     'all_agents'),
    ('feature_created', 'assignee')
) AS ev(event, role_target)
ON CONFLICT (tenant_id, event, channel, role_target) DO NOTHING;
