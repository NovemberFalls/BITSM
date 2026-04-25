"""Email notification service: direct Resend API sending + event dispatch."""

import json
import logging
import fnmatch
import threading

import resend

from config import Config
from models.db import fetch_one, fetch_all, insert_returning

logger = logging.getLogger(__name__)

# Default patterns for known ticketing system emails (anti-loop)
DEFAULT_BLOCKLIST = [
    "*@noreply.*",
    "*noreply*",
    "*@freshservice.com",
    "*@freshdesk.com",
    "*@zendesk.com",
    "*@servicedesk.*",
    "*@jira.*",
]


def _is_blocked_email(email: str, tenant_blocklist: list[str] | None = None) -> bool:
    """Check if email matches any blocklist pattern (anti-loop protection)."""
    email_lower = email.lower()
    patterns = DEFAULT_BLOCKLIST + (tenant_blocklist or [])
    for pattern in patterns:
        if fnmatch.fnmatch(email_lower, pattern.lower()):
            logger.info("Blocked email to %s (matched pattern: %s)", email, pattern)
            return True
    return False


def send_email(
    to: str,
    subject: str,
    html_body: str,
    from_email: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
    cc: list[str] | None = None,
    tenant_id: int | None = None,
) -> str | None:
    """Send an email via Resend API. Returns message ID or None on failure.

    When DEMO_MODE is enabled and a tenant_id is supplied, the tenant's BYOK
    Resend key is used instead of the platform key.  This allows demo tenants to
    send email through their own Resend account without touching platform creds.
    """
    api_key = Config.RESEND_API_KEY

    if Config.DEMO_MODE and tenant_id is not None:
        from services.billing_service import get_byok_keys
        byok = get_byok_keys(tenant_id)
        byok_resend = (byok or {}).get("resend")
        if byok_resend:
            api_key = byok_resend

    if not api_key:
        logger.warning("RESEND_API_KEY not configured, skipping email to %s", to)
        return None

    resend.api_key = api_key
    sender = f"{from_name or Config.DEFAULT_FROM_NAME} <{from_email or Config.DEFAULT_FROM_EMAIL}>"

    if Config.DEMO_MODE:
        subject = f"[DEMO] {subject}"

    params: dict = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        params["reply_to"] = reply_to
    if cc:
        params["cc"] = cc

    try:
        result = resend.Emails.send(params)
        msg_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
        logger.info("Sent email to %s (Resend ID: %s)", to, msg_id)
        return msg_id
    except Exception as e:
        logger.error("Resend send failed to %s: %s", to, e)
        return None


def dispatch_ticket_emails(tenant_id: int, ticket_id: int, event: str, comment: dict | None = None):
    """Main dispatcher: resolve preferences → recipients → send via Resend.

    Runs in a background thread to avoid blocking the request.
    """
    def _dispatch():
        try:
            _dispatch_sync(tenant_id, ticket_id, event, comment)
        except Exception as e:
            logger.error("Email dispatch failed for ticket %s event %s: %s", ticket_id, event, e)

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()


