"""Tenant setup helpers: seed defaults for new tenants."""

import logging

from models.db import execute, fetch_one, fetch_all, insert_returning

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_PREFERENCES = [
    ("ticket_created", "requester"),
    ("ticket_created", "assignee"),
    ("ticket_assigned", "assignee"),
    ("ticket_resolved", "requester"),
    ("ticket_closed", "requester"),
    ("agent_reply", "requester"),
    ("requester_reply", "assignee"),
    ("sla_warning", "assignee"),
    ("sla_breach", "assignee"),
]


DEFAULT_GROUPS = [
    {
        "name": "Agents",
        "is_default": True,
        "permissions": [
            "tickets.view", "tickets.create", "tickets.create.support",
            "tickets.create.task", "tickets.create.bug", "tickets.create.feature",
            "atlas.chat", "sprints.manage",
        ],
    },
    {
        "name": "Senior Agents",
        "is_default": False,
        "permissions": [
            "tickets.view", "tickets.create", "tickets.close", "tickets.assign",
            "tickets.create.support", "tickets.create.task", "tickets.create.bug", "tickets.create.feature",
            "atlas.chat", "audit.view", "sprints.manage",
        ],
    },
    {
        "name": "Managers",
        "is_default": False,
        "permissions": [
            "tickets.view", "tickets.create", "tickets.close", "tickets.assign",
            "tickets.create.support", "tickets.create.task", "tickets.create.bug", "tickets.create.feature",
            "atlas.chat", "audit.view", "audit.review", "audit.kba",
            "categories.manage", "locations.manage", "kb.manage",
            "metrics.view", "reports.view", "phone.manage",
            "automations.manage", "sprints.manage",
        ],
    },
    {
        "name": "Admins",
        "is_default": False,
        "permissions": [
            "tickets.view", "tickets.create", "tickets.close", "tickets.assign",
            "tickets.create.support", "tickets.create.task", "tickets.create.bug", "tickets.create.feature",
            "atlas.chat", "atlas.admin",
            "audit.view", "audit.review", "audit.kba",
            "categories.manage", "locations.manage", "kb.manage",
            "metrics.view", "reports.view", "phone.manage",
            "users.invite", "users.manage",
            "automations.manage", "sprints.manage",
            "connectors.manage", "notifications.manage", "teams.manage",
        ],
    },
]


def seed_default_groups(tenant_id: int):
    """Create default RBAC groups with permissions for a new tenant."""
    try:
        existing = fetch_one(
            "SELECT count(*) as cnt FROM groups WHERE tenant_id = %s",
            [tenant_id],
        )
        if existing and existing["cnt"] > 0:
            return

        all_perms = {r["slug"]: r["id"] for r in fetch_all("SELECT id, slug FROM permissions")}

        for group_def in DEFAULT_GROUPS:
            group_id = insert_returning(
                """INSERT INTO groups (tenant_id, name, is_default, is_active)
                   VALUES (%s, %s, %s, true) RETURNING id""",
                [tenant_id, group_def["name"], group_def["is_default"]],
            )
            for slug in group_def["permissions"]:
                perm_id = all_perms.get(slug)
                if perm_id:
                    execute(
                        """INSERT INTO group_permissions (group_id, permission_id)
                           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                        [group_id, perm_id],
                    )
        logger.info("Seeded default RBAC groups for tenant %s", tenant_id)
    except Exception as e:
        logger.error("Failed to seed groups for tenant %s: %s", tenant_id, e)


def seed_notification_preferences(tenant_id: int):
    """Insert default email notification preferences for a new tenant."""
    try:
        for event, role_target in DEFAULT_EMAIL_PREFERENCES:
            execute(
                """INSERT INTO notification_preferences (tenant_id, event, channel, role_target)
                   VALUES (%s, %s, 'email', %s)
                   ON CONFLICT DO NOTHING""",
                [tenant_id, event, role_target],
            )
        logger.info("Seeded notification preferences for tenant %s", tenant_id)
    except Exception as e:
        logger.error("Failed to seed notification preferences for tenant %s: %s", tenant_id, e)
