"""Notification service: Teams webhooks, Slack webhooks, email dispatch, in-app notifications."""

import json
import logging

import requests as http_requests

from config import Config
from models.db import fetch_one, insert_returning

logger = logging.getLogger(__name__)


def _get_tenant_notification_config(tenant_id: int) -> dict:
    """Return tenant notification settings (webhook URLs, app_url, app_name) from DB."""
    try:
        tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
        if not tenant:
            return {}
        settings = tenant.get("settings") or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        return settings
    except Exception as e:
        logger.error("Failed to fetch tenant notification config for tenant %s: %s", tenant_id, e)
        return {}


def notify_ticket_event(tenant_id: int, ticket_id: int, event: str, comment: dict | None = None):
    """Dispatch notifications for a ticket event."""
    ticket = fetch_one(
        """SELECT t.*, u_req.name as requester_name, u_asg.name as assignee_name
           FROM tickets t
           LEFT JOIN users u_req ON u_req.id = t.requester_id
           LEFT JOIN users u_asg ON u_asg.id = t.assignee_id
           WHERE t.id = %s""",
        [ticket_id],
    )
    if not ticket:
        return

    # Per-tenant config from DB (one extra query, never requires .env changes)
    tenant_cfg = _get_tenant_notification_config(tenant_id)
    teams_url = tenant_cfg.get("teams_webhook_url") or ""
    slack_url = tenant_cfg.get("slack_webhook_url") or ""
    app_url = (tenant_cfg.get("app_url") or Config.APP_URL).rstrip("/")

    # Teams webhook
    if teams_url:
        _send_teams_notification(teams_url, ticket, event, tenant_id, app_url)

    # Slack webhook
    if slack_url:
        _send_slack_notification(slack_url, ticket, event, tenant_id, app_url)

    # Log in-app notification (pass only JSON-serializable fields)
    _log_notification(tenant_id, ticket_id, "in_app", event, {
        "event": event,
        "ticket_number": ticket.get("ticket_number"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "requester_name": ticket.get("requester_name"),
        "assignee_name": ticket.get("assignee_name"),
    })

    # Email dispatch via Resend
    try:
        from services.email_service import dispatch_ticket_emails
        dispatch_ticket_emails(tenant_id, ticket_id, event, comment=comment)
    except Exception as e:
        logger.error("Email dispatch failed for ticket %s: %s", ticket_id, e)


def _send_teams_notification(webhook_url: str, ticket: dict, event: str, tenant_id: int, app_url: str = ""):
    """POST adaptive card to Teams incoming webhook."""
    event_labels = {
        "ticket_created": "New Ticket",
        "ticket_assigned": "Ticket Assigned",
        "ticket_resolved": "Ticket Resolved",
        "ticket_closed": "Ticket Closed",
        "agent_reply": "Agent Reply",
        "requester_reply": "Requester Reply",
        "sla_warning": "SLA Warning",
        "sla_breach": "SLA Breach",
        "comment_added": "New Comment",
    }
    label = event_labels.get(event, event.replace("_", " ").title())

    priority_colors = {
        "p1": "Attention",
        "p2": "Warning",
        "p3": "Accent",
        "p4": "Good",
    }
    color = priority_colors.get(ticket.get("priority", "medium"), "Default")

    base_url = app_url or Config.APP_URL
    ticket_url = f"{base_url}/tickets/{ticket['id']}"

    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": f"{label}: {ticket['ticket_number']}",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": color,
                    },
                    {
                        "type": "TextBlock",
                        "text": ticket.get("subject", ""),
                        "wrap": True,
                    },
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "Priority", "value": ticket.get("priority", "N/A").upper()},
                            {"title": "Status", "value": ticket.get("status", "N/A").replace("_", " ").title()},
                            {"title": "Assignee", "value": ticket.get("assignee_name") or "Unassigned"},
                            {"title": "Requester", "value": ticket.get("requester_name") or "Unknown"},
                        ],
                    },
                ],
                "actions": [
                    {"type": "Action.OpenUrl", "title": "View Ticket", "url": ticket_url},
                ],
            },
        }],
    }

    try:
        resp = http_requests.post(webhook_url, json=card, timeout=10)
        status = "sent" if resp.status_code < 400 else "failed"
        _log_notification(
            tenant_id, ticket["id"], "teams_webhook", event,
            {"status_code": resp.status_code}, status=status,
        )
    except Exception as e:
        logger.error("Teams webhook failed: %s", e)
        _log_notification(
            tenant_id, ticket["id"], "teams_webhook", event,
            {"error": str(e)}, status="failed", error_message=str(e),
        )


def _send_slack_notification(webhook_url: str, ticket: dict, event: str, tenant_id: int, app_url: str = ""):
    """POST message to Slack incoming webhook."""
    event_labels = {
        "ticket_created": "New Ticket",
        "ticket_assigned": "Ticket Assigned",
        "ticket_resolved": "Ticket Resolved",
        "ticket_closed": "Ticket Closed",
        "agent_reply": "Agent Reply",
        "requester_reply": "Requester Reply",
        "sla_warning": "SLA Warning",
        "sla_breach": "SLA Breach",
        "comment_added": "New Comment",
    }
    label = event_labels.get(event, event.replace("_", " ").title())

    priority_emoji = {
        "p1": ":red_circle:",
        "p2": ":large_orange_circle:",
        "p3": ":large_blue_circle:",
        "p4": ":white_circle:",
    }
    emoji = priority_emoji.get(ticket.get("priority", ""), ":large_blue_circle:")

    base_url = app_url or Config.APP_URL
    ticket_url = f"{base_url}/tickets/{ticket['id']}"
    assignee = ticket.get("assignee_name") or "Unassigned"
    requester = ticket.get("requester_name") or "Unknown"
    priority = ticket.get("priority", "N/A").upper()
    status = ticket.get("status", "N/A").replace("_", " ").title()

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{label}* — <{ticket_url}|{ticket['ticket_number']}>\n"
                        f"{ticket.get('subject', '')}"
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Priority:* {priority}"},
                    {"type": "mrkdwn", "text": f"*Status:* {status}"},
                    {"type": "mrkdwn", "text": f"*Assignee:* {assignee}"},
                    {"type": "mrkdwn", "text": f"*Requester:* {requester}"},
                ],
            },
        ]
    }

    try:
        resp = http_requests.post(webhook_url, json=payload, timeout=10)
        slack_status = "sent" if resp.status_code < 400 else "failed"
        _log_notification(
            tenant_id, ticket["id"], "slack_webhook", event,
            {"status_code": resp.status_code}, status=slack_status,
        )
    except Exception as e:
        logger.error("Slack webhook failed: %s", e)
        _log_notification(
            tenant_id, ticket["id"], "slack_webhook", event,
            {"error": str(e)}, status="failed", error_message=str(e),
        )


def _log_notification(
    tenant_id: int, ticket_id: int, channel: str, event: str,
    payload=None, status: str = "sent", error_message: str = None,
):
    """Record notification in the notifications table."""
    try:
        insert_returning(
            """INSERT INTO notifications (tenant_id, ticket_id, channel, recipient, payload, status, error_message, sent_at)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, CASE WHEN %s = 'sent' THEN now() ELSE NULL END)
               RETURNING id""",
            [tenant_id, ticket_id, channel, event,
             json.dumps(payload) if payload else "{}",
             status, error_message, status],
        )
    except Exception as e:
        logger.error("Failed to log notification: %s", e)
