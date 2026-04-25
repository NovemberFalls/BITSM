#!/usr/bin/env bash
# fetch-secrets.sh — Pull all BITSM secrets from your Azure Key Vault into /run/bitsm/env
#
# Runs as ExecStartPre in the systemd unit that wraps docker-compose on the BITSM host.
# Requires: system-assigned managed identity on the host VM with
#           "Key Vault Secrets User" on the configured Key Vault.
#
# Output: /run/bitsm/env (tmpfs, mode 640, group=deploy)
# Log:    /var/log/bitsm-fetch-secrets.log
#
# Usage:
#   VAULT_NAME=your-keyvault sudo -E /usr/local/bin/bitsm-fetch-secrets.sh
#
# To add a new secret: az keyvault secret set --vault-name "${VAULT_NAME}" --name <kv-name> --value <value>
# then add a fetch line here and re-deploy.
#
# FERNET KEY NOTE: Do not rotate fernet-key without first running the data-migration
# script to re-encrypt all rows in connectors.config_encrypted.

set -euo pipefail

# ---------------------------------------------------------------------------
# Managed Identity auth — must be set before any az commands.
# This process runs as root (ExecStartPre=+) and needs its own az config dir.
# Without this, az CLI cannot authenticate and all secret fetches fail.
# ---------------------------------------------------------------------------

export AZURE_CONFIG_DIR=/root/.azure
az login --identity --allow-no-subscriptions >/dev/null 2>&1 \
    || { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FATAL: az login --identity failed" | tee -a /var/log/bitsm-fetch-secrets.log; exit 1; }

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VAULT_NAME="${VAULT_NAME:-kv-bitsm}"
TMPFS_DIR="/run/bitsm"
OUTPUT_FILE="${TMPFS_DIR}/env"
LOG_FILE="/var/log/bitsm-fetch-secrets.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "${LOG_FILE}"
}

die() {
    log "FATAL: $*"
    exit 1
}

# ---------------------------------------------------------------------------
# Validate az CLI availability
# ---------------------------------------------------------------------------

if ! command -v az &>/dev/null; then
    die "az CLI not found. Install azure-cli on this VM."
fi

log "Starting BITSM secret fetch from vault: ${VAULT_NAME}"

# ---------------------------------------------------------------------------
# Mount tmpfs if not already mounted
# ---------------------------------------------------------------------------

if ! mountpoint -q "${TMPFS_DIR}" 2>/dev/null; then
    log "Mounting tmpfs at ${TMPFS_DIR}"
    mkdir -p "${TMPFS_DIR}"
    mount -t tmpfs -o size=8m,mode=700 tmpfs "${TMPFS_DIR}" \
        || die "Failed to mount tmpfs at ${TMPFS_DIR}"
fi

# Grant the deploy group traverse access to the tmpfs directory so the backup
# cron (runs as deploy) can read the env file. The directory itself stays
# root-owned (750 — world has no access). This must run every invocation, not
# just on first mount, because the mount is persistent across service restarts.
chown root:deploy "${TMPFS_DIR}"
chmod 750 "${TMPFS_DIR}"

# ---------------------------------------------------------------------------
# Helper — fetch a single secret; fails fast on empty result
# ---------------------------------------------------------------------------

fetch() {
    local kv_name="$1"
    local value
    value=$(az keyvault secret show \
        --vault-name "${VAULT_NAME}" \
        --name "${kv_name}" \
        --query value \
        -o tsv 2>/dev/null) || die "Failed to fetch secret '${kv_name}' from ${VAULT_NAME}"

    if [[ -z "${value}" ]]; then
        die "Secret '${kv_name}' returned empty value from ${VAULT_NAME}"
    fi
    printf '%s' "${value}"
}

# ---------------------------------------------------------------------------
# Fetch all secrets
# ---------------------------------------------------------------------------

log "Fetching secrets..."

# Flask session signing key
SECRET_KEY=$(fetch "secret-key")

# Fernet encryption key — connector configs + backup encryption
FERNET_KEY=$(fetch "fernet-key")

# PostgreSQL (helpdesk DB on bitsm-postgres-1, port 5433 localhost)
HELPDESK_PG_PASSWORD=$(fetch "pg-password")

# Auth: Microsoft 365 (MSAL) — OAuth app registration credentials, not infrastructure MI
AZURE_CLIENT_ID=$(fetch "ms365-client-id")
AZURE_CLIENT_SECRET=$(fetch "ms365-client-secret")
AZURE_TENANT_ID=$(fetch "ms365-tenant-id")

