"""HTML email templates for ticket lifecycle notifications and user invites."""

# ============================================================
# Default templates — subject, headline, intro paragraph.
# Tenant-specific overrides are stored in notification_templates
# and loaded by email_service._dispatch_sync before calling render_email().
# Variables use {{name}} syntax and are substituted by _apply_vars().
# ============================================================

TEMPLATE_DEFAULTS: dict[str, dict[str, str]] = {
    "ticket_created": {
        "subject_template": "[{{ticket_number}}] Ticket received: {{subject}}",
        "body_headline":    "We've Received Your Request",
        "body_intro":       "Our team will look into it shortly.",
    },
    "ticket_assigned": {
        "subject_template": "[{{ticket_number}}] Ticket assigned to you: {{subject}}",
        "body_headline":    "Ticket Assigned to You",
        "body_intro":       "You've been assigned to handle this ticket.",
    },
    "ticket_resolved": {
        "subject_template": "[{{ticket_number}}] Resolved: {{subject}}",
        "body_headline":    "Your Ticket Has Been Resolved",
        "body_intro":       "If this doesn't fully address your issue, you can reply to reopen.",
    },
    "ticket_closed": {
        "subject_template": "[{{ticket_number}}] Closed: {{subject}}",
        "body_headline":    "Your Ticket Has Been Closed",
        "body_intro":       "This ticket has been closed. If you need further help, please open a new ticket.",
    },
    "agent_reply": {
        "subject_template": "Re: [{{ticket_number}}] {{subject}}",
        "body_headline":    "New Reply on Your Ticket",
        "body_intro":       "{{author_name}} replied:",
    },
    "requester_reply": {
        "subject_template": "Re: [{{ticket_number}}] {{subject}}",
        "body_headline":    "Requester Replied",
        "body_intro":       "{{author_name}} replied to this ticket:",
    },
    "sla_warning": {
        "subject_template": "[{{ticket_number}}] SLA Warning: {{subject}}",
        "body_headline":    "SLA Deadline Approaching",
        "body_intro":       "This ticket's SLA deadline is approaching. Time remaining: {{time_remaining}}.",
    },
    "sla_breach": {
        "subject_template": "[{{ticket_number}}] SLA Breached: {{subject}}",
        "body_headline":    "SLA Breached",
        "body_intro":       "This ticket has exceeded its SLA deadline and requires immediate attention.",
    },
    "status_changed": {
        "subject_template": "[{{ticket_number}}] Status Updated: {{old_status}} \u2192 {{new_status}}",
        "body_headline":    "Ticket Status Updated",
        "body_intro":       "The status of your ticket has been updated from {{old_status}} to {{new_status}}.",
    },
    "priority_changed": {
        "subject_template": "[{{ticket_number}}] Priority Changed: {{old_priority}} \u2192 {{new_priority}}",
        "body_headline":    "Ticket Priority Changed",
        "body_intro":       "The priority has been changed from {{old_priority}} to {{new_priority}}.",
    },
    "team_assigned": {
        "subject_template": "[{{ticket_number}}] Team Assigned: {{subject}}",
        "body_headline":    "Ticket Assigned to Team",
        "body_intro":       "This ticket has been assigned to a team.",
    },
    "category_changed": {
        "subject_template": "[{{ticket_number}}] Category Updated: {{subject}}",
        "body_headline":    "Ticket Category Changed",
        "body_intro":       "The category for this ticket has been updated.",
    },
    "internal_note": {
        "subject_template": "[{{ticket_number}}] Internal Note: {{subject}}",
        "body_headline":    "Internal Note Added",
        "body_intro":       "{{author_name}} added an internal note:",
    },
    "task_created": {
        "subject_template": "[{{ticket_number}}] New Task: {{subject}}",
        "body_headline":    "New Task Created",
        "body_intro":       "A new task has been created and may require assignment.",
    },
    "bug_created": {
        "subject_template": "[{{ticket_number}}] Bug Report: {{subject}}",
        "body_headline":    "New Bug Report",
        "body_intro":       "A new bug report has been submitted and needs triage.",
    },
    "feature_created": {
        "subject_template": "[{{ticket_number}}] Feature Request: {{subject}}",
        "body_headline":    "New Feature Request",
        "body_intro":       "A new feature request has been submitted for review.",
    },
    "custom_created": {
        "subject_template": "[{{ticket_number}}] Custom Request: {{subject}}",
        "body_headline":    "New Custom Request",
        "body_intro":       "A new custom form request has been submitted.",
    },
}

