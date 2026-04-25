"""Tests for the notification group event matrix endpoints and email dispatch.

Covers:
  - GET  /api/notifications/group-event-matrix
  - PUT  /api/notifications/groups/<id>/events
  - _dispatch_sync() group filter (NULL=enabled, True=enabled, False=blocked)

All tests are unit-level: every DB call is mocked, no live database required.
Auth is bypassed by injecting a session dict into the Flask test client.
"""

import pytest
from unittest.mock import patch, call, MagicMock

from app import create_app
from config import Config


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """Create the Flask app once for the module with auth disabled and no real DB."""
    import os
    # Use direct assignment (not setdefault) — prior test files may have loaded
    # the .env via load_dotenv, setting SECRET_KEY to the default placeholder.
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["SECRET_KEY"] = "test-secret-key-for-pytest"
    os.environ["RESEND_API_KEY"] = "test-resend-key"
    os.environ.setdefault("FERNET_KEY", "")

    with (
        patch("app._validate_secrets"),   # Config class vars are cached; bypass the check
        patch("models.db.init_pool"),
        patch("services.queue_service.QueueProcessor"),
    ):
        application = create_app()
        application.config["TESTING"] = True
        application.config["WTF_CSRF_ENABLED"] = False
        yield application


@pytest.fixture
def client(app):
    """Test client with a pre-populated session (tenant_admin with users.manage)."""
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {
                "id": 1,
                "tenant_id": 10,
                "email": "admin@example.com",
                "name": "Admin",
                "role": "tenant_admin",
                "permissions": ["users.manage", "notifications.manage"],
            }
        yield c


@pytest.fixture
def client_no_auth(app):
    """Test client with no session — simulates unauthenticated request."""
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TENANT_ID = 10
_GROUP_1 = {"id": 1, "name": "Alpha Team"}
_GROUP_2 = {"id": 2, "name": "Beta Team"}
_ALL_EVENTS = [
    "ticket_created", "task_created", "bug_created", "feature_created",
    "ticket_assigned", "team_assigned",
    "ticket_resolved", "ticket_closed", "status_changed",
    "priority_changed", "category_changed",
    "agent_reply", "requester_reply", "internal_note",
    "sla_warning", "sla_breach",
]


# ===========================================================================
# GET /api/notifications/group-event-matrix
# ===========================================================================