def _dispatch_sync(tenant_id: int, ticket_id: int, event: str, comment: dict | None = None):
    """Synchronous dispatch logic."""
    if not Config.RESEND_API_KEY and not Config.DEMO_MODE:
        return

    # 0. Load tenant template override for this event (if any)
    tmpl_row = fetch_one(
        "SELECT subject_template, body_headline, body_intro FROM notification_templates WHERE tenant_id = %s AND event = %s",
        [tenant_id, event],
    )
    tenant_template = dict(tmpl_row) if tmpl_row else None

    # 1. Query notification preferences for this tenant+event where channel='email' and enabled
    prefs = fetch_all(
        """SELECT role_target FROM notification_preferences
           WHERE tenant_id = %s AND event = %s AND channel = 'email' AND enabled = true""",
        [tenant_id, event],
    )
    if not prefs:
        return

    # 2. Load ticket with requester + assignee details
    ticket = fetch_one(
        """SELECT t.*, u_req.email as requester_email, u_req.name as requester_name,
                  u_asg.email as assignee_email, u_asg.name as assignee_name,
                  ten.settings, ten.email_from_address, ten.email_from_name,
                  ten.name as tenant_name, ten.slug as tenant_slug
           FROM tickets t
           LEFT JOIN users u_req ON u_req.id = t.requester_id
           LEFT JOIN users u_asg ON u_asg.id = t.assignee_id
           LEFT JOIN tenants ten ON ten.id = t.tenant_id
           WHERE t.id = %s""",
        [ticket_id],
    )
    if not ticket:
        return

    # Get tenant email blocklist
    settings = ticket.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    tenant_blocklist = settings.get("email_blocklist", [])

    # Tenant-specific from address
    from_email = ticket.get("email_from_address") or None
    from_name = ticket.get("email_from_name") or ticket.get("tenant_name") or None

    # Reply-To: tenant inbound address so customer replies thread back as comments
    tenant_slug = ticket.get("tenant_slug") or ""
    inbound_domain = settings.get("inbound_email_domain") or Config.INBOUND_EMAIL_DOMAIN
    reply_to = f"{tenant_slug}@{inbound_domain}" if tenant_slug and inbound_domain else None

    tenant_app_url = (settings.get("app_url") or Config.APP_URL).rstrip("/")
    agent_ticket_url = f"{tenant_app_url}/tickets/{ticket_id}"
    portal_ticket_url = f"{tenant_app_url}/{tenant_slug}/portal" if tenant_slug else agent_ticket_url
    app_name = settings.get("app_name") or Config.APP_NAME

    # 2b. For status_changed: fetch last 2 public comments as activity history
    recent_comments = None
    if event == "status_changed":
        recent_comments = fetch_all(
            """SELECT content, author_name FROM ticket_comments
               WHERE ticket_id = %s AND is_internal = false
               ORDER BY created_at DESC LIMIT 2""",
            [ticket_id],
        ) or []

    # 3. Resolve recipients per role_target
    from services.email_templates import render_email

    role_targets = {p["role_target"] for p in prefs}
    recipients: list[tuple[str, str, str]] = []  # (email, name, role_target)

    if "requester" in role_targets and ticket.get("requester_email"):
        recipients.append((ticket["requester_email"], ticket.get("requester_name", ""), "requester"))

    if "assignee" in role_targets and ticket.get("assignee_email"):
        recipients.append((ticket["assignee_email"], ticket.get("assignee_name", ""), "assignee"))

    if "all_agents" in role_targets:
        agents = fetch_all(
            """SELECT email, name FROM users
               WHERE tenant_id = %s AND role IN ('super_admin', 'tenant_admin', 'agent') AND is_active = true""",
            [tenant_id],
        )
        seen = {r[0] for r in recipients}
        for a in agents:
            if a["email"] not in seen:
                recipients.append((a["email"], a["name"], "all_agents"))
                seen.add(a["email"])

    if "group" in role_targets:
        # Get user members only from groups subscribed to this event.
        # LEFT JOIN notification_group_events so that groups with NO row (NULL)
        # are treated as enabled=true — backward-compatible default for existing
        # groups that haven't configured per-event subscriptions yet.
        #
        # Wrapped in try/except: if migration 034 hasn't been applied yet, the
        # notification_group_events table won't exist. Rather than killing all
        # email dispatch (requester/assignee/all_agents emails already in
        # recipients would be lost), we log a warning and skip the group block.
        try:
            user_members = fetch_all(
                """SELECT DISTINCT u.email, u.name
                   FROM notification_groups ng
                   JOIN notification_group_members ngm ON ngm.group_id = ng.id
                   JOIN users u ON u.id = ngm.user_id AND u.is_active = true
                   LEFT JOIN notification_group_events nge ON nge.group_id = ng.id
                       AND nge.event = %s AND nge.channel = 'email'
                   WHERE ng.tenant_id = %s
                     AND ngm.user_id IS NOT NULL
                     AND (nge.enabled IS NULL OR nge.enabled = true)""",
                [event, tenant_id],
            )
            # Get external email members with the same per-event filter.
            ext_members = fetch_all(
                """SELECT DISTINCT ngm.email
                   FROM notification_groups ng
                   JOIN notification_group_members ngm ON ngm.group_id = ng.id
                   LEFT JOIN notification_group_events nge ON nge.group_id = ng.id
                       AND nge.event = %s AND nge.channel = 'email'
                   WHERE ng.tenant_id = %s
                     AND ngm.user_id IS NULL
                     AND ngm.email IS NOT NULL
                     AND (nge.enabled IS NULL OR nge.enabled = true)""",
                [event, tenant_id],
            )
            seen = {r[0] for r in recipients}
            for m in user_members:
                if m["email"] not in seen:
                    recipients.append((m["email"], m["name"], "group"))
                    seen.add(m["email"])
            for m in ext_members:
                if m["email"] not in seen:
                    recipients.append((m["email"], m["email"], "group"))
                    seen.add(m["email"])
        except Exception as e:
            logger.warning(
                "Group member query failed for tenant %s event %s (migration 034 not applied?): %s",
                tenant_id, event, e,
            )

        # Team event subscriptions — send to team members who are subscribed
        try:
            team_members = fetch_all(
                """SELECT DISTINCT u.email, u.name
                   FROM teams t
                   JOIN team_members tm ON tm.team_id = t.id
                   JOIN users u ON u.id = tm.user_id AND u.is_active = true
                   LEFT JOIN team_event_subscriptions tes ON tes.team_id = t.id
                       AND tes.event = %s AND tes.channel = 'email'
                   WHERE t.tenant_id = %s AND t.is_active = true
                     AND (tes.enabled IS NULL OR tes.enabled = true)""",
                [event, tenant_id],
            )
            seen = {r[0] for r in recipients}
            for m in team_members:
                if m["email"] not in seen:
                    recipients.append((m["email"], m["name"], "team"))
                    seen.add(m["email"])
        except Exception as e:
            logger.warning(
                "Team member query failed for tenant %s event %s: %s",
                tenant_id, event, e,
            )

    # 4. Send to each recipient
    for email_addr, name, role_target in recipients:
        if _is_blocked_email(email_addr, tenant_blocklist):
            continue

        ticket_url = portal_ticket_url if role_target == "requester" else agent_ticket_url
        rendered = render_email(
            event, ticket, comment,
            extra={
                "app_name": app_name,
                "ticket_url": ticket_url,
                "role_target": role_target,
                "recent_comments": recent_comments,
            },
            template=tenant_template,
        )

        msg_id = send_email(
            to=email_addr,
            subject=rendered["subject"],
            html_body=rendered["html"],
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            tenant_id=tenant_id,
        )

        # 5. Log to notifications table
        status = "sent" if msg_id else "failed"
        try:
            insert_returning(
                """INSERT INTO notifications (tenant_id, ticket_id, channel, recipient, payload, status, sent_at)
                   VALUES (%s, %s, 'email', %s, %s::jsonb, %s, CASE WHEN %s = 'sent' THEN now() ELSE NULL END)
                   RETURNING id""",
                [
                    tenant_id, ticket_id, email_addr,
                    json.dumps({"event": event, "role_target": role_target, "resend_id": msg_id}),
                    status, status,
                ],
            )
        except Exception as e:
            logger.error("Failed to log email notification: %s", e)