# Variables available in every event template
COMMON_VARS = [
    "ticket_number", "subject", "description",
    "status", "priority", "category", "tags",
    "requester_name", "requester_email",
    "assignee_name",
    "created_date", "ticket_url",
    "tenant_name", "app_name",
]

# Additional per-event variables shown in the template editor
EXTRA_VARS: dict[str, list[str]] = {
    "status_changed":   ["old_status", "new_status"],
    "priority_changed": ["old_priority", "new_priority"],
    "agent_reply":      ["author_name"],
    "requester_reply":  ["author_name"],
    "internal_note":    ["author_name"],
    "sla_warning":      ["time_remaining"],
}


def _apply_vars(text: str, vars: dict) -> str:
    """Replace {{key}} placeholders with values from vars dict."""
    for key, val in vars.items():
        text = text.replace("{{" + key + "}}", str(val or ""))
    return text


def _format_date(dt) -> str:
    """Format a datetime or ISO string as 'Month DD, YYYY'."""
    if not dt:
        return ""
    from datetime import datetime
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return str(dt)
    try:
        return dt.strftime("%B %d, %Y")
    except Exception:
        return str(dt)


def _truncate(text: str, limit: int = 300) -> str:
    if not text:
        return ""
    return text[:limit] + ("…" if len(text) > limit else "")


