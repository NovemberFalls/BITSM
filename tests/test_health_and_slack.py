"""Tests for the queue-aware health endpoint and Slack notification channel.

Health endpoint (GET /api/webhooks/health):
  - Returns 200 + queue_processor=alive when pq-poll thread is live
  - Returns 503 + queue_processor=dead  when pq-poll thread is absent

Slack notification (_send_slack_notification):
  - notify_ticket_event calls Slack when slack_webhook_url is set in tenant settings
  - Slack payload contains the expected block structure
  - notify_ticket_event skips Slack when slack_webhook_url is empty
  - Failed Slack POST logs to notifications table with status=failed

All tests are unit-level: DB calls mocked, no live server required.
"""

import pytest
from unittest.mock import patch, MagicMock

from app import create_app


# ---------------------------------------------------------------------------
# App fixture (shared for module)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    import os
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["SECRET_KEY"] = "test-secret-key-for-pytest"
    os.environ.setdefault("FERNET_KEY", "")

    with (
        patch("app._validate_secrets"),
        patch("models.db.init_pool"),
        patch("services.queue_service.QueueProcessor"),
    ):
        application = create_app()
        application.config["TESTING"] = True
        yield application


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_ok_when_queue_alive(self, client):
        """200 + queue_processor=alive when pq-poll thread is running."""
        with patch("services.queue_service.is_queue_alive", return_value=True):
            resp = client.get("/api/webhooks/health")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["service"] == "helpdesk"
        assert data["queue_processor"] == "alive"

    def test_health_degraded_when_queue_dead(self, client):
        """503 + queue_processor=dead when pq-poll thread is absent."""
        with patch("services.queue_service.is_queue_alive", return_value=False):
            resp = client.get("/api/webhooks/health")

        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "degraded"
        assert data["queue_processor"] == "dead"

    def test_health_no_auth_required(self, client):
        """Health endpoint must be reachable without any session or API key."""
        with patch("services.queue_service.is_queue_alive", return_value=True):
            resp = client.get("/api/webhooks/health")
        assert resp.status_code != 401
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

SAMPLE_TICKET = {
    "id": 42,
    "ticket_number": "TKT-00042",
    "subject": "POS terminal offline at Store 7",
    "priority": "p1",
    "status": "open",
    "requester_name": "Jane Smith",
    "assignee_name": "Bob Agent",
}


class TestSlackNotification:
    def test_slack_called_when_url_configured(self, app):
        """notify_ticket_event posts to Slack when slack_webhook_url is set in tenant settings."""
        with app.app_context():
            with (
                patch("services.notification_service._get_tenant_notification_config",
                      return_value={"slack_webhook_url": "https://hooks.slack.com/test"}),
                patch("services.notification_service.fetch_one", return_value=SAMPLE_TICKET),
                patch("services.notification_service.insert_returning"),
                patch("services.notification_service.http_requests.post") as mock_post,
                patch("services.email_service.dispatch_ticket_emails"),
            ):
                mock_post.return_value = MagicMock(status_code=200)

                from services.notification_service import notify_ticket_event
                notify_ticket_event(1, 42, "ticket_created")

                mock_post.assert_called_once()
                call_kwargs = mock_post.call_args
                assert call_kwargs[0][0] == "https://hooks.slack.com/test"

    def test_slack_payload_has_blocks(self, app):
        """Slack payload uses the blocks layout with ticket fields."""
        with app.app_context():
            with (
                patch("services.notification_service._get_tenant_notification_config",
                      return_value={"slack_webhook_url": "https://hooks.slack.com/test"}),
                patch("services.notification_service.fetch_one", return_value=SAMPLE_TICKET),
                patch("services.notification_service.insert_returning"),
                patch("services.notification_service.http_requests.post") as mock_post,
                patch("services.email_service.dispatch_ticket_emails"),
            ):
                mock_post.return_value = MagicMock(status_code=200)

                from services.notification_service import notify_ticket_event
                notify_ticket_event(1, 42, "ticket_created")

                payload = mock_post.call_args[1]["json"]
                assert "blocks" in payload
                assert len(payload["blocks"]) == 2
                # First block contains ticket number and subject
                header_text = payload["blocks"][0]["text"]["text"]
                assert "TKT-00042" in header_text
                assert "POS terminal offline" in header_text
                # Second block has 4 fields
                assert len(payload["blocks"][1]["fields"]) == 4

    def test_slack_skipped_when_url_empty(self, app):
        """notify_ticket_event does not call requests.post when slack_webhook_url is empty."""
        with app.app_context():
            with (
                patch("services.notification_service._get_tenant_notification_config",
                      return_value={}),
                patch("services.notification_service.fetch_one", return_value=SAMPLE_TICKET),
                patch("services.notification_service.insert_returning"),
                patch("services.notification_service.http_requests.post") as mock_post,
                patch("services.email_service.dispatch_ticket_emails"),
            ):
                from services.notification_service import notify_ticket_event
                notify_ticket_event(1, 42, "ticket_created")

                mock_post.assert_not_called()

    def test_slack_failure_logs_to_notifications(self, app):
        """A failed Slack POST logs status=failed to the notifications table."""
        with app.app_context():
            with (
                patch("services.notification_service._get_tenant_notification_config",
                      return_value={"slack_webhook_url": "https://hooks.slack.com/test"}),
                patch("services.notification_service.fetch_one", return_value=SAMPLE_TICKET),
                patch("services.notification_service.insert_returning") as mock_insert,
                patch("services.notification_service.http_requests.post") as mock_post,
                patch("services.email_service.dispatch_ticket_emails"),
            ):
                mock_post.return_value = MagicMock(status_code=500)

                from services.notification_service import notify_ticket_event
                notify_ticket_event(1, 42, "ticket_created")

                # Find the slack_webhook insert call
                slack_calls = [
                    c for c in mock_insert.call_args_list
                    if "slack_webhook" in str(c)
                ]
                assert slack_calls, "Expected a slack_webhook row in notifications table"
                # The args list includes status; 500 response → status=failed
                call_args = slack_calls[0][0][1]  # positional params list
                assert "failed" in call_args