# ============================================================
# Invite email dispatch
# ============================================================

def send_invite_email(
    user_id: int,
    user_email: str,
    user_name: str,
    role: str,
    tenant_id: int | None = None,
    expires_at: str | None = None,
    inviter_id: int | None = None,
):
    """Send an invite email to a new user. Runs in a background thread."""
    def _send():
        try:
            _send_invite_sync(user_id, user_email, user_name, role, tenant_id, expires_at, inviter_id)
        except Exception as e:
            logger.error("Invite email failed for %s: %s", user_email, e)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


def _send_invite_sync(
    user_id: int,
    user_email: str,
    user_name: str,
    role: str,
    tenant_id: int | None,
    expires_at: str | None,
    inviter_id: int | None,
):
    """Synchronous invite email logic."""
    if not Config.RESEND_API_KEY and not Config.DEMO_MODE:
        logger.warning("RESEND_API_KEY not configured, skipping invite email to %s", user_email)
        return

    from services.email_templates import render_invite_email

    # Look up tenant name and inviter name
    tenant_name = "the platform"
    from_email = None
    from_name = None
    tenant_settings = {}
    if tenant_id:
        tenant = fetch_one(
            "SELECT name, settings, email_from_address, email_from_name FROM tenants WHERE id = %s",
            [tenant_id],
        )
        if tenant:
            tenant_name = tenant["name"]
            from_email = tenant.get("email_from_address") or None
            from_name = tenant.get("email_from_name") or tenant_name or None
            tenant_settings = tenant.get("settings") or {}
            if isinstance(tenant_settings, str):
                tenant_settings = json.loads(tenant_settings)

    inviter_name = None
    if inviter_id:
        inviter = fetch_one("SELECT name FROM users WHERE id = %s", [inviter_id])
        if inviter:
            inviter_name = inviter["name"]

    tenant_app_url = (tenant_settings.get("app_url") or Config.APP_URL).rstrip("/")
    login_url = f"{tenant_app_url}/login"

    rendered = render_invite_email(
        user_name=user_name or user_email,
        user_email=user_email,
        role=role,
        tenant_name=tenant_name,
        inviter_name=inviter_name,
        expires_at=expires_at,
        app_name=tenant_settings.get("app_name") or Config.APP_NAME,
        login_url=login_url,
    )

    msg_id = send_email(
        to=user_email,
        subject=rendered["subject"],
        html_body=rendered["html"],
        from_email=from_email,
        from_name=from_name,
        tenant_id=tenant_id,
    )

    logger.info(
        "Invite email %s to %s (user_id=%s, tenant=%s, resend_id=%s)",
        "sent" if msg_id else "failed",
        user_email, user_id, tenant_name, msg_id,
    )