def base_template(title: str, body_html: str, ticket_url: str, app_name: str = "Helpdesk") -> str:
    """Shared responsive HTML email wrapper."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;border:1px solid #e1e4e8;overflow:hidden;">
  <!-- Header -->
  <tr>
    <td style="background:#1a1d23;padding:20px 32px;">
      <span style="color:#ffffff;font-size:16px;font-weight:600;letter-spacing:0.5px;">{app_name}</span>
    </td>
  </tr>
  <!-- Body -->
  <tr>
    <td style="padding:32px;">
      {body_html}
      <div style="margin-top:28px;">
        <a href="{ticket_url}" style="display:inline-block;background:#4f8cff;color:#ffffff;text-decoration:none;padding:10px 24px;border-radius:6px;font-size:14px;font-weight:500;">View Ticket</a>
      </div>
    </td>
  </tr>
  <!-- Footer -->
  <tr>
    <td style="padding:16px 32px;border-top:1px solid #e1e4e8;background:#fafbfc;">
      <span style="font-size:12px;color:#6a737d;">Sent by {app_name} &middot; Powered by Resend</span>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _details_table(rows: list[tuple[str, str]]) -> str:
    """Render a 2-column key-value details grid."""
    visible = [(label, value) for label, value in rows if value]
    cells = ""
    for i in range(0, len(visible), 2):
        pair = visible[i:i+2]
        cells += '<tr>'
        for label, value in pair:
            cells += (
                f'<td style="padding:8px 16px 8px 0;font-size:13px;vertical-align:top;width:50%;">'
                f'<span style="display:block;color:#6a737d;font-size:11px;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:2px;">{label}</span>'
                f'<span style="color:#24292e;font-weight:500;">{value}</span>'
                f'</td>'
            )
        if len(pair) == 1:
            cells += '<td style="width:50%;"></td>'
        cells += '</tr>'
    return f'<table style="margin-top:16px;border-collapse:collapse;width:100%;">{cells}</table>'


def _priority_label(priority: str) -> str:
    labels = {"p1": "P1 — Urgent", "p2": "P2 — High", "p3": "P3 — Medium", "p4": "P4 — Low"}
    return labels.get(priority, priority.upper() if priority else "N/A")


def _status_label(status: str) -> str:
    return (status or "open").replace("_", " ").title()


def render_email(
    event: str,
    ticket: dict,
    comment: dict | None = None,
    extra: dict | None = None,
    template: dict | None = None,
) -> dict:
    """Render subject + HTML body for a ticket event.

    Args:
        event:    Event name (e.g. 'ticket_created', 'status_changed').
        ticket:   Ticket row dict (includes requester/assignee/tenant fields).
        comment:  Optional comment dict (content, author_name) or status-change
                  metadata (old_status, new_status).
        extra:    Extra context: app_name, ticket_url, role_target, time_remaining,
                  recent_comments (list of last N comment dicts for status_changed).
        template: Optional tenant override from notification_templates table with
                  keys subject_template, body_headline, body_intro.

    Returns: { "subject": str, "html": str }
    """
    extra = extra or {}
    tn = ticket.get("ticket_number", "")
    subj_text = ticket.get("subject", "")
    app_name = extra.get("app_name", "Helpdesk")
    ticket_url = extra.get("ticket_url", "")
    role = extra.get("role_target", "")

    # Build the common variable substitution map
    tmpl_vars = {
        "ticket_number":   tn,
        "subject":         subj_text,
        "description":     _truncate(ticket.get("description") or ""),
        "status":          _status_label(ticket.get("status", "")),
        "old_status":      _status_label((comment or {}).get("old_status", "")),
        "new_status":      _status_label((comment or {}).get("new_status", "")),
        "priority":        _priority_label(ticket.get("priority", "")),
        "category":        ticket.get("category", "") or "",
        "tags":            ", ".join(ticket.get("tags") or []),
        "requester_name":  ticket.get("requester_name", ""),
        "requester_email": ticket.get("requester_email", ""),
        "assignee_name":   ticket.get("assignee_name", "") or "Unassigned",
        "author_name":     (comment or {}).get("author_name", "Support Agent"),
        "time_remaining":  extra.get("time_remaining", "less than 1 hour"),
        "created_date":    _format_date(ticket.get("created_at")),
        "ticket_url":      ticket_url,
        "tenant_name":     ticket.get("tenant_name", ""),
        "app_name":        app_name,
    }

    # Resolve subject/headline/intro — prefer tenant override, fall back to defaults
    defaults = TEMPLATE_DEFAULTS.get(event, {
        "subject_template": f"[{{{{ticket_number}}}}] {event.replace('_', ' ').title()}: {{{{subject}}}}",
        "body_headline":    event.replace("_", " ").title(),
        "body_intro":       "",
    })
    tpl = template or defaults
    subject  = _apply_vars(tpl.get("subject_template", defaults["subject_template"]), tmpl_vars)
    headline = _apply_vars(tpl.get("body_headline",    defaults["body_headline"]),    tmpl_vars)
    intro    = _apply_vars(tpl.get("body_intro",       defaults["body_intro"]),       tmpl_vars)

    details = _details_table([
        ("Ticket",    tn),
        ("Priority",  _priority_label(ticket.get("priority", ""))),
        ("Status",    _status_label(ticket.get("status", ""))),
        ("Requester", ticket.get("requester_name", "")),
        ("Assignee",  ticket.get("assignee_name", "") or "Unassigned"),
    ])

    # --- Event-specific body rendering ---

    if event == "ticket_created":
        # Assignee gets a slightly different headline/intro regardless of template
        if role == "assignee" and not template:
            headline = "New Ticket Assigned"
            intro    = "A new ticket has been created and assigned to you."
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    elif event == "ticket_assigned":
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    elif event == "ticket_resolved":
        resolution = ""
        if comment and comment.get("content"):
            resolution = f'<div style="background:#f0fff4;border-left:3px solid #34d058;padding:12px 16px;margin-top:16px;border-radius:4px;font-size:14px;color:#24292e;">{comment["content"]}</div>'
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {resolution}
        {details}
        """

    elif event == "ticket_closed":
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    elif event == "agent_reply":
        comment_body = (comment or {}).get("content", "")
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0 0 16px;">{intro}</p>
        <div style="background:#f6f8fa;border-left:3px solid #4f8cff;padding:12px 16px;border-radius:4px;font-size:14px;color:#24292e;white-space:pre-wrap;">{comment_body}</div>
        """

    elif event == "requester_reply":
        comment_body = (comment or {}).get("content", "")
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0 0 16px;">{intro}</p>
        <div style="background:#f6f8fa;border-left:3px solid #f9826c;padding:12px 16px;border-radius:4px;font-size:14px;color:#24292e;white-space:pre-wrap;">{comment_body}</div>
        """

    elif event == "sla_warning":
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#e36209;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    elif event == "sla_breach":
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#cb2431;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    elif event == "status_changed":
        old_s = _status_label((comment or {}).get("old_status", ""))
        new_s = _status_label((comment or {}).get("new_status", ""))
        # Status change badge
        status_badge = (
            f'<div style="display:inline-block;margin-top:16px;padding:10px 16px;background:#f6f8fa;'
            f'border-radius:6px;font-size:14px;color:#24292e;">'
            f'<span style="color:#6a737d;">{old_s}</span>'
            f'<span style="margin:0 10px;color:#586069;">&#8594;</span>'
            f'<strong style="color:#24292e;">{new_s}</strong>'
            f'</div>'
        )
        # Last 1-2 public comments as history
        recent_html = ""
        recent_comments = extra.get("recent_comments") or []
        if recent_comments:
            recent_html = '<div style="margin-top:20px;"><div style="font-size:12px;font-weight:600;color:#6a737d;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Recent Activity</div>'
            for rc in recent_comments:
                rc_author = rc.get("author_name", "Agent")
                rc_content = rc.get("content", "")
                rc_html = rc_content[:400] + ("…" if len(rc_content) > 400 else "")
                recent_html += (
                    f'<div style="margin-bottom:8px;padding:10px 14px;background:#f6f8fa;border-radius:4px;">'
                    f'<div style="font-size:12px;font-weight:600;color:#586069;margin-bottom:4px;">{rc_author}</div>'
                    f'<div style="font-size:13px;color:#24292e;white-space:pre-wrap;">{rc_html}</div>'
                    f'</div>'
                )
            recent_html += '</div>'

        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {status_badge}
        {recent_html}
        {details}
        """

    else:
        body = f"""
        <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">{headline}</h2>
        <p style="color:#586069;font-size:14px;margin:0;">{intro}</p>
        <p style="font-size:15px;color:#24292e;margin:12px 0 0;font-weight:500;">{subj_text}</p>
        {details}
        """

    html = base_template(subject, body, ticket_url, app_name)
    return {"subject": subject, "html": html}


# ============================================================
# Invite email templates
# ============================================================

def invite_template(title: str, body_html: str, login_url: str, app_name: str = "Helpdesk") -> str:
    """Responsive HTML email wrapper for invite emails (CTA → login page)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;border:1px solid #e1e4e8;overflow:hidden;">
  <tr>
    <td style="background:#1a1d23;padding:20px 32px;">
      <span style="color:#ffffff;font-size:16px;font-weight:600;letter-spacing:0.5px;">{app_name}</span>
    </td>
  </tr>
  <tr>
    <td style="padding:32px;">
      {body_html}
      <div style="margin-top:28px;">
        <a href="{login_url}" style="display:inline-block;background:#4f8cff;color:#ffffff;text-decoration:none;padding:12px 32px;border-radius:6px;font-size:14px;font-weight:600;">Accept Invitation</a>
      </div>
      <p style="margin-top:16px;font-size:12px;color:#6a737d;">
        Sign in with your Microsoft or Google account to activate your access.
      </p>
    </td>
  </tr>
  <tr>
    <td style="padding:16px 32px;border-top:1px solid #e1e4e8;background:#fafbfc;">
      <span style="font-size:12px;color:#6a737d;">Sent by {app_name} &middot; Powered by Resend</span>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _action_template(title: str, body_html: str, action_url: str, action_label: str, app_name: str = "Helpdesk") -> str:
    """Responsive HTML email wrapper for single-action transactional emails."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;border:1px solid #e1e4e8;overflow:hidden;">
  <tr>
    <td style="background:#1a1d23;padding:20px 32px;">
      <span style="color:#ffffff;font-size:16px;font-weight:600;letter-spacing:0.5px;">{app_name}</span>
    </td>
  </tr>
  <tr>
    <td style="padding:32px;">
      {body_html}
      <div style="margin-top:28px;">
        <a href="{action_url}" style="display:inline-block;background:#4f8cff;color:#ffffff;text-decoration:none;padding:12px 32px;border-radius:6px;font-size:14px;font-weight:600;">{action_label}</a>
      </div>
      <p style="margin-top:16px;font-size:12px;color:#6a737d;">
        If you did not request this, you can safely ignore this email.
      </p>
    </td>
  </tr>
  <tr>
    <td style="padding:16px 32px;border-top:1px solid #e1e4e8;background:#fafbfc;">
      <span style="font-size:12px;color:#6a737d;">Sent by {app_name} &middot; Powered by Resend</span>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def render_password_reset_email(
    user_email: str,
    reset_url: str,
    app_name: str = "Helpdesk",
) -> dict:
    """Render subject + HTML body for a password reset email.

    Returns: { "subject": str, "html": str }
    """
    subject = f"Reset your {app_name} password"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">Reset Your Password</h2>
    <p style="color:#586069;font-size:14px;margin:0 0 8px;">
      We received a request to reset the password for <strong>{user_email}</strong>.
    </p>
    <p style="color:#586069;font-size:14px;margin:0;">
      Click the button below to choose a new password. This link expires in <strong>1 hour</strong>.
    </p>
    """
    html = _action_template(subject, body, reset_url, "Reset Password", app_name)
    return {"subject": subject, "html": html}


