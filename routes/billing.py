"""Billing blueprint: Stripe Checkout, webhook handler, usage API."""

import logging

import stripe
from flask import Blueprint, jsonify, request, session

from app import limiter
from config import Config
from models.db import execute as db_execute, fetch_one
from routes.auth import login_required, require_role
from services.billing_service import CAP_PER_USER, get_byok_keys, get_monthly_usage, set_byok_keys, validate_byok_key

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__)

stripe.api_key = Config.STRIPE_SECRET_KEY

# ---------------------------------------------------------------------------
# Tier → Stripe price ID mapping (set via env vars after creating products)
# ---------------------------------------------------------------------------
TIER_PRICE_IDS = {
    "starter":    Config.STRIPE_PRICE_STARTER,
    "pro":        Config.STRIPE_PRICE_PRO,
    "business":   Config.STRIPE_PRICE_BUSINESS,
    "enterprise": Config.STRIPE_PRICE_ENTERPRISE,
}

# Stripe price ID → internal tier name
PRICE_TO_TIER = {v: k for k, v in TIER_PRICE_IDS.items() if v}


# ---------------------------------------------------------------------------
# GET /api/billing/usage  — current month usage + plan info for the session tenant
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/usage")
@login_required
@limiter.limit("60 per minute")
def get_usage():
    user = session.get("user", {})
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "no tenant"}), 400

    tenant = fetch_one(
        "SELECT plan_tier, plan_expires_at, stripe_subscription_id FROM tenants WHERE id = %s",
        [tenant_id],
    )
    if not tenant:
        return jsonify({"error": "tenant not found"}), 404

    usage = get_monthly_usage(tenant_id)
    tier = tenant.get("plan_tier", "free")

    return jsonify({
        "tier": tier,
        "cap_per_user": CAP_PER_USER.get(tier),
        "plan_expires_at": tenant["plan_expires_at"].isoformat() if tenant.get("plan_expires_at") else None,
        "has_subscription": bool(tenant.get("stripe_subscription_id")),
        **usage,
    })


# ---------------------------------------------------------------------------
# POST /api/billing/checkout  — create Stripe Checkout session
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/checkout", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def create_checkout():
    user = session.get("user", {})
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "no tenant"}), 400

    body = request.get_json() or {}
    tier = body.get("tier")
    if tier not in TIER_PRICE_IDS or not TIER_PRICE_IDS.get(tier):
        return jsonify({"error": "invalid tier"}), 400

    tenant = fetch_one(
        "SELECT name, slug, stripe_customer_id FROM tenants WHERE id = %s", [tenant_id]
    )
    if not tenant:
        return jsonify({"error": "tenant not found"}), 404

    # End users are free seats — only count paid roles (agent + tenant_admin)
    user_count = (fetch_one(
        "SELECT count(*) AS cnt FROM users WHERE tenant_id = %s AND is_active = true AND role IN ('agent', 'tenant_admin')",
        [tenant_id],
    ) or {}).get("cnt", 1)
    user_count = max(int(user_count), 1)  # minimum 1 to avoid 0-quantity checkout

    try:
        customer_id = tenant.get("stripe_customer_id")

        # Create Stripe customer if we don't have one yet
        if not customer_id:
            customer = stripe.Customer.create(
                name=tenant["name"],
                email=user.get("email", ""),
                metadata={"tenant_id": str(tenant_id)},
            )
            customer_id = customer.id
            db_execute(
                "UPDATE tenants SET stripe_customer_id = %s WHERE id = %s",
                [customer_id, tenant_id],
            )

        slug = tenant.get("slug", "")
        base = f"{Config.APP_URL}/{slug}" if slug else Config.APP_URL
        checkout = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{
                "price": TIER_PRICE_IDS[tier],
                "quantity": int(user_count),
            }],
            success_url=f"{base}/admin/billing?billing=success",
            cancel_url=f"{base}/admin/billing?billing=cancelled",
            metadata={"tenant_id": str(tenant_id), "tier": tier},
            subscription_data={"metadata": {"tenant_id": str(tenant_id), "tier": tier}},
        )
        return jsonify({"url": checkout.url})
    except stripe.StripeError as e:
        logger.error("Stripe checkout error tenant=%s: %s", tenant_id, e)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/billing/portal  — Stripe customer portal (manage/cancel)
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/portal", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def billing_portal():
    user = session.get("user", {})
    tenant_id = user.get("tenant_id")

    tenant = fetch_one("SELECT slug, stripe_customer_id FROM tenants WHERE id = %s", [tenant_id])
    customer_id = (tenant or {}).get("stripe_customer_id")
    if not customer_id:
        return jsonify({"error": "no billing account"}), 400

    try:
        slug = (tenant or {}).get("slug", "")
        base = f"{Config.APP_URL}/{slug}" if slug else Config.APP_URL
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{base}/admin/billing",
        )
        return jsonify({"url": portal.url})
    except stripe.StripeError as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/billing/webhook  — Stripe webhook (signature verified)
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, Config.STRIPE_WEBHOOK_SECRET
        )
    except stripe.errors.SignatureVerificationError:
        logger.warning("Stripe webhook: invalid signature")
        return jsonify({"error": "invalid signature"}), 400

    event_type = event["type"]
    # Stripe SDK v5+ returns StripeObject (not dict subclass) — convert so handlers can use .get()
    data = event["data"]["object"].to_dict()

    if event_type == "customer.subscription.created":
        _handle_subscription_created(data)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)
    elif event_type == "invoice.payment_succeeded":
        _handle_payment_succeeded(data)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------