# ============================================================
# Password reset email
# ============================================================

def send_password_reset_email(user_id: int, user_email: str, reset_url: str):
    """Send a password reset email. Runs in a background thread."""
    def _send():
        try:
            _send_password_reset_sync(user_id, user_email, reset_url)
        except Exception as e:
            logger.error("Password reset email failed for %s: %s", user_email, e)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


def _send_password_reset_sync(user_id: int, user_email: str, reset_url: str):
    """Synchronous password reset email logic."""
    if not Config.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not configured, skipping password reset email to %s", user_email)
        return

    from services.email_templates import render_password_reset_email

    rendered = render_password_reset_email(
        user_email=user_email,
        reset_url=reset_url,
        app_name=Config.APP_NAME,
    )

    msg_id = send_email(
        to=user_email,
        subject=rendered["subject"],
        html_body=rendered["html"],
    )

    logger.info(
        "Password reset email %s to %s (user_id=%s, resend_id=%s)",
        "sent" if msg_id else "failed",
        user_email, user_id, msg_id,
    )


# ============================================================
# Email verification email
# ============================================================

def send_verification_email(user_id: int, user_email: str, verify_url: str):
    """Send an email verification email. Runs in a background thread."""
    def _send():
        try:
            _send_verification_sync(user_id, user_email, verify_url)
        except Exception as e:
            logger.error("Verification email failed for %s: %s", user_email, e)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


def _send_verification_sync(user_id: int, user_email: str, verify_url: str):
    """Synchronous email verification logic."""
    if not Config.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not configured, skipping verification email to %s", user_email)
        return

    from services.email_templates import render_verification_email

    rendered = render_verification_email(
        user_email=user_email,
        verify_url=verify_url,
        app_name=Config.APP_NAME,
    )

    msg_id = send_email(
        to=user_email,
        subject=rendered["subject"],
        html_body=rendered["html"],
    )

    logger.info(
        "Verification email %s to %s (user_id=%s, resend_id=%s)",
        "sent" if msg_id else "failed",
        user_email, user_id, msg_id,
    )