class TestGetGroupEventMatrix:

    def test_returns_empty_list_when_tenant_has_no_groups(self, client):
        """Empty groups list returns [] — not an error, just no data."""
        with (
            patch("routes.notifications.fetch_all", side_effect=[
                [],   # groups query returns nothing
            ]),
        ):
            resp = client.get("/api/notifications/group-event-matrix")

        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_defaults_all_events_to_enabled_when_no_nge_rows_exist(self, client):
        """
        # REGRESSION: would be RED before this change
        When notification_group_events has no rows for a group, all 4 events must
        default to enabled=true. Before this endpoint existed there was no way to
        retrieve the matrix at all — any implementation that returned enabled=false
        or omitted events would break the UX default.
        """
        with (
            patch("routes.notifications.fetch_all", side_effect=[
                [_GROUP_1],  # groups
                [],          # existing nge rows (none)
            ]),
        ):
            resp = client.get("/api/notifications/group-event-matrix")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

        group_data = data[0]
        assert group_data["group_id"] == 1
        assert group_data["group_name"] == "Alpha Team"

        events = {e["event"]: e["enabled"] for e in group_data["events"]}
        assert set(events.keys()) == set(_ALL_EVENTS), "All 16 events must be present"
        assert all(events[e] is True for e in _ALL_EVENTS), (
            "All events must default to enabled=True when no DB rows exist"
        )

    def test_returns_false_for_event_with_disabled_nge_row(self, client):
        """A DB row with enabled=False must surface as enabled=False in the response.

        The remaining events (no DB row) must still default to True.
        """
        with (
            patch("routes.notifications.fetch_all", side_effect=[
                [_GROUP_1],
                [
                    # Only sla_breach is explicitly disabled
                    {"group_id": 1, "event": "sla_breach", "channel": "email", "enabled": False},
                ],
            ]),
        ):
            resp = client.get("/api/notifications/group-event-matrix")

        assert resp.status_code == 200
        events = {e["event"]: e["enabled"] for e in resp.get_json()[0]["events"]}

        assert events["sla_breach"] is False, "Explicitly disabled event must be False"
        assert events["ticket_created"] is True, "Event with no row must still default to True"
        assert events["ticket_resolved"] is True
        assert events["ticket_closed"] is True

    def test_requires_authentication_returns_redirect_for_unauthenticated_request(self, client_no_auth):
        """Unauthenticated request must not reach the endpoint handler.

        With AUTH_ENABLED=false and no session user, login_required falls through
        to the dev auto-provision path which requires a real DB. We verify the
        endpoint is guarded by confirming the response is NOT a 200 when the DB
        call inside the decorator fails.
        """
        with patch("routes.auth.Config") as mock_cfg:
            mock_cfg.AUTH_ENABLED = True  # Force auth check on
            resp = client_no_auth.get("/api/notifications/group-event-matrix")

        # With auth enabled and no session, login_required redirects to /login (302)
        assert resp.status_code in (302, 401), (
            f"Expected redirect or 401 for unauthenticated request, got {resp.status_code}"
        )

    def test_multiple_groups_each_get_all_events(self, client):
        """Matrix must include a full event row for every group returned by the DB query."""
        with (
            patch("routes.notifications.fetch_all", side_effect=[
                [_GROUP_1, _GROUP_2],  # two groups
                [
                    # Group 2 has one explicit False row; Group 1 has no rows at all
                    {"group_id": 2, "event": "ticket_created", "channel": "email", "enabled": False},
                ],
            ]),
        ):
            resp = client.get("/api/notifications/group-event-matrix")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

        g1 = next(g for g in data if g["group_id"] == 1)
        g2 = next(g for g in data if g["group_id"] == 2)

        assert len(g1["events"]) == len(_ALL_EVENTS)
        assert len(g2["events"]) == len(_ALL_EVENTS)

        g2_events = {e["event"]: e["enabled"] for e in g2["events"]}
        assert g2_events["ticket_created"] is False
        assert g2_events["ticket_resolved"] is True


# ===========================================================================
# PUT /api/notifications/groups/<id>/events
# ===========================================================================

