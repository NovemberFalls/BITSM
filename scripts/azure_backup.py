#!/usr/bin/env python3
"""
BITSM Azure Backup — PostgreSQL → Azure Blob Storage
GFS rotation: daily×7, weekly×4, monthly×3

Runs on the Azure VM alongside the Docker stack.
Dumps the helpdesk DB from bitsm-postgres-1 and uploads to Azure Blob.

Usage:
  python3 scripts/azure_backup.py              # full backup + rotate
  python3 scripts/azure_backup.py --dry-run    # show rotation plan, no deletes
  python3 scripts/azure_backup.py --rotate-only  # skip dump, rotate blobs only

Cron (02:00 UTC daily):
  0 2 * * * /opt/bitsm/scripts/azure_backup.py >> /var/log/bitsm-backup.log 2>&1
"""

import argparse
import datetime
import hashlib
import io
import os
import subprocess
import sys
import traceback
from pathlib import Path

# ── Load env ──────────────────────────────────────────────────────────────────

# Secrets are fetched from Azure Key Vault into a tmpfs path at service start by
# bitsm-fetch-secrets.sh. /run/bitsm/env only exists while the service is running
# (tmpfs, cleared on reboot). Cron may run when the BITSM service is stopped or
# after a reboot, so we self-heal by invoking the fetch script if the env file is absent.
ENV_FILE = "/run/bitsm/env"

if not os.path.exists(ENV_FILE) or not os.access(ENV_FILE, os.R_OK):
    # Fetch secrets from Key Vault into tmpfs so cron can proceed independently
    # of whether the BITSM service is active, or if the file exists but is not
    # readable by this process (e.g., mode 600 root-owned, cron running as deploy).
    # Requires the managed-identity role assignment on the host VM.
    subprocess.run(["/usr/local/bin/bitsm-fetch-secrets.sh"], check=True)


def load_env(env_path: str = ENV_FILE) -> dict:
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

env = load_env()

CONN_STR    = env.get("AZURE_STORAGE_CONNECTION_STRING") or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
PG_PASSWORD = env.get("HELPDESK_PG_PASSWORD") or os.environ.get("HELPDESK_PG_PASSWORD", "")
PG_USER     = env.get("HELPDESK_PG_USER", "helpdesk_app")
PG_DATABASE = env.get("HELPDESK_PG_DATABASE", "helpdesk")
BACKUP_KEY  = env.get("BACKUP_ENCRYPTION_KEY") or env.get("FERNET_KEY") or os.environ.get("BACKUP_ENCRYPTION_KEY") or os.environ.get("FERNET_KEY")

CONTAINER   = "bitsm-backups"
BLOB_PREFIX = "db/"          # blobs live at db/YYYY-MM-DD.dump.gz

# GFS retention
KEEP_DAILY     = 7   # 1 per day for 1 week
KEEP_WEEKLY    = 4   # 1 per week for 1 month
KEEP_MONTHLY   = 3   # 1 per month for 1 quarter
KEEP_QUARTERLY = 4   # 1 per quarter for 1 year
KEEP_YEARLY    = 5   # 1 per year for 5 years

# ── Helpers ───────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)