def _get_tenant_id_from_subscription(sub) -> int | None:
    """Extract our tenant_id from subscription metadata or customer lookup."""
    meta_tid = (sub.get("metadata") or {}).get("tenant_id")
    if meta_tid:
        return int(meta_tid)

    customer_id = sub.get("customer")
    if customer_id:
        row = fetch_one("SELECT id FROM tenants WHERE stripe_customer_id = %s", [customer_id])
        if row:
            return row["id"]
    return None


def _tier_from_subscription(sub) -> str:
    """Map the subscription's price ID back to an internal tier name."""
    items = (sub.get("items") or {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id")
        return PRICE_TO_TIER.get(price_id, "starter")
    return "starter"


def _handle_subscription_created(sub):
    tenant_id = _get_tenant_id_from_subscription(sub)
    if not tenant_id:
        return
    tier = _tier_from_subscription(sub)
    db_execute(
        "UPDATE tenants SET plan_tier = %s, stripe_subscription_id = %s, plan_expires_at = NULL WHERE id = %s",
        [tier, sub["id"], tenant_id],
    )
    logger.info("Billing: tenant %s subscribed → %s", tenant_id, tier)


def _handle_subscription_updated(sub):
    tenant_id = _get_tenant_id_from_subscription(sub)
    if not tenant_id:
        return
    status = sub.get("status")
    if status in ("active", "trialing"):
        tier = _tier_from_subscription(sub)
        db_execute(
            "UPDATE tenants SET plan_tier = %s, stripe_subscription_id = %s, plan_expires_at = NULL WHERE id = %s",
            [tier, sub["id"], tenant_id],
        )
        logger.info("Billing: tenant %s updated → %s", tenant_id, tier)
    elif status in ("canceled", "unpaid", "past_due"):
        db_execute(
            "UPDATE tenants SET plan_tier = 'free', stripe_subscription_id = NULL WHERE id = %s",
            [tenant_id],
        )
        logger.info("Billing: tenant %s downgraded to free (status=%s)", tenant_id, status)


def _handle_subscription_deleted(sub):
    tenant_id = _get_tenant_id_from_subscription(sub)
    if not tenant_id:
        return
    db_execute(
        "UPDATE tenants SET plan_tier = 'free', stripe_subscription_id = NULL WHERE id = %s",
        [tenant_id],
    )
    logger.info("Billing: tenant %s subscription cancelled → free", tenant_id)


def _handle_payment_succeeded(invoice):
    """Process a successful subscription payment.

    1. Resolve tenant from customer ID
    2. Confirm plan_tier is not stuck on free/trial (repair if needed)
    3. Update plan_expires_at from invoice period end
    4. Log payment details for audit
    """
    customer_id = invoice.get("customer")
    if not customer_id:
        logger.warning("Billing: payment_succeeded event missing customer ID")
        return

    row = fetch_one("SELECT id, plan_tier, stripe_subscription_id FROM tenants WHERE stripe_customer_id = %s", [customer_id])
    if not row:
        logger.warning("Billing: payment_succeeded — no tenant for customer %s", customer_id)
        return

    tenant_id = row["id"]
    current_tier = row.get("plan_tier", "free")
    amount = invoice.get("amount_paid", 0)  # in cents
    currency = invoice.get("currency", "usd")
    invoice_id = invoice.get("id", "unknown")
    subscription_id = invoice.get("subscription")

    # If plan_tier is stuck on free/trial but we just got a paid invoice,
    # resolve the correct tier from the subscription and repair it.
    if current_tier in ("free", "trial") and subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            tier = _tier_from_subscription(sub)
            db_execute(
                "UPDATE tenants SET plan_tier = %s, stripe_subscription_id = %s WHERE id = %s",
                [tier, subscription_id, tenant_id],
            )
            logger.info(
                "Billing: tenant %s plan repaired %s → %s (invoice %s)",
                tenant_id, current_tier, tier, invoice_id,
            )
        except stripe.StripeError as e:
            logger.error("Billing: failed to retrieve subscription %s for tier repair: %s", subscription_id, e)

    # Update plan_expires_at from the invoice's billing period end
    period_end = None
    lines = (invoice.get("lines") or {}).get("data", [])
    if lines:
        period_end = lines[0].get("period", {}).get("end")

    if period_end:
        db_execute(
            "UPDATE tenants SET plan_expires_at = to_timestamp(%s) WHERE id = %s",
            [period_end, tenant_id],
        )

    logger.info(
        "Billing: payment succeeded tenant=%s amount=%s %s invoice=%s period_end=%s",
        tenant_id, amount, currency, invoice_id, period_end,
    )


def _handle_payment_failed(invoice):
    """Log a failed payment. No immediate downgrade — Stripe will transition the
    subscription to past_due/unpaid/canceled, which triggers _handle_subscription_updated
    to handle the tier change. This avoids premature downgrades during retry windows.
    """
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    row = fetch_one("SELECT id, plan_tier FROM tenants WHERE stripe_customer_id = %s", [customer_id])
    if not row:
        logger.warning("Billing: payment FAILED — no tenant for customer %s", customer_id)
        return

    tenant_id = row["id"]
    amount = invoice.get("amount_due", 0)
    invoice_id = invoice.get("id", "unknown")
    attempt_count = invoice.get("attempt_count", 0)

    logger.warning(
        "Billing: payment FAILED tenant=%s amount=%s invoice=%s attempt=%s "
        "(no immediate downgrade — waiting for Stripe subscription status change)",
        tenant_id, amount, invoice_id, attempt_count,
    )


# ---------------------------------------------------------------------------
# GET /api/billing/byok  — return masked BYOK key status for the session tenant
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/byok")
@login_required
@limiter.limit("30 per minute")
def get_byok():
    tenant_id = session.get("user", {}).get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "no tenant"}), 400

    keys = get_byok_keys(tenant_id)
    if keys is None:
        # Fernet not configured or DB error — return all null rather than 500
        return jsonify({
            "anthropic": None, "openai": None, "voyage": None,
            "twilio_account_sid": None, "twilio_auth_token": None,
            "twilio_phone_number": None, "elevenlabs": None,
            "resend": None,
        })

    def _mask(val):
        """Return last 4 chars prefixed with ****, or None if not set."""
        if not val:
            return None
        return f"****{val[-4:]}"

    return jsonify({
        "anthropic":           _mask(keys.get("anthropic")),
        "openai":              _mask(keys.get("openai")),
        "voyage":              _mask(keys.get("voyage")),
        "twilio_account_sid":  _mask(keys.get("twilio_account_sid")),
        "twilio_auth_token":   _mask(keys.get("twilio_auth_token")),
        "twilio_phone_number": _mask(keys.get("twilio_phone_number")),
        "elevenlabs":          _mask(keys.get("elevenlabs")),
        "resend":              _mask(keys.get("resend")),
    })


