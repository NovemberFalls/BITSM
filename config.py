"""Centralized configuration from environment variables."""

import os


class Config:
    # --- Flask ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB upload limit (was enforced by Caddy; now app-level)
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours
    IDLE_TIMEOUT_MINUTES: int = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "60"))
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # --- Redis (session store) ---
    REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

    # --- PostgreSQL (helpdesk database) ---
    PG_HOST = os.environ.get("HELPDESK_PG_HOST", "localhost")
    PG_PORT = int(os.environ.get("HELPDESK_PG_PORT", 5432))
    PG_DATABASE = os.environ.get("HELPDESK_PG_DATABASE", "helpdesk")
    PG_USER = os.environ.get("HELPDESK_PG_USER", "postgres")
    PG_PASSWORD = os.environ.get("HELPDESK_PG_PASSWORD", "")
    PG_POOL_MIN = int(os.environ.get("HELPDESK_PG_POOL_MIN", 0))
    PG_POOL_MAX = int(os.environ.get("HELPDESK_PG_POOL_MAX", 10))

    # --- Auth: Microsoft 365 (MSAL) ---
    AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_AUTHORITY = os.environ.get(
        "AZURE_AUTHORITY",
        f"https://login.microsoftonline.com/{os.environ.get('AZURE_TENANT_ID', 'common')}",
    )
    AZURE_REDIRECT_PATH = "/auth/callback/microsoft"

    # --- Auth: Google OAuth ---
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_PATH = "/auth/callback/google"

    # --- Auth: General ---
    AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
    ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "")  # Optional domain restriction

    # Super admin emails (comma-separated, case-insensitive)
    SUPER_ADMIN_EMAILS = {
        e.strip().lower()
        for e in os.environ.get("SUPER_ADMIN_EMAILS", "").split(",")
        if e.strip()
    }

    # Super admin domains — any user from these domains gets super_admin on first login
    # Example: SUPER_ADMIN_DOMAINS=your-domain.com
    SUPER_ADMIN_DOMAINS = {
        d.strip().lower()
        for d in os.environ.get("SUPER_ADMIN_DOMAINS", "").split(",")
        if d.strip()
    }

    # Personal email domains (users from these get end_user with no tenant)
    PERSONAL_EMAIL_DOMAINS = {
        "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
        "live.com", "msn.com", "aol.com", "protonmail.com", "proton.me",
        "me.com", "mac.com", "mail.com", "zoho.com", "yandex.com",
    }

    # --- Encryption ---
    FERNET_KEY = os.environ.get("FERNET_KEY", "")  # For encrypting connector configs

    # --- Anthropic (Claude API) ---
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    AI_MODEL_CHAT = os.environ.get("AI_MODEL_CHAT", "claude-haiku-4-5-20251001")      # L1 chat (Haiku — fast, cheap)
    AI_MODEL_CHAT_L2 = os.environ.get("AI_MODEL_CHAT_L2", "claude-sonnet-4-20250514")  # L2 escalation (Sonnet — deep analysis)
    AI_MODEL_ROUTER = os.environ.get("AI_MODEL_ROUTER", "claude-haiku-4-5-20251001")   # Pipeline tasks (tagging, enrich, triage)

    # --- AI Failover ---
    AI_FALLBACK_PROVIDER = os.environ.get("AI_FALLBACK_PROVIDER", "openai")

    # --- Embeddings ---
    EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "voyage")  # 'voyage' or 'openai'
    VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "voyage-3")
    EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", 1024))

    # --- Resend (Email) ---
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "helpdesk@bitsm.io")
    DEFAULT_FROM_NAME = os.environ.get("DEFAULT_FROM_NAME", "BITSM")

    # --- Inbound Email (Cloudflare Email Worker → webhook) ---
    INBOUND_EMAIL_SECRET = os.environ.get("INBOUND_EMAIL_SECRET", "")
    INBOUND_EMAIL_DOMAIN = os.environ.get("INBOUND_EMAIL_DOMAIN", "bitsm.io")

    # --- Stripe ---
    STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_STARTER   = os.environ.get("STRIPE_PRICE_STARTER", "")
    STRIPE_PRICE_PRO       = os.environ.get("STRIPE_PRICE_PRO", "")
    STRIPE_PRICE_BUSINESS  = os.environ.get("STRIPE_PRICE_BUSINESS", "")
    STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")

    # --- Platform Phone (shared pool — one EL account + Twilio account for all platform-tier tenants) ---
    PLATFORM_ELEVENLABS_API_KEY = os.environ.get("PLATFORM_ELEVENLABS_API_KEY", "")
    PLATFORM_TWILIO_ACCOUNT_SID = os.environ.get("PLATFORM_TWILIO_ACCOUNT_SID", "")
    PLATFORM_TWILIO_AUTH_TOKEN  = os.environ.get("PLATFORM_TWILIO_AUTH_TOKEN", "")

    # --- Phone Helpdesk (legacy dev vars — kept for local testing) ---
    DEV_TWILIO_ACCOUNT_SID  = os.environ.get("DEV_TWILIO_ACCOUNT_SID", "")
    DEV_TWILIO_AUTH_TOKEN   = os.environ.get("DEV_TWILIO_AUTH_TOKEN", "")
    DEV_TWILIO_PHONE_NUMBER = os.environ.get("DEV_TWILIO_PHONE_NUMBER", "")  # E.164, e.g. +14155552671
    DEV_ELEVENLABS_API_KEY  = os.environ.get("DEV_ELEVENLABS_API_KEY", "")
    DEV_ONCALL_NUMBER       = os.environ.get("DEV_ONCALL_NUMBER", "")        # Your mobile for testing

    # --- Rate Limiting ---
    RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "120 per minute")  # SOC 2 CC6.8 — global API rate limit

    # --- Feature Gates ---
    TENANT_CREATION_ENABLED = os.environ.get("TENANT_CREATION_ENABLED", "true").lower() == "true"
    DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
    DEMO_TENANT_TTL_DAYS = int(os.environ.get("DEMO_TENANT_TTL_DAYS", "7"))

    # --- App ---
    APP_NAME = os.environ.get("APP_NAME", "BITSM")
    APP_URL = os.environ.get("APP_URL", "https://bitsm.io")
    LEGAL_COMPANY_NAME = os.environ.get("LEGAL_COMPANY_NAME", "Boord Information Technology Services, LLC")
    LEGAL_CONTACT_EMAIL = os.environ.get("LEGAL_CONTACT_EMAIL", "leonard@boord-it.com")
    WEBHOOK_HMAC_KEY = os.environ.get("WEBHOOK_HMAC_KEY", "")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING")
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
    DOCUMENTS_DIR = os.path.join(os.path.dirname(__file__), "documents")
