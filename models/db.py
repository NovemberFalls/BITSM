"""PostgreSQL connection pool and query helpers for the helpdesk database."""

import logging
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

from config import Config

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def init_pool():
    """Initialize the threaded connection pool."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=Config.PG_POOL_MIN,
            maxconn=Config.PG_POOL_MAX,
            host=Config.PG_HOST,
            port=Config.PG_PORT,
            dbname=Config.PG_DATABASE,
            user=Config.PG_USER,
            password=Config.PG_PASSWORD,
            options="-c search_path=helpdesk,public",
        )
        logger.info("PostgreSQL pool initialized (%s:%s/%s)", Config.PG_HOST, Config.PG_PORT, Config.PG_DATABASE)


def close_pool():
    """Close all connections in the pool."""
    global _pool
    with _pool_lock:
        if _pool:
            _pool.closeall()
            _pool = None
            logger.info("PostgreSQL pool closed")


_POOL_RETRIES    = 12    # max retries when pool is exhausted
_POOL_RETRY_WAIT = 0.25  # seconds between retries (3s total max wait)


def _get_conn():
    """Get a connection from the pool. Retries with backoff if pool is exhausted."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    for attempt in range(_POOL_RETRIES):
        try:
            return _pool.getconn()
        except psycopg2.pool.PoolError:
            if attempt >= _POOL_RETRIES - 1:
                raise
            time.sleep(_POOL_RETRY_WAIT)
    raise psycopg2.pool.PoolError("connection pool exhausted after retries")


def _put_conn(conn):
    if _pool is not None:
        _pool.putconn(conn)


@contextmanager
def cursor(dict_cursor=True):
    """Context manager yielding a database cursor. Auto-commits on success, rolls back on error.

    If the connection is broken (OperationalError / InterfaceError), it is
    discarded from the pool rather than returned, so the next caller gets a
    fresh connection.
    """
    conn = _get_conn()
    factory = psycopg2.extras.RealDictCursor if dict_cursor else None
    broken = False
    try:
        with conn.cursor(cursor_factory=factory) as cur:
            yield cur
        conn.commit()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        if broken:
            # Discard the dead connection; pool will create a new one on demand.
            if _pool is not None:
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    pass
        else:
            _put_conn(conn)


# ============================================================
# Generic query helpers
# ============================================================

def fetch_one(sql: str, params=None) -> dict | None:
    """Execute query and return a single row as dict, or None."""
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_all(sql: str, params=None) -> list[dict]:
    """Execute query and return all rows as list of dicts."""
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params=None) -> int:
    """Execute a statement and return rowcount."""
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def insert_returning(sql: str, params=None, col: str = "id"):
    """Execute an INSERT ... RETURNING and return the value."""
    with cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[col] if row else None


# ============================================================
# Tenant-scoped helpers
# ============================================================

def fetch_all_tenant(sql: str, tenant_id: int, params=None) -> list[dict]:
    """Execute query with tenant_id prepended to params."""
    full_params = [tenant_id] + (list(params) if params else [])
    return fetch_all(sql, full_params)


def fetch_one_tenant(sql: str, tenant_id: int, params=None) -> dict | None:
    """Execute query with tenant_id prepended to params."""
    full_params = [tenant_id] + (list(params) if params else [])
    return fetch_one(sql, full_params)
