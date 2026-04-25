"""RBAC permission resolution service.

Resolution order (highest priority wins):
1. super_admin → always has ALL permissions (bypass)
2. user_permission_overrides → explicit grant/deny per user
3. group_permissions → union of all groups the user belongs to
4. Role defaults → backward compat for users not yet in any group
"""

import logging
from flask import session
from models.db import fetch_all

logger = logging.getLogger(__name__)

# Role-based defaults for backward compat (users with no group memberships)
_ROLE_DEFAULTS: dict[str, set[str]] = {
    "super_admin": set(),  # bypass — never hits defaults
    "tenant_admin": {
        "tickets.view", "tickets.create", "tickets.close", "tickets.assign",
        "tickets.create.support", "tickets.create.task", "tickets.create.bug", "tickets.create.feature", "tickets.create.custom",
        "categories.manage", "locations.manage", "kb.manage",
        "audit.view", "audit.review", "audit.kba",
        "metrics.view", "reports.view", "atlas.chat", "atlas.admin",
        "users.invite", "users.manage", "phone.manage",
        "automations.manage", "sprints.manage",
        "connectors.manage", "notifications.manage", "teams.manage",
    },
    "agent": {
        "tickets.view", "tickets.create", "tickets.close", "tickets.assign",
        "tickets.create.support", "tickets.create.task", "tickets.create.bug", "tickets.create.feature", "tickets.create.custom",
        "atlas.chat", "audit.view",
    },
    "end_user": {
        "tickets.view", "tickets.create",
        "tickets.create.support", "tickets.create.custom",
    },
}


def get_user_permissions(user_id: int, role: str) -> list[str]:
    """Resolve effective permissions for a user.

    Returns sorted list of permission slugs.
    """
    # super_admin bypasses everything
    if role == "super_admin":
        rows = fetch_all("SELECT slug FROM permissions ORDER BY slug")
        return [r["slug"] for r in rows]

    # 1. Get all group permissions (union across all groups)
    group_perms = fetch_all(
        """SELECT DISTINCT p.slug
           FROM user_group_memberships ugm
           JOIN group_permissions gp ON gp.group_id = ugm.group_id
           JOIN permissions p ON p.id = gp.permission_id
           JOIN groups g ON g.id = ugm.group_id AND g.is_active = true
           WHERE ugm.user_id = %s""",
        [user_id],
    )
    effective = {r["slug"] for r in group_perms}

    # 2. If user has no group memberships, fall back to role defaults
    if not effective:
        effective = set(_ROLE_DEFAULTS.get(role, set()))

    # 3. Apply per-user overrides (grant or deny)
    overrides = fetch_all(
        """SELECT p.slug, upo.granted
           FROM user_permission_overrides upo
           JOIN permissions p ON p.id = upo.permission_id
           WHERE upo.user_id = %s""",
        [user_id],
    )
    for ov in overrides:
        if ov["granted"]:
            effective.add(ov["slug"])
        else:
            effective.discard(ov["slug"])

    return sorted(effective)


def enrich_session_permissions(user_dict: dict) -> dict:
    """Add resolved permissions list to a session user dict.

    Call this during login/session establishment.
    Returns the enriched dict (mutates in place too).
    """
    try:
        perms = get_user_permissions(user_dict["id"], user_dict.get("role", ""))
    except Exception as e:
        logger.warning("Failed to load permissions for user %s: %s", user_dict.get("id"), e)
        # Fall back to role defaults
        perms = sorted(_ROLE_DEFAULTS.get(user_dict.get("role", ""), set()))

    user_dict["permissions"] = perms
    return user_dict


def has_permission(slug: str) -> bool:
    """Check if current session user has a specific permission.

    Shorthand for route/template use.
    """
    user = session.get("user", {})
    if user.get("role") == "super_admin":
        return True
    return slug in (user.get("permissions") or [])


def get_all_permissions() -> list[dict]:
    """Return all system-defined permissions, grouped by category."""
    return fetch_all("SELECT id, slug, label, category, description FROM permissions ORDER BY category, slug")