# ============================================================
# CSAT (Customer Satisfaction) email on ticket resolve
# ============================================================

def send_csat_email(ticket_id: int, tenant_id: int):
    """Send a CSAT survey email to the requester when a ticket is resolved.

    Runs in a background thread to avoid blocking the request.
    Only sends if:
    - Requester has an email address
    - No CSAT survey has already been sent for this ticket
    - Requester is an end_user (not an agent rating themselves)
    """
    def _send():
        try:
            _send_csat_sync(ticket_id, tenant_id)
        except Exception as e:
            logger.error("CSAT email failed for ticket %s: %s", ticket_id, e)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


def _send_csat_sync(ticket_id: int, tenant_id: int):
    """Synchronous CSAT email logic."""
    if not Config.RESEND_API_KEY and not Config.DEMO_MODE:
        logger.warning("RESEND_API_KEY not configured, skipping CSAT email for ticket %s", ticket_id)
        return

    import secrets

    # Check if CSAT already sent for this ticket
    existing = fetch_one(
        "SELECT id FROM csat_surveys WHERE ticket_id = %s",
        [ticket_id],
    )
    if existing:
        logger.info("CSAT already sent for ticket %s, skipping", ticket_id)
        return

    # Load ticket + requester
    ticket = fetch_one(
        """SELECT t.ticket_number, t.subject,
                  u.id as requester_id, u.email as requester_email, u.name as requester_name, u.role as requester_role,
                  ten.settings, ten.email_from_address, ten.email_from_name,
                  ten.name as tenant_name, ten.slug as tenant_slug
           FROM tickets t
           LEFT JOIN users u ON u.id = t.requester_id
           LEFT JOIN tenants ten ON ten.id = t.tenant_id
           WHERE t.id = %s""",
        [ticket_id],
    )
    if not ticket:
        return

    requester_email = ticket.get("requester_email")
    if not requester_email:
        logger.info("CSAT skipped for ticket %s: requester has no email", ticket_id)
        return

    # Only send CSAT to end_users (not agents/admins resolving their own tickets)
    requester_role = ticket.get("requester_role", "")
    if requester_role in ("super_admin", "tenant_admin", "agent"):
        logger.info("CSAT skipped for ticket %s: requester is %s", ticket_id, requester_role)
        return

    # Generate unique survey token
    token = secrets.token_urlsafe(32)

    # Determine app URL
    settings = ticket.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    app_url = (settings.get("app_url") or Config.APP_URL).rstrip("/")
    tenant_slug = ticket.get("tenant_slug") or ""
    survey_url = f"{app_url}/api/webhooks/csat/{token}"

    # Insert survey record
    insert_returning(
        """INSERT INTO csat_surveys (tenant_id, ticket_id, requester_id, token, email_sent_at)
           VALUES (%s, %s, %s, %s, now())
           RETURNING id""",
        [tenant_id, ticket_id, ticket.get("requester_id"), token],
    )

    from services.email_templates import render_csat_email

    rendered = render_csat_email(
        ticket_number=ticket.get("ticket_number", ""),
        ticket_subject=ticket.get("subject", ""),
        requester_name=ticket.get("requester_name", ""),
        survey_url=survey_url,
        app_name=settings.get("app_name") or Config.APP_NAME,
    )

    from_email = ticket.get("email_from_address") or None
    from_name = ticket.get("email_from_name") or ticket.get("tenant_name") or None

    msg_id = send_email(
        to=requester_email,
        subject=rendered["subject"],
        html_body=rendered["html"],
        from_email=from_email,
        from_name=from_name,
        tenant_id=tenant_id,
    )

    logger.info(
        "CSAT email %s to %s for ticket %s (resend_id=%s)",
        "sent" if msg_id else "failed",
        requester_email, ticket_id, msg_id,
    )