# Auth: Google OAuth (BITSM sign-in)
GOOGLE_CLIENT_ID=$(fetch "google-client-id")
GOOGLE_CLIENT_SECRET=$(fetch "google-client-secret")

# Anthropic (Claude API — Atlas triage + tagging)
ANTHROPIC_API_KEY=$(fetch "anthropic-api-key")

# Embeddings
VOYAGE_API_KEY=$(fetch "voyage-api-key")
OPENAI_API_KEY=$(fetch "openai-api-key")

# Resend (transactional email)
RESEND_API_KEY=$(fetch "resend-api-key")

# Inbound email webhook HMAC (Cloudflare Email Worker → BITSM)
INBOUND_EMAIL_SECRET=$(fetch "inbound-email-secret")

# Stripe (BITSM billing — 4 pricing tiers)
STRIPE_SECRET_KEY=$(fetch "stripe-secret-key")
STRIPE_PUBLISHABLE_KEY=$(fetch "stripe-publishable-key")
STRIPE_WEBHOOK_SECRET=$(fetch "stripe-webhook-secret")
STRIPE_PRICE_STARTER=$(fetch "stripe-price-starter")
STRIPE_PRICE_PRO=$(fetch "stripe-price-pro")
STRIPE_PRICE_BUSINESS=$(fetch "stripe-price-business")
STRIPE_PRICE_ENTERPRISE=$(fetch "stripe-price-enterprise")

# ElevenLabs + Twilio — platform pool (shared across all platform-tier tenants)
PLATFORM_ELEVENLABS_API_KEY=$(fetch "platform-elevenlabs-api-key")
PLATFORM_TWILIO_ACCOUNT_SID=$(fetch "platform-twilio-account-sid")
PLATFORM_TWILIO_AUTH_TOKEN=$(fetch "platform-twilio-auth-token")

# ElevenLabs + Twilio — dev/legacy pool (used for testing and single-tenant legacy configs)
DEV_TWILIO_ACCOUNT_SID=$(fetch "dev-twilio-account-sid")
DEV_TWILIO_AUTH_TOKEN=$(fetch "dev-twilio-auth-token")
DEV_TWILIO_PHONE_NUMBER=$(fetch "dev-twilio-phone-number")
DEV_ELEVENLABS_API_KEY=$(fetch "dev-elevenlabs-api-key")
DEV_ONCALL_NUMBER=$(fetch "dev-oncall-number")

# Azure Blob Storage — used by azure_backup.py for encrypted backups
AZURE_STORAGE_CONNECTION_STRING=$(fetch "azure-storage-connection-string")

# Sentry (optional — omit from KV if not used)
SENTRY_DSN=$(az keyvault secret show \
    --vault-name "${VAULT_NAME}" \
    --name "sentry-dsn" \
    --query value -o tsv 2>/dev/null || true)

# SSH key for monitoring access (optional — omit from KV if not used)
BITSM_SSH_KEY=$(az keyvault secret show \
    --vault-name "${VAULT_NAME}" \
    --name "bitsm-ssh-key" \
    --query value -o tsv 2>/dev/null || true)

log "All secrets fetched successfully."

# ---------------------------------------------------------------------------
# Write output file
# ---------------------------------------------------------------------------

# Create the file before writing to ensure correct mode before any content lands
install -m 640 /dev/null "${OUTPUT_FILE}"

cat > "${OUTPUT_FILE}" << EOF
# Generated by bitsm-fetch-secrets.sh at $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# DO NOT EDIT — overwritten on every service start
# Source: ${VAULT_NAME}

# ─── SITE-SPECIFIC CONFIG (customize for your deployment) ────────────
# The values below are for the primary bitsm.io deployment.
# Self-hosters: update APP_URL, DEFAULT_FROM_EMAIL, SUPER_ADMIN_EMAILS/DOMAINS,
# and INBOUND_EMAIL_DOMAIN to match your environment.

