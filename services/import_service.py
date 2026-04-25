"""Location import service: parse CSV/JSON/Excel and batch-insert locations."""

import csv
import io
import json
import logging

from models.db import fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)


def parse_locations_csv(content: str) -> list[dict]:
    """Parse Freshservice-format CSV into location records.

    Expected columns: Location Name, Parent Location, Contact Name, Email,
    Phone, Address Line1, Address Line2, City, State, Country, ZipCode
    """
    reader = csv.DictReader(io.StringIO(content))
    locations = []
    for row in reader:
        name = (row.get("Location Name") or "").strip()
        if not name:
            continue
        locations.append({
            "name": name,
            "parent_name": (row.get("Parent Location") or "").strip() or None,
            "contact_name": (row.get("Contact Name") or "").strip() or None,
            "email": (row.get("Email") or "").strip() or None,
            "phone": (row.get("Phone") or "").strip() or None,
            "address": (row.get("Address Line1") or "").strip() or None,
            "city": (row.get("City") or "").strip() or None,
            "state": (row.get("State") or "").strip() or None,
            "country": (row.get("Country") or "").strip() or None,
            "zipcode": (row.get("ZipCode") or "").strip() or None,
        })
    return locations


def parse_locations_json(content: str) -> list[dict]:
    """Parse JSON array of location objects.

    Supports flat format: [{"name": "...", "parent_name": "..."}]
    Or nested format: [{"name": "...", "children": [...]}]
    """
    data = json.loads(content)
    if not isinstance(data, list):
        data = [data]

    locations = []
    _flatten_json_locations(data, None, locations)
    return locations


def _flatten_json_locations(items: list, parent_name: str | None, result: list):
    """Recursively flatten nested JSON structure."""
    for item in items:
        if isinstance(item, str):
            result.append({"name": item, "parent_name": parent_name})
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        pn = (item.get("parent_name") or "").strip() or parent_name
        result.append({"name": name, "parent_name": pn})
        children = item.get("children", [])
        if children:
            _flatten_json_locations(children, name, result)


def parse_locations_excel(content: bytes) -> list[dict]:
    """Parse Excel (.xlsx) file with same columns as CSV."""
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

    # First row is headers
    headers = [str(h or "").strip() for h in rows[0]]
    locations = []
    for row in rows[1:]:
        row_dict = dict(zip(headers, row))
        name = str(row_dict.get("Location Name") or "").strip()
        if not name:
            continue
        locations.append({
            "name": name,
            "parent_name": str(row_dict.get("Parent Location") or "").strip() or None,
        })

    wb.close()
    return locations


def resolve_and_insert_locations(tenant_id: int, locations: list[dict]) -> dict:
    """Insert locations, resolving parent references by name. Two-pass approach.

    Returns dict with created, skipped, linked counts.
    """
    name_to_id = {}
    created = 0
    skipped = 0

    # Load existing locations for this tenant (to detect duplicates and resolve parents)
    from models.db import fetch_all
    existing = fetch_all(
        "SELECT id, name FROM locations WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )
    for row in existing:
        name_to_id[row["name"]] = row["id"]

    # Pass 1: Insert all locations without parent_id
    for loc in locations:
        if loc["name"] in name_to_id:
            skipped += 1
            continue
        loc_id = insert_returning(
            "INSERT INTO locations (tenant_id, name, created_via) VALUES (%s, %s, 'import') RETURNING id",
            [tenant_id, loc["name"]],
        )
        name_to_id[loc["name"]] = loc_id
        created += 1

    # Pass 2: Resolve parent references
    linked = 0
    for loc in locations:
        parent_name = loc.get("parent_name")
        if not parent_name or parent_name not in name_to_id:
            continue
        loc_id = name_to_id.get(loc["name"])
        parent_id = name_to_id[parent_name]
        if loc_id and parent_id and loc_id != parent_id:
            execute(
                "UPDATE locations SET parent_id = %s WHERE id = %s AND parent_id IS NULL",
                [parent_id, loc_id],
            )
            linked += 1

    logger.info(
        "Location import for tenant %s: created=%d, skipped=%d, linked=%d",
        tenant_id, created, skipped, linked,
    )
    return {"created": created, "skipped": skipped, "linked": linked}