def render_verification_email(
    user_email: str,
    verify_url: str,
    app_name: str = "Helpdesk",
) -> dict:
    """Render subject + HTML body for an email verification email.

    Returns: { "subject": str, "html": str }
    """
    subject = f"Verify your {app_name} email"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">Verify Your Email Address</h2>
    <p style="color:#586069;font-size:14px;margin:0 0 8px;">
      Thanks for signing up! Please verify <strong>{user_email}</strong> to activate your account.
    </p>
    <p style="color:#586069;font-size:14px;margin:0;">
      This link expires in <strong>24 hours</strong>.
    </p>
    """
    html = _action_template(subject, body, verify_url, "Verify Email", app_name)
    return {"subject": subject, "html": html}


def render_invite_email(
    user_name: str,
    user_email: str,
    role: str,
    tenant_name: str,
    inviter_name: str | None = None,
    expires_at: str | None = None,
    app_name: str = "Helpdesk",
    login_url: str = "",
) -> dict:
    """Render subject + HTML body for a user invite email.

    Returns: { "subject": str, "html": str }
    """
    role_label = {
        "tenant_admin": "Administrator",
        "agent": "Support Agent",
        "end_user": "Team Member",
    }.get(role, role)
    inviter_line = f" by <strong>{inviter_name}</strong>" if inviter_name else ""

    expires_line = ""
    if expires_at:
        try:
            from datetime import datetime
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            expires_line = (
                f'<p style="font-size:13px;color:#586069;margin:8px 0 0;">'
                f'This invitation expires on <strong>{exp.strftime("%B %d, %Y")}</strong>.</p>'
            )
        except Exception:
            pass

    details = _details_table([
        ("Organization", tenant_name),
        ("Role", role_label),
        ("Email", user_email),
    ])

    subject = f"You're invited to {tenant_name} on {app_name}"
    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">You've Been Invited</h2>
    <p style="color:#586069;font-size:14px;margin:0 0 4px;">
      You've been invited{inviter_line} to join <strong>{tenant_name}</strong> as a <strong>{role_label}</strong>.
    </p>
    <p style="color:#586069;font-size:14px;margin:4px 0 0;">
      Click the button below to accept and set up your account.
    </p>
    {details}
    {expires_line}
    """

    html = invite_template(subject, body, login_url, app_name)
    return {"subject": subject, "html": html}


