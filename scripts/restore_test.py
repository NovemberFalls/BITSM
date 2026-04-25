#!/usr/bin/env python3
"""
BITSM Restore Test — Azure Blob → temporary PostgreSQL database

Downloads the most recent daily backup from Azure Blob Storage, verifies its
SHA256 checksum against blob metadata, restores it to a temporary database, runs
a basic health check (row counts on tenants/tickets/users), then drops the temp DB.

Usage:
  python3 scripts/restore_test.py              # full restore test
  python3 scripts/restore_test.py --dry-run    # download + checksum verify only, no restore

Environment variables (or /run/bitsm/env):
  AZURE_STORAGE_CONNECTION_STRING   Azure Blob Storage connection string
  HELPDESK_PG_HOST                  PostgreSQL host (default: localhost)
  HELPDESK_PG_PORT                  PostgreSQL port (default: 5433)
  HELPDESK_PG_USER                  PostgreSQL superuser for restore (default: helpdesk_app)
  HELPDESK_PG_PASSWORD              PostgreSQL password

Cron example (04:00 UTC daily, after the 02:00 backup):
  0 4 * * * /opt/bitsm/scripts/restore_test.py >> /var/log/bitsm-restore-test.log 2>&1
"""

import argparse
import datetime
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import traceback

# ── Constants ─────────────────────────────────────────────────────────────────

CONTAINER        = "bitsm-backups"
DAILY_PREFIX     = "db/"
RESTORE_TEST_DB  = "bitsm_restore_test"
LOG_FILE         = "/var/log/bitsm-restore-test.log"

HEALTH_QUERIES = [
    ("tenants", "SELECT COUNT(*) FROM tenants"),
    ("tickets", "SELECT COUNT(*) FROM tickets"),
    ("users",   "SELECT COUNT(*) FROM users"),
]

# ── Load env ──────────────────────────────────────────────────────────────────

def load_env(env_path: str = "/run/bitsm/env") -> dict:
    """Parse key=value pairs from .env file."""
    env = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except (FileNotFoundError, PermissionError) as e:
        # PermissionError: file exists but this process cannot read it.
        # Logged here so cron output captures the reason instead of silent empty env.
        if isinstance(e, PermissionError):
            print(f"WARNING: Cannot read {env_path}: {e}", file=sys.stderr, flush=True)
    return env

_env = load_env()

CONN_STR    = _env.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
PG_HOST     = _env.get("HELPDESK_PG_HOST")     or os.environ.get("HELPDESK_PG_HOST",     "localhost")
PG_PORT     = _env.get("HELPDESK_PG_PORT")     or os.environ.get("HELPDESK_PG_PORT",     "5433")
PG_USER     = _env.get("HELPDESK_PG_USER")     or os.environ.get("HELPDESK_PG_USER",     "helpdesk_app")
PG_PASSWORD = _env.get("HELPDESK_PG_PASSWORD") or os.environ.get("HELPDESK_PG_PASSWORD", "")

# ── Logging ───────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)

def log_result(success: bool, detail: str = "") -> None:
    """Write a final SUCCESS / FAILURE line to the log file."""
    status = "SUCCESS" if success else "FAILURE"
    line = f"[{ts()}] {status}" + (f": {detail}" if detail else "")
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # non-fatal — stdout already has the line

# ── Azure helpers ─────────────────────────────────────────────────────────────

def get_most_recent_daily(service_client) -> tuple[str, str | None]:
    """
    Return (blob_name, sha256_from_metadata) for the most recent blob under
    DAILY_PREFIX, sorted by name (YYYY-MM-DD lexicographic = chronological).
    sha256_from_metadata is None if the blob has no sha256 metadata key.
    Raises RuntimeError if no blobs are found.
    """
    container = service_client.get_container_client(CONTAINER)
    blobs = sorted(
        container.list_blobs(name_starts_with=DAILY_PREFIX, include=["metadata"]),
        key=lambda b: b.name,
        reverse=True,
    )
    if not blobs:
        raise RuntimeError(f"No blobs found under {CONTAINER}/{DAILY_PREFIX}")

    latest = blobs[0]
    sha256_meta = (latest.metadata or {}).get("sha256")
    log(f"Most recent daily backup: {latest.name}  ({(latest.size or 0) / 1024 / 1024:.1f} MB)")
    if sha256_meta:
        log(f"Metadata SHA256: {sha256_meta}")
    else:
        log("WARNING: No sha256 metadata on blob — checksum verification will be skipped.")
    return latest.name, sha256_meta


def download_blob(service_client, blob_name: str) -> bytes:
    """Download blob content into memory and return raw bytes."""
    container = service_client.get_container_client(CONTAINER)
    blob_client = container.get_blob_client(blob_name)
    log(f"Downloading {blob_name} ...")
    stream = blob_client.download_blob()
    data = stream.readall()
    log(f"Download complete: {len(data) / 1024 / 1024:.1f} MB")
    return data

# ── Checksum ──────────────────────────────────────────────────────────────────

def verify_checksum(data: bytes, expected_hex: str | None) -> bool:
    """
    Compute SHA256 of data, compare to expected_hex.
    If expected_hex is None, logs a warning and returns True (cannot verify).
    Returns True on match, False on mismatch.
    """
    actual = hashlib.sha256(data).hexdigest()
    log(f"Computed SHA256: {actual}")
    if expected_hex is None:
        log("Checksum verification SKIPPED (no metadata).")
        return True
    if actual == expected_hex:
        log("Checksum OK.")
        return True
    log(f"Checksum MISMATCH: expected {expected_hex}, got {actual}")
    return False

# ── PostgreSQL helpers ────────────────────────────────────────────────────────