# Non-secret config (hardcoded — no KV round-trip needed)
APP_ENV=production
APP_NAME=BITSM
APP_URL=https://bitsm.io
LOG_LEVEL=WARNING
AUTH_ENABLED=true
SESSION_COOKIE_SECURE=true
HELPDESK_PG_HOST=postgres
HELPDESK_PG_PORT=5432
HELPDESK_PG_DATABASE=helpdesk
HELPDESK_PG_USER=helpdesk_app
HELPDESK_PG_POOL_MIN=1
HELPDESK_PG_POOL_MAX=10
REDIS_URL=redis://redis:6379/0
EMBEDDING_PROVIDER=voyage
EMBEDDING_MODEL=voyage-3
EMBEDDING_DIMENSIONS=1024
DEFAULT_FROM_EMAIL=helpdesk@bitsm.io
DEFAULT_FROM_NAME=BITSM
INBOUND_EMAIL_DOMAIN=bitsm.io
SUPER_ADMIN_EMAILS=admin@your-domain.com
SUPER_ADMIN_DOMAINS=your-domain.com
AZURE_AUTHORITY=https://login.microsoftonline.com/common
ALLOWED_DOMAIN=
DEMO_MODE=true
DEMO_TENANT_TTL_DAYS=7
TENANT_CREATION_ENABLED=true
RATE_LIMIT_DEFAULT=120 per minute
QUEUE_MAX_LLM_CONCURRENCY=5
QUEUE_POLL_INTERVAL=2.0
IDLE_TIMEOUT_MINUTES=60

# Secrets from ${VAULT_NAME}
SECRET_KEY=${SECRET_KEY}
# fernet-key: see FERNET KEY NOTE above before rotating
FERNET_KEY=${FERNET_KEY}
HELPDESK_PG_PASSWORD=${HELPDESK_PG_PASSWORD}
AZURE_CLIENT_ID=${AZURE_CLIENT_ID}
AZURE_CLIENT_SECRET=${AZURE_CLIENT_SECRET}
AZURE_TENANT_ID=${AZURE_TENANT_ID}
GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
VOYAGE_API_KEY=${VOYAGE_API_KEY}
OPENAI_API_KEY=${OPENAI_API_KEY}
RESEND_API_KEY=${RESEND_API_KEY}
INBOUND_EMAIL_SECRET=${INBOUND_EMAIL_SECRET}
STRIPE_SECRET_KEY=${STRIPE_SECRET_KEY}
STRIPE_PUBLISHABLE_KEY=${STRIPE_PUBLISHABLE_KEY}
STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET}
STRIPE_PRICE_STARTER=${STRIPE_PRICE_STARTER}
STRIPE_PRICE_PRO=${STRIPE_PRICE_PRO}
STRIPE_PRICE_BUSINESS=${STRIPE_PRICE_BUSINESS}
STRIPE_PRICE_ENTERPRISE=${STRIPE_PRICE_ENTERPRISE}
PLATFORM_ELEVENLABS_API_KEY=${PLATFORM_ELEVENLABS_API_KEY}
PLATFORM_TWILIO_ACCOUNT_SID=${PLATFORM_TWILIO_ACCOUNT_SID}
PLATFORM_TWILIO_AUTH_TOKEN=${PLATFORM_TWILIO_AUTH_TOKEN}
DEV_TWILIO_ACCOUNT_SID=${DEV_TWILIO_ACCOUNT_SID}
DEV_TWILIO_AUTH_TOKEN=${DEV_TWILIO_AUTH_TOKEN}
DEV_TWILIO_PHONE_NUMBER=${DEV_TWILIO_PHONE_NUMBER}
DEV_ELEVENLABS_API_KEY=${DEV_ELEVENLABS_API_KEY}
DEV_ONCALL_NUMBER=${DEV_ONCALL_NUMBER}
AZURE_STORAGE_CONNECTION_STRING=${AZURE_STORAGE_CONNECTION_STRING}
EOF

# Append optional secrets only if non-empty
if [[ -n "${SENTRY_DSN:-}" ]]; then
    echo "SENTRY_DSN=${SENTRY_DSN}" >> "${OUTPUT_FILE}"
fi
if [[ -n "${BITSM_SSH_KEY:-}" ]]; then
    echo "BITSM_SSH_KEY=${BITSM_SSH_KEY}" >> "${OUTPUT_FILE}"
fi

# Grant deploy group read access so the backup cron (runs as deploy) can read secrets
chown root:deploy "${OUTPUT_FILE}"

# Verify mode is correct
chmod 640 "${OUTPUT_FILE}"

log "Wrote ${OUTPUT_FILE} (mode 640, group=deploy, tmpfs)"
log "Secret fetch complete."