# ---------------------------------------------------------------------------
# PUT /api/billing/byok  — validate and store new BYOK keys
# ---------------------------------------------------------------------------
@billing_bp.route("/api/billing/byok", methods=["PUT"])
@login_required
@require_role("tenant_admin", "super_admin")
@limiter.limit("10 per minute")
def put_byok():
    tenant_id = session.get("user", {}).get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "no tenant"}), 400

    body = request.get_json() or {}
    known_providers = (
        "anthropic", "openai", "voyage",
        "twilio_account_sid", "twilio_auth_token", "twilio_phone_number", "elevenlabs",
        "resend",
    )

    # Extract only the providers that were actually submitted in the request body
    submitted = {p: body[p] for p in known_providers if p in body}
    if not submitted:
        return jsonify({"error": "no keys provided"}), 400

    # Validate all non-empty keys BEFORE saving any — atomic validation
    validated = {}
    for provider, key_value in submitted.items():
        if key_value == "":
            # Empty string = clear this key — no validation needed
            validated[provider] = True
            continue

        # Twilio auth_token validation requires the account SID as context.
        # If the SID is being submitted in the same request, pass it along so
        # the validator can test the credential pair without a DB round-trip.
        if provider == "twilio_auth_token" and "twilio_account_sid" in submitted:
            ok, message = validate_byok_key(provider, key_value, extra={"twilio_account_sid": submitted["twilio_account_sid"]})
        else:
            ok, message = validate_byok_key(provider, key_value)

        if not ok:
            logger.warning("BYOK validation failed tenant=%s provider=%s", tenant_id, provider)
            return jsonify({"error": f"Invalid {provider} key: {message}"}), 400
        validated[provider] = True

    # All validations passed — persist
    saved = set_byok_keys(tenant_id, submitted)
    if not saved:
        return jsonify({"error": "Failed to save keys — check server configuration"}), 500

    return jsonify({"status": "ok", "validated": validated})