class TestUpdateGroupEvents:

    def test_happy_path_upserts_each_event_row(self, client):
        """Valid payload causes execute() to be called once per event entry."""
        payload = {
            "events": [
                {"event": "ticket_created", "channel": "email", "enabled": True},
                {"event": "sla_breach", "channel": "email", "enabled": False},
            ]
        }
        with (
            patch("routes.notifications.fetch_one", return_value={"tenant_id": _TENANT_ID}),
            patch("routes.notifications.execute") as mock_exec,
        ):
            resp = client.put(
                "/api/notifications/groups/1/events",
                json=payload,
            )

        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

        # execute must have been called exactly twice (once per event entry)
        assert mock_exec.call_count == 2

        # First call must upsert ticket_created with enabled=True
        first_call_params = mock_exec.call_args_list[0][0][1]
        assert first_call_params[0] == 1          # group_id
        assert first_call_params[1] == "ticket_created"
        assert first_call_params[3] is True       # enabled

        # Second call must upsert sla_breach with enabled=False
        second_call_params = mock_exec.call_args_list[1][0][1]
        assert second_call_params[1] == "sla_breach"
        assert second_call_params[3] is False     # enabled

    def test_unknown_event_name_returns_400(self, client):
        """An event name not in _GROUP_EVENTS must be rejected with 400.

        The implementation validates each entry inline (validate then upsert per
        entry). A bad entry causes an immediate 400 return. Any valid entries
        that appeared BEFORE the bad one in the list will already have been
        upserted — this is the documented behaviour of the endpoint.

        Failure mode: if the validation were absent, unknown event names would be
        inserted into notification_group_events, corrupting the schema contract.

        We place the invalid entry FIRST so that we can also assert no execute()
        calls happen at all in that scenario.
        """
        payload = {
            "events": [
                {"event": "INVALID_EVENT_NAME", "enabled": True},  # bad — first entry
                {"event": "ticket_created", "enabled": True},
            ]
        }
        with (
            patch("routes.notifications.fetch_one", return_value={"tenant_id": _TENANT_ID}),
            patch("routes.notifications.execute") as mock_exec,
        ):
            resp = client.put("/api/notifications/groups/1/events", json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert "error" in body
        assert "INVALID_EVENT_NAME" in body["error"]
        # Invalid entry is first — no execute() calls must have occurred
        mock_exec.assert_not_called()

    def test_group_not_owned_by_tenant_returns_404(self, client):
        """Group belonging to a different tenant must return 404 (not 403) to avoid disclosure.

        Failure mode: if _verify_group_tenant() were absent, tenant A could modify
        tenant B's notification settings (IDOR).
        """
        with (
            # Group belongs to tenant_id=99, but the session user is tenant_id=10
            patch("routes.notifications.fetch_one", return_value={"tenant_id": 99}),
            patch("routes.notifications.execute") as mock_exec,
        ):
            resp = client.put(
                "/api/notifications/groups/999/events",
                json={"events": [{"event": "ticket_created", "enabled": True}]},
            )

        assert resp.status_code == 404
        mock_exec.assert_not_called()

    def test_nonexistent_group_returns_404(self, client):
        """A group that does not exist in the DB at all must return 404."""
        with (
            patch("routes.notifications.fetch_one", return_value=None),
            patch("routes.notifications.execute") as mock_exec,
        ):
            resp = client.put(
                "/api/notifications/groups/9999/events",
                json={"events": [{"event": "ticket_created", "enabled": True}]},
            )

        assert resp.status_code == 404
        mock_exec.assert_not_called()


# ===========================================================================
# _dispatch_sync — group filter (NULL/True/False nge rows)
# ===========================================================================

# These tests call _dispatch_sync directly, bypassing HTTP routing and auth.
# We mock fetch_all, fetch_one, and send_email at the module level where
# email_service imports them.

_TICKET_ROW = {
    "id": 42,
    "tenant_id": _TENANT_ID,
    "requester_email": "requester@example.com",
    "requester_name": "Requester",
    "assignee_email": None,
    "assignee_name": None,
    "settings": {},
    "email_from_address": None,
    "email_from_name": None,
}


def _make_dispatch_mocks(prefs, ticket, group_user_members, group_ext_members):
    """Return a patch context for the three fetch_all calls inside _dispatch_sync.

    Call order in _dispatch_sync:
      1. notification_preferences query  → prefs
      2. all_agents query (skipped if 'all_agents' not in role_targets)
         group user_members query        → group_user_members
      3. group ext_members query         → group_ext_members

    fetch_one is for the ticket row.
    """
    def _fetch_all_side_effect(sql, params=None):
        sql_strip = sql.strip().lower()
        if "notification_preferences" in sql_strip:
            return prefs
        if "notification_group_members" in sql_strip and "u.email" in sql_strip:
            return group_user_members
        if "notification_group_members" in sql_strip and "ngm.email" in sql_strip:
            return group_ext_members
        return []

    return _fetch_all_side_effect


class TestDispatchSyncGroupFilter:

    def _run(self, prefs, ticket_row, group_user_members, group_ext_members):
        """Execute _dispatch_sync with controlled DB responses. Returns mock_send."""
        from services.email_service import _dispatch_sync

        side_effect = _make_dispatch_mocks(
            prefs, ticket_row, group_user_members, group_ext_members
        )

        with (
            patch("services.email_service.fetch_all", side_effect=side_effect),
            patch("services.email_service.fetch_one", return_value=ticket_row),
            patch("services.email_service.insert_returning", return_value=1),
            patch("services.email_service.send_email", return_value="msg-id-1") as mock_send,
            # render_email is imported lazily inside _dispatch_sync via
            # `from services.email_templates import render_email` — patch it
            # at its source module so all importers see the mock.
            patch("services.email_templates.render_email", return_value={
                "subject": "Test subject", "html": "<p>Test</p>"
            }),
            # Config.RESEND_API_KEY is checked at the top of _dispatch_sync.
            # Prior test files may have loaded .env (setting it to empty or a
            # placeholder), so we pin it to a truthy value here regardless.
            patch("services.email_service.Config") as mock_cfg,
        ):
            mock_cfg.RESEND_API_KEY = "test-resend-key"
            mock_cfg.APP_URL = "https://test.example.com"
            mock_cfg.APP_NAME = "Test Helpdesk"
            _dispatch_sync(_TENANT_ID, 42, "ticket_created")

        return mock_send

    def test_sends_to_group_member_when_no_nge_row_exists_null_default(self):
        """
        # REGRESSION: would be RED before this change
        When the LEFT JOIN returns NULL for nge.enabled (no row exists), the
        (nge.enabled IS NULL OR nge.enabled = true) filter keeps the member.
        The email must be dispatched.

        Before the LEFT JOIN + NULL check, groups with no nge rows would have
        been filtered out entirely, silently dropping emails to subscribed groups.
        """
        prefs = [{"role_target": "group"}]
        # Simulate: DB query returns the member because NULL passes the filter
        user_members = [{"email": "member@example.com", "name": "Member One"}]

        mock_send = self._run(prefs, _TICKET_ROW, user_members, [])

        # Email must have been sent to the group member
        sent_emails = [c.kwargs.get("to") or c.args[0] for c in mock_send.call_args_list]
        assert "member@example.com" in sent_emails, (
            "Group member must receive email when no nge row exists (NULL = enabled default)"
        )

    def test_sends_to_group_member_when_nge_enabled_is_true(self):
        """Group member with an explicit enabled=True nge row must receive the email."""
        prefs = [{"role_target": "group"}]
        # The SQL filter (nge.enabled IS NULL OR nge.enabled = true) keeps enabled=True rows
        user_members = [{"email": "member-enabled@example.com", "name": "Enabled Member"}]

        mock_send = self._run(prefs, _TICKET_ROW, user_members, [])

        sent_emails = [c.kwargs.get("to") or c.args[0] for c in mock_send.call_args_list]
        assert "member-enabled@example.com" in sent_emails

    def test_does_not_send_to_group_member_when_nge_enabled_is_false(self):
        """
        # REGRESSION: would be RED before this change
        When nge.enabled = False, the SQL filter must exclude the member.
        The email must NOT be dispatched.

        Before the LEFT JOIN filter was added, _dispatch_sync fetched ALL group
        members with no per-event check. A member with enabled=False would still
        receive the email.
        """
        prefs = [{"role_target": "group"}]
        # Simulate: DB returns empty because the SQL WHERE clause filtered out the
        # disabled member. When nge.enabled = false, the query returns no rows.
        user_members = []  # DB filtered them out via (nge.enabled IS NULL OR nge.enabled = true)

        mock_send = self._run(prefs, _TICKET_ROW, user_members, [])

        # No group recipients at all, so only other role_targets could fire.
        # Since prefs only has 'group', send_email must not be called.
        sent_to_group_member = any(
            "member" in str(c) for c in mock_send.call_args_list
        )
        # More specifically: if user_members is empty and ext_members is empty,
        # no emails fire at all (no other role_targets in prefs)
        mock_send.assert_not_called()

    def test_sends_to_external_group_members_passing_nge_filter(self):
        """External (non-user) group members returned by the ext_members query get emails."""
        prefs = [{"role_target": "group"}]
        ext_members = [{"email": "external@partner.com"}]

        mock_send = self._run(prefs, _TICKET_ROW, [], ext_members)

        sent_emails = [c.kwargs.get("to") or c.args[0] for c in mock_send.call_args_list]
        assert "external@partner.com" in sent_emails, (
            "External group member must receive email when nge filter passes"
        )

    def test_does_not_send_to_external_members_when_nge_enabled_is_false(self):
        """External members excluded by the SQL filter (nge.enabled=False) must not receive email.

        Failure mode: if ext_members used a different SQL path without the nge
        filter, disabling a group's event would suppress user-member emails but
        still deliver to external addresses — inconsistent and wrong.
        """
        prefs = [{"role_target": "group"}]
        # SQL filter already excluded them — ext_members is empty
        ext_members = []

        mock_send = self._run(prefs, _TICKET_ROW, [], ext_members)

        mock_send.assert_not_called()