def bail(msg: str) -> None:
    print(f"[{ts()}] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def blob_name(date: datetime.date) -> str:
    return f"{BLOB_PREFIX}{date.isoformat()}.dump.gz"


def date_from_blob(name: str) -> datetime.date | None:
    """Extract date from blob name like db/YYYY-MM-DD.dump.gz"""
    stem = name.removeprefix(BLOB_PREFIX)
    try:
        return datetime.date.fromisoformat(stem.replace(".dump.gz", ""))
    except ValueError:
        return None


# ── GFS rotation ──────────────────────────────────────────────────────────────

def compute_keep_set(all_dates: list[datetime.date], today: datetime.date) -> set[datetime.date]:
    keep: set[datetime.date] = set()

    # Daily: last N days
    for i in range(KEEP_DAILY):
        keep.add(today - datetime.timedelta(days=i))

    # Weekly: most recent backup in each of the last N ISO weeks
    seen_weeks: dict[tuple, datetime.date] = {}
    for d in sorted(all_dates, reverse=True):
        wk = d.isocalendar()[:2]
        if wk not in seen_weeks:
            seen_weeks[wk] = d
    for _, d in sorted(seen_weeks.items(), reverse=True)[:KEEP_WEEKLY]:
        keep.add(d)

    # Monthly: most recent backup in each of the last N calendar months
    seen_months: dict[tuple, datetime.date] = {}
    for d in sorted(all_dates, reverse=True):
        mk = (d.year, d.month)
        if mk not in seen_months:
            seen_months[mk] = d
    for _, d in sorted(seen_months.items(), reverse=True)[:KEEP_MONTHLY]:
        keep.add(d)

    # Quarterly: most recent backup in each of the last N quarters
    def quarter(dt: datetime.date) -> tuple:
        return (dt.year, (dt.month - 1) // 3 + 1)

    seen_quarters: dict[tuple, datetime.date] = {}
    for d in sorted(all_dates, reverse=True):
        qk = quarter(d)
        if qk not in seen_quarters:
            seen_quarters[qk] = d
    for _, d in sorted(seen_quarters.items(), reverse=True)[:KEEP_QUARTERLY]:
        keep.add(d)

    # Yearly: most recent backup in each of the last N years
    seen_years: dict[int, datetime.date] = {}
    for d in sorted(all_dates, reverse=True):
        if d.year not in seen_years:
            seen_years[d.year] = d
    for _, d in sorted(seen_years.items(), reverse=True)[:KEEP_YEARLY]:
        keep.add(d)

    return keep


def rotate(client, today: datetime.date, dry_run: bool = False) -> None:
    from azure.storage.blob import ContainerClient
    container: ContainerClient = client.get_container_client(CONTAINER)

    blobs = {b.name: b for b in container.list_blobs(name_starts_with=BLOB_PREFIX)}
    dated = {date_from_blob(n): n for n in blobs if date_from_blob(n)}

    if not dated:
        log("No existing backups found — nothing to rotate.")
        return

    keep = compute_keep_set(list(dated.keys()), today)
    deleted = 0

    for d in sorted(dated):
        blob = dated[d]
        size_mb = (blobs[blob].size or 0) / 1024 / 1024
        if d not in keep:
            action = "[DRY RUN] would delete" if dry_run else "Deleting"
            log(f"  {action}: {blob}  ({size_mb:.1f} MB)")
            if not dry_run:
                container.delete_blob(blob)
            deleted += 1
        else:
            log(f"  Keeping : {blob}  ({size_mb:.1f} MB)")

    action = "Would delete" if dry_run else "Deleted"
    log(f"Rotation done — kept {len(keep) - deleted if not dry_run else len(keep)}, {action.lower()} {deleted}.")


# ── Dump ──────────────────────────────────────────────────────────────────────

def dump_and_upload(client, today: datetime.date) -> None:
    from azure.storage.blob import ContainerClient
    container: ContainerClient = client.get_container_client(CONTAINER)
    target_blob = blob_name(today)

    # Skip if today's backup already exists
    try:
        container.get_blob_client(target_blob).get_blob_properties()
        log(f"Today's backup already exists: {target_blob} — skipping dump.")
        return
    except Exception:
        pass  # blob doesn't exist, proceed

    log(f"Starting pg_dump from bitsm-postgres-1 → {target_blob}")

    cmd = [
        "docker", "exec", "bitsm-postgres-1",
        "pg_dump",
        "-U", PG_USER,
        "-d", PG_DATABASE,
        "-Fc",          # custom format (compressed)
    ]
    env_vars = os.environ.copy()
    if PG_PASSWORD:
        env_vars["PGPASSWORD"] = PG_PASSWORD

    result = subprocess.run(cmd, capture_output=True, env=env_vars)
    if result.returncode != 0:
        bail(f"pg_dump failed:\n{result.stderr.decode()}")

    raw = result.stdout
    log(f"pg_dump complete: {len(raw) / 1024 / 1024:.1f} MB raw")

    # pg_dump -Fc is already compressed internally; wrap in gzip for consistent
    # blob naming and an extra compression pass on the custom-format envelope.
    import gzip
    compressed = gzip.compress(raw, compresslevel=6)
    log(f"Compressed to {len(compressed) / 1024 / 1024:.1f} MB")

    # SOC 2 C1.1: Encrypt backup with Fernet (AES-128-CBC + HMAC-SHA256)
    encrypted = False
    upload_data = compressed
    if BACKUP_KEY:
        from cryptography.fernet import Fernet
        f = Fernet(BACKUP_KEY.encode() if isinstance(BACKUP_KEY, str) else BACKUP_KEY)
        upload_data = f.encrypt(compressed)
        encrypted = True
        log(f"Encrypted: {len(upload_data) / 1024 / 1024:.1f} MB")
    else:
        log("WARNING: No BACKUP_ENCRYPTION_KEY or FERNET_KEY — uploading unencrypted")

    sha256_hex = hashlib.sha256(upload_data).hexdigest()
    log(f"SHA256: {sha256_hex}")

    log(f"Uploading to Azure Blob: {CONTAINER}/{target_blob}")
    container.upload_blob(
        name=target_blob,
        data=io.BytesIO(upload_data),
        overwrite=True,
        content_settings=None,
        metadata={"sha256": sha256_hex, "encrypted": str(encrypted).lower()},
    )
    log(f"Upload complete: {CONTAINER}/{target_blob}  sha256={sha256_hex}  encrypted={encrypted}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="BITSM Azure backup")
    parser.add_argument("--dry-run",     action="store_true", help="Show rotation plan without deleting")
    parser.add_argument("--rotate-only", action="store_true", help="Skip dump, only rotate existing blobs")
    args = parser.parse_args()

    if not CONN_STR:
        bail("AZURE_STORAGE_CONNECTION_STRING not set — check /run/bitsm/env or re-run bitsm-fetch-secrets.sh")

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        bail("azure-storage-blob not installed. Run: pip3 install azure-storage-blob")

    try:
        today = datetime.date.today()
        log(f"BITSM backup starting — {today.isoformat()}")

        client = BlobServiceClient.from_connection_string(CONN_STR)

        if not args.rotate_only:
            dump_and_upload(client, today)

        log("Running GFS rotation...")
        rotate(client, today, dry_run=args.dry_run)

        log("Done.")
    except Exception:
        log(f"BACKUP FAILED:\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
