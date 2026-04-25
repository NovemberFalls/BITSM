"""Category import service: parse CSV/Excel and batch-insert problem categories."""

import csv
import io
import logging

from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "sev-1": "p1", "sev1": "p1", "p1": "p1", "1": "p1",
    "sev-2": "p2", "sev2": "p2", "p2": "p2", "2": "p2",
    "sev-3": "p3", "sev3": "p3", "p3": "p3", "3": "p3",
    "sev-4": "p4", "sev4": "p4", "p4": "p4", "4": "p4",
}


def _map_severity(val: str | None) -> str | None:
    if not val:
        return None
    return SEVERITY_MAP.get(val.strip().lower())


def parse_categories_excel(content: bytes) -> list[dict]:
    """Parse Excel with columns as hierarchy tiers (left to right) + optional Severity column."""
    try:
        import openpyxl
    except ImportError:
        raise ValueError("openpyxl is not installed. Run: pip install openpyxl")

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if not ws:
        return []

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h or "").strip() for h in rows[0]]

    # Identify tier columns (everything except Severity and Notes)
    _SKIP_COLS = {"severity", "priority", "default_priority", "notes", "instructions"}
    tier_cols = []
    severity_col = None
    for i, h in enumerate(headers):
        if h.lower() in ("severity", "priority", "default_priority"):
            severity_col = i
        elif h.lower() not in _SKIP_COLS:
            tier_cols.append(i)

    categories = []
    for row in rows[1:]:
        # Build the path from left to right
        path = []
        for col_idx in tier_cols:
            val = row[col_idx] if col_idx < len(row) else None
            if val is not None:
                val = str(val).strip()
                if val:
                    path.append(val)

        if not path:
            continue

        severity = None
        if severity_col is not None and severity_col < len(row):
            severity = _map_severity(str(row[severity_col] or ""))

        categories.append({"path": path, "default_priority": severity})

    wb.close()
    return categories


def parse_categories_csv(content: str) -> list[dict]:
    """Parse CSV with columns as hierarchy tiers + optional Severity column."""
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in rows[0]]

    _SKIP_COLS = {"severity", "priority", "default_priority", "notes", "instructions"}
    tier_cols = []
    severity_col = None
    for i, h in enumerate(headers):
        if h.lower() in ("severity", "priority", "default_priority"):
            severity_col = i
        elif h.lower() not in _SKIP_COLS:
            tier_cols.append(i)

    categories = []
    for row in rows[1:]:
        path = []
        for col_idx in tier_cols:
            val = row[col_idx].strip() if col_idx < len(row) else ""
            if val:
                path.append(val)
        if not path:
            continue

        severity = None
        if severity_col is not None and severity_col < len(row):
            severity = _map_severity(row[severity_col])

        categories.append({"path": path, "default_priority": severity})

    return categories


def resolve_and_insert_categories(tenant_id: int, categories: list[dict]) -> dict:
    """Insert categories from parsed paths, deduplicating by name+parent_id.

    Returns dict with created, skipped counts.
    """
    created = 0
    skipped = 0

    # Load existing categories for this tenant
    existing = fetch_all(
        "SELECT id, parent_id, name FROM problem_categories WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )

    # Build lookup: (parent_id, name) -> id
    key_to_id: dict[tuple, int] = {}
    for row in existing:
        key_to_id[(row["parent_id"], row["name"])] = row["id"]

    def ensure_node(name: str, parent_id: int | None) -> int:
        nonlocal created, skipped
        key = (parent_id, name)
        if key in key_to_id:
            skipped += 1
            return key_to_id[key]

        cat_id = insert_returning(
            """INSERT INTO problem_categories (tenant_id, parent_id, name, sort_order)
               VALUES (%s, %s, %s, 0) RETURNING id""",
            [tenant_id, parent_id, name],
        )
        key_to_id[key] = cat_id
        created += 1
        return cat_id

    for cat in categories:
        path = cat["path"]
        parent_id = None
        leaf_id = None
        for name in path:
            leaf_id = ensure_node(name, parent_id)
            parent_id = leaf_id

        # Set default_priority on the leaf node
        if leaf_id and cat.get("default_priority"):
            execute(
                "UPDATE problem_categories SET default_priority = %s WHERE id = %s",
                [cat["default_priority"], leaf_id],
            )

    logger.info(
        "Category import for tenant %s: created=%d, skipped=%d",
        tenant_id, created, skipped,
    )
    return {"created": created, "skipped": skipped}
