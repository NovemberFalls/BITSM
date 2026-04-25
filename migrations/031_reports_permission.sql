-- Migration 031: Add reports.view permission for reporting module
-- Reports access is admin-configurable via RBAC (not hardcoded to a role)

SET search_path = helpdesk, public;

INSERT INTO permissions (slug, label, category, description)
VALUES ('reports.view', 'View Reports', 'Analytics', 'Access reporting dashboard (basic reports for all tiers, advanced reports for paid tiers)')
ON CONFLICT (slug) DO NOTHING;

-- Grant reports.view to any existing group named 'Managers'
INSERT INTO group_permissions (group_id, permission_id)
SELECT g.id, p.id
FROM groups g, permissions p
WHERE g.name = 'Managers' AND g.is_active = true AND p.slug = 'reports.view'
ON CONFLICT DO NOTHING;
