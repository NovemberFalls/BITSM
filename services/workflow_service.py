"""Workflow service: ticket status transitions per ticket_type.

Each ticket_type has an ordered list of valid statuses. Transitions are validated
here before any status change is persisted. Tenants can override the default
workflows via the ticket_status_workflows table.
"""

import logging

from models.db import fetch_one

logger = logging.getLogger(__name__)

VALID_TICKET_TYPES = ("support", "task", "bug", "feature", "custom")

# In-memory cache: (tenant_id, ticket_type) → [status_dict, ...]
_workflow_cache: dict[tuple[int | None, str], list[dict]] = {}


def get_workflow(tenant_id: int | None, ticket_type: str) -> list[dict]:
    """Return the ordered status list for a ticket type.

    Checks tenant override first, then system default (tenant_id=NULL).
    Returns list of dicts: [{"key":"open","label":"Open","category":"active"}, ...]
    """
    cache_key = (tenant_id, ticket_type)
    if cache_key in _workflow_cache:
        return _workflow_cache[cache_key]

    # Try tenant-specific override
    row = None
    if tenant_id:
        row = fetch_one(
            "SELECT statuses FROM ticket_status_workflows WHERE tenant_id = %s AND ticket_type = %s",
            [tenant_id, ticket_type],
        )

    # Fall back to system default
    if not row:
        row = fetch_one(
            "SELECT statuses FROM ticket_status_workflows WHERE tenant_id IS NULL AND ticket_type = %s",
            [ticket_type],
        )

    if not row:
        logger.warning("No workflow found for ticket_type=%s, tenant=%s", ticket_type, tenant_id)
        return []

    statuses = row["statuses"] if isinstance(row["statuses"], list) else []
    _workflow_cache[cache_key] = statuses
    return statuses


def get_status_keys(tenant_id: int | None, ticket_type: str) -> list[str]:
    """Return just the status keys in order."""
    return [s["key"] for s in get_workflow(tenant_id, ticket_type)]


def is_valid_status(tenant_id: int | None, ticket_type: str, status: str) -> bool:
    """Check if a status is valid for the given ticket type."""
    return status in get_status_keys(tenant_id, ticket_type)


def get_initial_status(ticket_type: str) -> str:
    """Return the first status for a ticket type (using system defaults)."""
    if ticket_type in ("support", "custom"):
        return "open"
    return "backlog"


def get_done_statuses(tenant_id: int | None, ticket_type: str) -> list[str]:
    """Return status keys with category 'done' for a ticket type."""
    return [s["key"] for s in get_workflow(tenant_id, ticket_type)
            if s.get("category") == "done"]


def is_done_status(tenant_id: int | None, ticket_type: str, status: str) -> bool:
    """Check if a status is a terminal/done status for the given ticket type."""
    return status in get_done_statuses(tenant_id, ticket_type)


def clear_cache():
    """Clear the workflow cache (call after workflow changes)."""
    _workflow_cache.clear()