# ============================================================
# CSAT (Customer Satisfaction) email template
# ============================================================

def render_csat_email(
    ticket_number: str,
    ticket_subject: str,
    requester_name: str,
    survey_url: str,
    app_name: str = "Helpdesk",
) -> dict:
    """Render subject + HTML body for a CSAT survey email.

    Returns: { "subject": str, "html": str }
    """
    subject = f"[{ticket_number}] How was your experience?"

    # Build 5 rating buttons (1-5 stars)
    rating_buttons = ""
    star_labels = ["Very Unsatisfied", "Unsatisfied", "Neutral", "Satisfied", "Very Satisfied"]
    for i in range(1, 6):
        color = "#cb2431" if i <= 2 else ("#e36209" if i == 3 else "#34d058")
        rating_buttons += (
            f'<a href="{survey_url}?rating={i}" '
            f'style="display:inline-block;width:48px;height:48px;line-height:48px;text-align:center;'
            f'background:{color};color:#ffffff;text-decoration:none;border-radius:8px;font-size:20px;'
            f'font-weight:700;margin:0 4px;" title="{star_labels[i-1]}">{i}</a>'
        )

    body = f"""
    <h2 style="margin:0 0 8px;font-size:18px;color:#24292e;">How did we do?</h2>
    <p style="color:#586069;font-size:14px;margin:0 0 4px;">
      Hi {requester_name or "there"}, your ticket <strong>{ticket_number}</strong> has been resolved.
    </p>
    <p style="color:#586069;font-size:14px;margin:4px 0 0;">
      <strong>{ticket_subject}</strong>
    </p>
    <p style="color:#586069;font-size:14px;margin:16px 0 8px;">
      Please rate your experience (1 = poor, 5 = excellent):
    </p>
    <div style="text-align:center;margin:20px 0;">
      {rating_buttons}
    </div>
    <p style="color:#6a737d;font-size:12px;margin:16px 0 0;">
      Your feedback helps us improve our service.
    </p>
    """

    html = base_template(subject, body, survey_url, app_name)
    return {"subject": subject, "html": html}
