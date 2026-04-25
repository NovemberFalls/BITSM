#!/usr/bin/env bash
# BITSM Migration Runner
# Tracks applied migrations in helpdesk.schema_migrations table.
# Usage:
#   bash scripts/migrate.sh                      — apply all pending
#   bash scripts/migrate.sh migrations/054_x.sql — apply specific file
#   bash scripts/migrate.sh --status             — show applied/pending

set -euo pipefail

MIGRATIONS_DIR="$(cd "$(dirname "$0")/../migrations" && pwd)"
# Discover the postgres container name (works with any docker-compose project name)
CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'postgres' | head -1)
if [ -z "$CONTAINER" ]; then
    echo "[migrate] ERROR: No running postgres container found. Is docker compose up?"
    exit 1
fi
echo "[migrate] Using postgres container: $CONTAINER"

# Load .env for DB credentials if not already in environment
if [ -f "$(dirname "$0")/../.env" ]; then
    set -a
    source "$(dirname "$0")/../.env"
    set +a
fi

PG_USER="${HELPDESK_PG_USER:-helpdesk_app}"
PG_PASS="${HELPDESK_PG_PASSWORD:-}"
PG_DB="${HELPDESK_PG_DATABASE:-helpdesk}"

run_sql() {
    docker exec -e PGPASSWORD="$PG_PASS" "$CONTAINER" \
        psql -U "$PG_USER" -d "$PG_DB" -tAq -c "$1" 2>/dev/null
}

run_file() {
    local file="$1"
    # Prepend search_path so migration files don't need to set it themselves
    (echo "SET search_path TO helpdesk;"; cat "$file") | \
        docker exec -e PGPASSWORD="$PG_PASS" -i "$CONTAINER" \
        psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1
}

# Ensure tracking table exists
run_sql "CREATE TABLE IF NOT EXISTS helpdesk.schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);" > /dev/null

# --- Status mode ---
if [ "${1:-}" = "--status" ]; then
    echo "Applied migrations:"
    run_sql "SELECT filename, applied_at FROM helpdesk.schema_migrations ORDER BY filename;"
    echo ""
    echo "Pending migrations:"
    for FILE in $(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort); do
        FNAME="$(basename "$FILE")"
        APPLIED=$(run_sql "SELECT COUNT(*) FROM helpdesk.schema_migrations WHERE filename='$FNAME';")
        if [ "$APPLIED" = "0" ]; then
            echo "  PENDING: $FNAME"
        fi
    done
    exit 0
fi

# --- Single file mode ---
if [ -n "${1:-}" ] && [ "${1:-}" != "--status" ]; then
    FILE="$1"
    FNAME="$(basename "$FILE")"
    echo "Applying $FNAME..."
    run_file "$FILE"
    run_sql "INSERT INTO helpdesk.schema_migrations (filename) VALUES ('$FNAME') ON CONFLICT DO NOTHING;" > /dev/null
    echo "Done: $FNAME"
    exit 0
fi

# --- Apply all pending ---
APPLIED_COUNT=0
SKIPPED_COUNT=0

for FILE in $(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort); do
    FNAME="$(basename "$FILE")"
    APPLIED=$(run_sql "SELECT COUNT(*) FROM helpdesk.schema_migrations WHERE filename='$FNAME';")
    if [ "$APPLIED" = "0" ]; then
        echo "[migrate] Applying $FNAME..."
        run_file "$FILE"
        run_sql "INSERT INTO helpdesk.schema_migrations (filename) VALUES ('$FNAME');" > /dev/null
        echo "[migrate] Applied: $FNAME"
        APPLIED_COUNT=$((APPLIED_COUNT + 1))
    else
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    fi
done

echo "[migrate] Done. Applied: $APPLIED_COUNT, Skipped (already applied): $SKIPPED_COUNT"
