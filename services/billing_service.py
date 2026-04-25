"""Billing service: API cost tracking, monthly cap enforcement, and BYOK key management.

Tracks per-tenant monthly API spend in `api_usage_monthly` and enforces
per-user cost caps based on the tenant's plan tier.  BYOK (bring-your-own-key)
decryption is handled here for enterprise-tier tenants.

All DB-hitting functions (except check_ai_gate) silently swallow exceptions
so billing telemetry never breaks production workloads.
"""

import logging
import threading
from datetime import date, timedelta

from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost table — USD per token (NOT per 1M tokens)
# ---------------------------------------------------------------------------
COSTS = {
    "claude-haiku-4-5":            {"input": 0.80 / 1_000_000, "output": 4.00 / 1_000_000},
    "claude-haiku-4-5-20251001":   {"input": 0.80 / 1_000_000, "output": 4.00 / 1_000_000},
    "claude-sonnet-4-20250514":    {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "voyage-3":                    {"input": 0.06 / 1_000_000, "output": 0},
    "voyage-3-lite":               {"input": 0.02 / 1_000_000, "output": 0},
    "text-embedding-3-small":      {"input": 0.02 / 1_000_000, "output": 0},
}

# ---------------------------------------------------------------------------
# Monthly cap per user per tier (USD). None = unlimited (enterprise/BYOK).
#
# Canonical pricing (locked 2026-03-24):
#   Free:            $0     — AI-blocked; landing zone after trial expires
#   Trial:           $15    — Starter-level access; converts or drops to Free
#   Starter:         $50/seat, $15 API/user
#   Pro:             $100/seat, $30 API/user
#   Business:        $150/seat, $45 API/user
#   Enterprise BYOK: $100/seat flat — tenant supplies own keys, zero AI COGS
# ---------------------------------------------------------------------------
CAP_PER_USER = {
    "free":       0.00,
    "demo":       0.00,  # Defense-in-depth: cap catches any demo tenant that bypasses the BYOK gate
    "trial":     15.00,
    "starter":   15.00,
    "pro":       30.00,
    "business":  45.00,
    "enterprise": None,  # unlimited — BYOK at $100/seat flat, zero AI COGS
}

VALID_TIERS = ("free", "trial", "demo", "starter", "pro", "business", "enterprise")


# ---------------------------------------------------------------------------
# Custom exception — intentionally raised by check_ai_gate()
# ---------------------------------------------------------------------------
class ApiCapError(Exception):
    """Raised when a tenant cannot use AI (free tier or cap exceeded)."""

    def __init__(self, reason: str, tier: str):
        self.reason = reason   # 'ai_not_included' | 'api_cap_reached'
        self.tier = tier
        super().__init__(f"API cap: {reason} (tier={tier})")


# ---------------------------------------------------------------------------
# record_usage — fire-and-forget monthly rollup
# ---------------------------------------------------------------------------
def record_usage(
    tenant_id: int,
    model: str,
    call_type: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cost_override: float | None = None,
) -> None:
    """Upsert into api_usage_monthly.  Runs in a daemon thread — never raises.

    If *cost_override* is provided (USD), it is used directly instead of
    computing cost from token counts × per-token rates.  Useful for
    providers like ElevenLabs where the cost is already known in credits.
    """
    if tenant_id is None:
        return

    def _upsert():
        try:
            from models.db import execute as db_execute

            if cost_override is not None:
                cost = cost_override
            else:
                rates = COSTS.get(model, {"input": 0, "output": 0})
                cost = input_tokens * rates["input"] + output_tokens * rates["output"]

            db_execute(
                """INSERT INTO api_usage_monthly (tenant_id, month, total_cost_usd, call_count, last_updated)
                   VALUES (%s, to_char(now(), 'YYYY-MM'), %s, 1, now())
                   ON CONFLICT (tenant_id, month) DO UPDATE
                       SET total_cost_usd = api_usage_monthly.total_cost_usd + EXCLUDED.total_cost_usd,
                           call_count     = api_usage_monthly.call_count + 1,
                           last_updated   = now()""",
                [tenant_id, cost],
            )
        except Exception as exc:
            logger.debug("billing record_usage failed: %s", exc)

    threading.Thread(target=_upsert, daemon=True).start()


# ---------------------------------------------------------------------------
# get_cap_for_tenant — monthly $ budget for this tenant
# ---------------------------------------------------------------------------
def get_cap_for_tenant(tenant_id: int) -> float | None:
    """Return the monthly API budget (USD) for a tenant, or None if unlimited.

    Budget = CAP_PER_USER[tier] * active_user_count.
    Free tier returns 0.0.  Enterprise returns None (unlimited).
    """
    try:
        from models.db import fetch_one

        row = fetch_one(
            """SELECT t.plan_tier,
                      (SELECT count(*) FROM users u
                       WHERE u.tenant_id = t.id AND u.is_active = true
                         AND u.role IN ('agent', 'tenant_admin')) AS user_count
               FROM tenants t
               WHERE t.id = %s""",
            [tenant_id],
        )
        if not row:
            return 0.0

        tier = row.get("plan_tier", "free")
        per_user = CAP_PER_USER.get(tier)

        if per_user is None:
            return None  # enterprise — unlimited

        user_count = max(1, row.get("user_count", 0) or 0)
        return per_user * user_count
    except Exception as exc:
        logger.warning("get_cap_for_tenant(%s) failed: %s", tenant_id, exc)
        return 0.0


# ---------------------------------------------------------------------------
# get_monthly_usage — full usage summary for a tenant + month
# ---------------------------------------------------------------------------
def get_monthly_usage(tenant_id: int, month: str | None = None) -> dict:
    """Return usage summary for a tenant in the given month (default: current).

    Returns dict with keys: total_cost, call_count, cap, pct_used, over_cap, reset_date.
    """
    try:
        from models.db import fetch_one

        if month is None:
            month = date.today().strftime("%Y-%m")

        row = fetch_one(
            "SELECT total_cost_usd, call_count FROM api_usage_monthly WHERE tenant_id = %s AND month = %s",
            [tenant_id, month],
        )

        total_cost = float(row["total_cost_usd"]) if row else 0.0
        call_count = row["call_count"] if row else 0

        cap = get_cap_for_tenant(tenant_id)

        # Reset date = first day of next month
        today = date.today()
        reset_date = (today.replace(day=1) + timedelta(days=32)).replace(day=1)

        if cap is None:
            pct_used = 0.0
            over_cap = False
        elif cap == 0:
            pct_used = 100.0 if total_cost > 0 else 0.0
            over_cap = total_cost > 0
        else:
            pct_used = round((total_cost / cap) * 100, 2)
            over_cap = total_cost >= cap

        return {
            "total_cost": round(total_cost, 4),
            "call_count": call_count,
            "cap": cap,
            "pct_used": pct_used,
            "over_cap": over_cap,
            "reset_date": reset_date.isoformat(),
        }
    except Exception as exc:
        logger.warning("get_monthly_usage(%s, %s) failed: %s", tenant_id, month, exc)
        return {
            "total_cost": 0.0,
            "call_count": 0,
            "cap": 0.0,
            "pct_used": 0.0,
            "over_cap": False,
            "reset_date": (date.today().replace(day=1) + timedelta(days=32)).replace(day=1).isoformat(),
        }


# ---------------------------------------------------------------------------
# is_over_cap — fast boolean check
# ---------------------------------------------------------------------------
def is_over_cap(tenant_id: int) -> bool:
    """Return True if the tenant has exceeded their monthly API cost cap.

    Returns False for enterprise (unlimited) and if the DB query fails
    (fail-open so billing glitches don't block production).
    """
    try:
        from models.db import fetch_one

        cap = get_cap_for_tenant(tenant_id)
        if cap is None:
            return False  # enterprise — unlimited

        month = date.today().strftime("%Y-%m")
        row = fetch_one(
            "SELECT total_cost_usd FROM api_usage_monthly WHERE tenant_id = %s AND month = %s",
            [tenant_id, month],
        )
        spend = float(row["total_cost_usd"]) if row else 0.0
        return spend >= cap
    except Exception as exc:
        logger.warning("is_over_cap(%s) failed: %s — failing open", tenant_id, exc)
        return False


# ---------------------------------------------------------------------------
# check_ai_gate — the ONLY function that intentionally raises
# ---------------------------------------------------------------------------
def check_ai_gate(tenant_id: int) -> None:
    """Raise ApiCapError if the tenant cannot use AI.

    Call this before any AI operation.  Free tier raises 'ai_not_included',
    over-cap raises 'api_cap_reached', enterprise always passes.
    When DEMO_MODE is globally enabled, all tenants must supply BYOK Anthropic
    keys — no platform key fallback.  Missing key raises 'byok_required'.
    """
    from models.db import fetch_one

    row = fetch_one("SELECT plan_tier FROM tenants WHERE id = %s", [tenant_id])
    tier = (row or {}).get("plan_tier", "free")

    if tier == "free":
        raise ApiCapError("ai_not_included", tier)

    if tier == "enterprise":
        return  # unlimited

    if is_over_cap(tenant_id):
        raise ApiCapError("api_cap_reached", tier)

    # In DEMO_MODE every non-enterprise tenant must supply their own Anthropic key.
    if Config.DEMO_MODE:
        byok = get_byok_keys(tenant_id)
        if not (byok and byok.get("anthropic")):
            raise ApiCapError("byok_required", tier)


# ---------------------------------------------------------------------------
# get_byok_keys — decrypt enterprise BYOK keys
# ---------------------------------------------------------------------------
def get_byok_keys(tenant_id: int) -> dict | None:
    """Decrypt and return BYOK API keys for an enterprise tenant.

    Returns dict with keys 'anthropic', 'openai', 'voyage',
    'twilio_account_sid', 'twilio_auth_token', 'twilio_phone_number',
    'elevenlabs' — each is a decrypted string or None if the tenant
    hasn't set that key.
    Returns None entirely if Fernet is not configured or on any error.
    """
    try:
        if not Config.FERNET_KEY:
            logger.warning("FERNET_KEY not set — cannot decrypt BYOK keys")
            return None

        from cryptography.fernet import Fernet
        from models.db import fetch_one

        row = fetch_one(
            """SELECT byok_anthropic_key, byok_openai_key, byok_voyage_key,
                      byok_twilio_account_sid, byok_twilio_auth_token,
                      byok_twilio_phone_number, byok_elevenlabs_api_key,
                      byok_resend_api_key
               FROM tenants WHERE id = %s""",
            [tenant_id],
        )
        if not row:
            return None

        f = Fernet(Config.FERNET_KEY.encode() if isinstance(Config.FERNET_KEY, str) else Config.FERNET_KEY)

        def _decrypt(val):
            if not val:
                return None
            try:
                return f.decrypt(val.encode() if isinstance(val, str) else val).decode()
            except Exception:
                return None

        return {
            "anthropic":           _decrypt(row.get("byok_anthropic_key")),
            "openai":              _decrypt(row.get("byok_openai_key")),
            "voyage":              _decrypt(row.get("byok_voyage_key")),
            "twilio_account_sid":  _decrypt(row.get("byok_twilio_account_sid")),
            "twilio_auth_token":   _decrypt(row.get("byok_twilio_auth_token")),
            "twilio_phone_number": _decrypt(row.get("byok_twilio_phone_number")),
            "elevenlabs":          _decrypt(row.get("byok_elevenlabs_api_key")),
            "resend":              _decrypt(row.get("byok_resend_api_key")),
        }
    except Exception as exc:
        logger.warning("get_byok_keys(%s) failed: %s", tenant_id, exc)
        return None


# ---------------------------------------------------------------------------
# is_demo_tenant — check if a tenant is on the demo plan tier
# ---------------------------------------------------------------------------
def is_demo_tenant(tenant_id: int) -> bool:
    """Check if a tenant is on the demo plan tier."""
    if not tenant_id:
        return False
    try:
        from models.db import fetch_one
        row = fetch_one("SELECT plan_tier FROM tenants WHERE id = %s", [tenant_id])
        return row is not None and row.get("plan_tier") == "demo"
    except Exception:
        return True  # Fail-closed: assume demo if DB check fails


# ---------------------------------------------------------------------------
# set_byok_keys — encrypt and store BYOK keys for a tenant
# ---------------------------------------------------------------------------
def set_byok_keys(tenant_id: int, keys: dict) -> bool:
    """Encrypt and store BYOK API keys for a tenant.

    Takes a dict with optional keys: 'anthropic', 'openai', 'voyage',
    'twilio_account_sid', 'twilio_auth_token', 'twilio_phone_number', 'elevenlabs'.
    Only updates columns for keys present in the dict.
    Empty string value means clear the key (set column to NULL).
    Returns True on success, False on error.

    Keys are NEVER logged in plaintext — only tenant_id and outcome are logged.
    """
    if not Config.FERNET_KEY:
        logger.warning("set_byok_keys(%s): FERNET_KEY not set — cannot encrypt BYOK keys", tenant_id)
        return False

    # Only handle the known providers — ignore any extra keys silently
    known = {
        "anthropic":           "byok_anthropic_key",
        "openai":              "byok_openai_key",
        "voyage":              "byok_voyage_key",
        "twilio_account_sid":  "byok_twilio_account_sid",
        "twilio_auth_token":   "byok_twilio_auth_token",
        "twilio_phone_number": "byok_twilio_phone_number",
        "elevenlabs":          "byok_elevenlabs_api_key",
        "resend":              "byok_resend_api_key",
    }
    providers_in_request = {k: v for k, v in keys.items() if k in known}
    if not providers_in_request:
        return True  # nothing to do

    try:
        from cryptography.fernet import Fernet
        from models.db import execute as db_execute

        f = Fernet(Config.FERNET_KEY.encode() if isinstance(Config.FERNET_KEY, str) else Config.FERNET_KEY)

        set_clauses = []
        params = []
        for provider, plaintext in providers_in_request.items():
            col = known[provider]
            if plaintext == "":
                # Clear the key — set column to NULL
                set_clauses.append(f"{col} = NULL")
            else:
                ciphertext = f.encrypt(plaintext.encode()).decode()
                set_clauses.append(f"{col} = %s")
                params.append(ciphertext)

        params.append(tenant_id)
        sql = f"UPDATE tenants SET {', '.join(set_clauses)} WHERE id = %s"
        db_execute(sql, params)
        logger.info("set_byok_keys(%s): updated providers=%s", tenant_id, list(providers_in_request.keys()))
        return True
    except Exception as exc:
        logger.warning("set_byok_keys(%s) failed: %s", tenant_id, exc)
        return False


# ---------------------------------------------------------------------------
# validate_byok_key — live validation of a BYOK key against its provider
# ---------------------------------------------------------------------------
def validate_byok_key(provider: str, key: str, extra: dict | None = None) -> tuple[bool, str]:
    """Test a BYOK API key against the live provider API.

    Returns (True, "OK") on success, (False, "error message") on failure.
    Calls are kept minimal (cheapest model/input) to avoid unnecessary spend.
    The key is NEVER logged.

    For 'twilio_auth_token', pass extra={"twilio_account_sid": "<sid>"} so the
    pair can be validated together.  'twilio_account_sid' and 'twilio_phone_number'
    are validated implicitly through the auth_token check and return (True, "OK").
    """
    if provider == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "test"}],
            )
            return True, "OK"
        except Exception as exc:
            return False, str(exc)

    elif provider == "voyage":
        try:
            import requests
            resp = requests.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"input": ["test"], "model": "voyage-3"},
                timeout=15,
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            return False, str(exc)

    elif provider == "openai":
        try:
            import openai
            client = openai.OpenAI(api_key=key)
            client.embeddings.create(
                model="text-embedding-3-small",
                input=["test"],
                dimensions=256,
            )
            return True, "OK"
        except Exception as exc:
            return False, str(exc)

    elif provider == "elevenlabs":
        try:
            import requests
            resp = requests.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": key},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"ElevenLabs API returned {resp.status_code}"
        except Exception as exc:
            return False, str(exc)

    elif provider == "twilio_auth_token":
        # Requires the account SID to validate the credential pair
        sid = (extra or {}).get("twilio_account_sid")
        if not sid:
            return False, "twilio_account_sid required to validate auth_token"
        try:
            from twilio.rest import Client
            from twilio.base.exceptions import TwilioRestException
            client = Client(sid, key)
            client.api.accounts(sid).fetch()
            return True, "OK"
        except TwilioRestException as exc:
            return False, str(exc)
        except Exception as exc:
            return False, str(exc)

    elif provider in ("twilio_account_sid", "twilio_phone_number"):
        # Validated indirectly via the twilio_auth_token check
        return True, "OK"

    elif provider == "resend":
        try:
            import requests
            resp = requests.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"Resend API returned {resp.status_code}"
        except Exception as exc:
            return False, str(exc)

    else:
        return False, f"Unknown provider: {provider}"
