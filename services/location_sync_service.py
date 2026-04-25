"""Location DB sync service: connect to external DB and import locations."""

import hashlib
import logging
import secrets

logger = logging.getLogger(__name__)


DB_TYPE_PREFIXES = {
    "postgresql": "postgresql+psycopg2",
    "mysql":      "mysql+pymysql",
    "mssql":      "mssql+pyodbc",
}

DB_DEFAULT_PORTS = {
    "postgresql": 5432,
    "mysql":      3306,
    "mssql":      1433,
}


def build_connection_string(db_type: str, host: str, port: int, dbname: str, user: str, password: str) -> str:
    """Assemble a SQLAlchemy URI from structured fields."""
    from urllib.parse import quote_plus
    prefix = DB_TYPE_PREFIXES.get(db_type, "postgresql+psycopg2")
    safe_user = quote_plus(user)
    safe_pass = quote_plus(password)
    uri = f"{prefix}://{safe_user}:{safe_pass}@{host}:{port}/{dbname}"
    if db_type == "mssql":
        uri += "?driver=ODBC+Driver+17+for+SQL+Server"
    return uri


def generate_webhook_token() -> tuple[str, str]:
    """Generate a webhook token and its SHA-256 hash. Returns (token, hash)."""
    token = "hd_sync_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def build_select_query(schema: str | None, table: str, limit: int | None = None) -> str:
    """Build a safe SELECT * query, quoting identifiers."""
    table_ref = f'"{table}"' if not schema else f'"{schema}"."{table}"'
    q = f"SELECT * FROM {table_ref}"
    if limit:
        q += f" LIMIT {limit}"
    return q


def test_db_connection(connection_string: str, schema: str | None, table: str) -> dict:
    """Connect to external DB, run SELECT * LIMIT 5, return columns + rows."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        raise RuntimeError("sqlalchemy is not installed. Run: pip install sqlalchemy")

    query = build_select_query(schema, table, limit=5)
    engine = create_engine(
        connection_string,
        pool_timeout=10,
        pool_pre_ping=True,
        connect_args=_safe_connect_args(connection_string),
    )
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query))
            columns = list(result.keys())
            rows = [{col: _serialize(row._mapping[col]) for col in columns} for row in result]
        return {"columns": columns, "rows": rows}
    finally:
        engine.dispose()


LEVEL_ORDER = ["company", "country", "state", "city", "store"]


def run_sync(tenant_id: int, config: dict) -> dict:
    """Execute full sync using level-based hierarchy mapping.

    Each level config: { "column": str|None, "fixed": str|None }
    Levels with neither column nor fixed value are skipped.
    The engine builds the tree by walking levels top-to-bottom per row,
    deduplicating nodes by (parent_id, name).
    """
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        raise RuntimeError("sqlalchemy is not installed. Run: pip install sqlalchemy")

    connection_string = config["connection_string"]
    schema = config.get("schema") or None
    table  = config["table"]

    # Build ordered list of active levels
    raw_levels = config.get("levels", {})
    active_levels: list[tuple[str, dict]] = []
    for key in LEVEL_ORDER:
        cfg = raw_levels.get(key) or {}
        if cfg.get("column") or (cfg.get("fixed") or "").strip():
            active_levels.append((key, cfg))

    if not active_levels:
        return {"created": 0, "skipped": 0, "linked": 0, "total_fetched": 0}

    query = build_select_query(schema, table)
    engine = create_engine(
        connection_string,
        pool_timeout=15,
        pool_pre_ping=True,
        connect_args=_safe_connect_args(connection_string),
    )
    try:
        with engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(text(query))]
    finally:
        engine.dispose()

    if not rows:
        return {"created": 0, "skipped": 0, "linked": 0, "total_fetched": 0}

    from models.db import fetch_all, insert_returning

    # Seed cache with existing locations so we don't re-insert on subsequent syncs
    existing = fetch_all(
        "SELECT id, name, parent_id FROM locations WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )
    cache: dict[tuple, int] = {(r["parent_id"], r["name"]): r["id"] for r in existing}

    created = 0
    skipped = 0

    def get_or_create(name: str, level_label: str, parent_id: int | None) -> int:
        nonlocal created, skipped
        key = (parent_id, name)
        if key in cache:
            skipped += 1
            return cache[key]
        loc_id = insert_returning(
            "INSERT INTO locations (tenant_id, name, level_label, parent_id, created_via) VALUES (%s, %s, %s, %s, 'db_sync') RETURNING id",
            [tenant_id, name, level_label, parent_id],
        )
        cache[key] = loc_id
        created += 1
        return loc_id

    for row in rows:
        parent_id: int | None = None
        for level_key, level_cfg in active_levels:
            col   = level_cfg.get("column") or ""
            fixed = (level_cfg.get("fixed") or "").strip()
            value = str(row.get(col) or "").strip() if col else fixed
            if not value:
                continue
            label = level_key.capitalize()
            parent_id = get_or_create(value, label, parent_id)

    logger.info(
        "Location DB sync for tenant %s: fetched=%d created=%d skipped=%d",
        tenant_id, len(rows), created, skipped,
    )
    return {"created": created, "skipped": skipped, "linked": 0, "total_fetched": len(rows)}


def classify_db_error(exc: Exception) -> tuple[int, str]:
    """Return (http_status, clean_message) for a DB exception.

    Connection failures → 502 (so the caller knows the host/creds are wrong).
    Query/SQL errors    → 400 (the connection worked, the SQL is wrong).
    """
    msg = str(exc)

    # Strip verbose SQLAlchemy boilerplate — keep only the first meaningful line
    first_line = msg.splitlines()[0] if msg else "Unknown error"
    # Remove leading "(DriverError) " prefix that SQLAlchemy prepends
    import re
    first_line = re.sub(r"^\([^)]+\)\s*", "", first_line).strip()

    try:
        from sqlalchemy.exc import OperationalError, ProgrammingError, NoSuchTableError
        if isinstance(exc, OperationalError):
            return 502, f"Connection failed: {first_line}"
        if isinstance(exc, (ProgrammingError, NoSuchTableError)):
            return 400, f"Query error: {first_line}"
    except ImportError:
        pass

    return 400, first_line


def _safe_connect_args(connection_string: str) -> dict:
    """Return connect_args appropriate for the DB dialect."""
    cs = connection_string.lower()
    if cs.startswith("postgresql") or cs.startswith("postgres"):
        return {"connect_timeout": 10}
    if cs.startswith("mysql"):
        return {"connect_timeout": 10}
    return {}


def _serialize(value) -> str | None:
    """Convert a DB value to a JSON-safe string."""
    if value is None:
        return None
    return str(value)