def _pg_env() -> dict:
    e = os.environ.copy()
    if PG_PASSWORD:
        e["PGPASSWORD"] = PG_PASSWORD
    return e

def _psql(sql: str, dbname: str = "postgres") -> subprocess.CompletedProcess:
    """Run a psql command against a target database, return CompletedProcess."""
    cmd = [
        "psql",
        "-h", PG_HOST,
        "-p", str(PG_PORT),
        "-U", PG_USER,
        "-d", dbname,
        "-c", sql,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=_pg_env())


def drop_and_create_restore_db() -> None:
    """Drop RESTORE_TEST_DB if it exists, then create it fresh."""
    log(f"Dropping existing restore DB (if any): {RESTORE_TEST_DB}")
    r = _psql(f"DROP DATABASE IF EXISTS {RESTORE_TEST_DB};")
    if r.returncode != 0:
        raise RuntimeError(f"DROP DATABASE failed:\n{r.stderr}")

    log(f"Creating restore DB: {RESTORE_TEST_DB}")
    r = _psql(f"CREATE DATABASE {RESTORE_TEST_DB};")
    if r.returncode != 0:
        raise RuntimeError(f"CREATE DATABASE failed:\n{r.stderr}")


def restore_dump(dump_gz_data: bytes) -> None:
    """
    Decrypt (if encrypted), decompress gzip wrapper, then pipe raw pg_dump
    custom-format data through pg_restore into RESTORE_TEST_DB.
    """
    import gzip

    # Decrypt if encryption key is available and data looks like Fernet token
    decryption_key = os.environ.get("BACKUP_ENCRYPTION_KEY") or os.environ.get("FERNET_KEY")
    if decryption_key and dump_gz_data[:5] == b"gAAAA":
        log("Encrypted backup detected — decrypting ...")
        from cryptography.fernet import Fernet
        f = Fernet(decryption_key.encode() if isinstance(decryption_key, str) else decryption_key)
        dump_gz_data = f.decrypt(dump_gz_data)
        log(f"Decrypted: {len(dump_gz_data) / 1024 / 1024:.1f} MB")

    log("Decompressing gzip wrapper ...")
    raw_dump = gzip.decompress(dump_gz_data)
    log(f"Decompressed: {len(raw_dump) / 1024 / 1024:.1f} MB — starting pg_restore ...")

    cmd = [
        "pg_restore",
        "-h", PG_HOST,
        "-p", str(PG_PORT),
        "-U", PG_USER,
        "-d", RESTORE_TEST_DB,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
    ]
    result = subprocess.run(
        cmd,
        input=raw_dump,
        capture_output=True,
        env=_pg_env(),
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"pg_restore failed (exit {result.returncode}):\n{stderr}")
    log("pg_restore complete.")


def run_health_checks() -> dict[str, int]:
    """
    Run each health check query against RESTORE_TEST_DB.
    Returns dict of {table_name: row_count}.
    Raises RuntimeError on any query failure.
    """
    counts: dict[str, int] = {}
    for label, sql in HEALTH_QUERIES:
        r = _psql(sql, dbname=RESTORE_TEST_DB)
        if r.returncode != 0:
            raise RuntimeError(f"Health check '{label}' failed:\n{r.stderr}")
        # psql -c "SELECT COUNT(*)" output:
        #  count
        # -------
        #  12345
        # (1 row)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                counts[label] = int(line)
                break
        else:
            raise RuntimeError(f"Could not parse row count from output:\n{r.stdout}")
        log(f"  {label}: {counts[label]:,} rows")
    return counts


def drop_restore_db() -> None:
    """Drop the temporary restore database, ignoring errors."""
    log(f"Dropping restore DB: {RESTORE_TEST_DB}")
    r = _psql(f"DROP DATABASE IF EXISTS {RESTORE_TEST_DB};")
    if r.returncode != 0:
        log(f"WARNING: DROP DATABASE failed (non-fatal): {r.stderr.strip()}")

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="BITSM restore test")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and verify checksum only; skip actual restore and health check",
    )
    args = parser.parse_args()

    if not CONN_STR:
        log("ERROR: AZURE_STORAGE_CONNECTION_STRING not set in /run/bitsm/env or environment.")
        log_result(False, "AZURE_STORAGE_CONNECTION_STRING not configured")
        sys.exit(1)

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        log("ERROR: azure-storage-blob not installed. Run: pip3 install azure-storage-blob")
        log_result(False, "azure-storage-blob not installed")
        sys.exit(1)

    success = False
    try:
        log(f"BITSM restore test starting {'(dry-run)' if args.dry_run else ''}")
        service_client = BlobServiceClient.from_connection_string(CONN_STR)

        # 1. Find and download most recent daily backup
        blob_name, expected_sha256 = get_most_recent_daily(service_client)
        data = download_blob(service_client, blob_name)

        # 2. Verify checksum
        if not verify_checksum(data, expected_sha256):
            raise RuntimeError(f"SHA256 checksum mismatch for {blob_name}")

        if args.dry_run:
            log("Dry-run: skipping restore and health checks.")
            log_result(True, f"dry-run checksum OK for {blob_name}")
            sys.exit(0)

        # 3. Create temp DB and restore
        drop_and_create_restore_db()
        restore_dump(data)

        # 4. Health checks
        log("Running health checks ...")
        counts = run_health_checks()
        summary = ", ".join(f"{k}={v:,}" for k, v in counts.items())
        log(f"Health checks passed: {summary}")

        # 5. Cleanup
        drop_restore_db()

        success = True
        log_result(True, f"{blob_name}  [{summary}]")

    except Exception:
        tb = traceback.format_exc()
        log(f"RESTORE TEST FAILED:\n{tb}")
        log_result(False, str(sys.exc_info()[1]))
        # Attempt cleanup even on failure
        try:
            drop_restore_db()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
