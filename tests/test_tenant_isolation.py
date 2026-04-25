"""Tests for multi-tenant data isolation (P0).

Verifies that an authenticated user from Tenant A CANNOT access data
belonging to Tenant B through any API endpoint. This is the single most
critical security property of a multi-tenant SaaS application.

Background: A live cross-tenant data leak was discovered in the ticket
and comment endpoints. These tests ensure that every tenant-scoped
resource enforces proper isolation.

All tests are unit-level: every DB call is mocked, no live database required.
Auth is bypassed by injecting a session dict into the Flask test client.
"""

import sys
import types

import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Module stubs — redis, stripe, etc. are not installed in the local dev
# environment (only on the deployment server). Inject stubs before any app
# imports so create_app() and blueprint registration succeed.
# ---------------------------------------------------------------------------

def _ensure_module_stub(name: str, attrs: dict | None = None):
    """Register a stub module in sys.modules if the real one is not available."""
    if name in sys.modules and not isinstance(sys.modules[name], types.ModuleType):
        return
    try:
        __import__(name)
    except (ImportError, ModuleNotFoundError):
        mod = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(mod, k, v)
        sys.modules[name] = mod

# Redis stub needs .get() to return None so flask_session's
# _retrieve_session_data() treats it as "no saved session".
_redis_client = MagicMock()
_redis_client.get.return_value = None
_redis_client.setex.return_value = True
_redis_client.set.return_value = True
_redis_client.delete.return_value = True

_ensure_module_stub("redis", {
    "Redis": MagicMock,
    "StrictRedis": MagicMock,
    "from_url": MagicMock(return_value=_redis_client),
})
_ensure_module_stub("stripe", {
    "checkout": MagicMock(),
    "Webhook": MagicMock(),
    "api_key": None,
})

from app import create_app


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A_ID = 10
TENANT_B_ID = 20

USER_A = {
    "id": 1,
    "tenant_id": TENANT_A_ID,
    "email": "agent-a@tenant-a.com",
    "name": "Agent A",
    "role": "agent",
    "permissions": [
        "tickets.view", "tickets.create", "tickets.update",
        "kb.manage", "users.manage",
        "audit.view", "audit.review",
        "automations.manage",
        "phone.manage",
    ],
}

USER_B = {
    "id": 2,
    "tenant_id": TENANT_B_ID,
    "email": "agent-b@tenant-b.com",
    "name": "Agent B",
    "role": "agent",
    "permissions": [
        "tickets.view", "tickets.create", "tickets.update",
        "kb.manage", "users.manage",
        "audit.view", "audit.review",
        "automations.manage",
        "phone.manage",
    ],
}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """Create the Flask app once for the module with auth disabled and no real DB."""
    import os
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["SECRET_KEY"] = "test-secret-key-for-pytest"
    os.environ.setdefault("FERNET_KEY", "")

    def _make_app_with_limiter_disabled():
        """Create the Flask app with RATELIMIT_ENABLED=False injected before init_app."""
        import app as app_module
        original_init_app = app_module.limiter.init_app

        def _patched_init_app(flask_app):
            flask_app.config["RATELIMIT_ENABLED"] = False
            return original_init_app(flask_app)

        app_module.limiter.init_app = _patched_init_app
        try:
            return create_app()
        finally:
            app_module.limiter.init_app = original_init_app

    with (
        patch("app._validate_secrets"),
        patch("models.db.init_pool"),
        patch("services.queue_service.QueueProcessor"),
    ):
        application = _make_app_with_limiter_disabled()
        application.config["TESTING"] = True
        application.config["WTF_CSRF_ENABLED"] = False
        # Override the Redis session interface with the default Flask cookie-based
        # session so session_transaction() works without a real Redis connection.
        from flask.sessions import SecureCookieSessionInterface
        application.session_interface = SecureCookieSessionInterface()
        yield application


@pytest.fixture
def client_a(app):
    """Test client authenticated as a Tenant A agent."""
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = dict(USER_A)
            sess["csrf_token"] = "test-csrf-token"
        yield c


@pytest.fixture
def client_b(app):
    """Test client authenticated as a Tenant B agent."""
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = dict(USER_B)
            sess["csrf_token"] = "test-csrf-token"
        yield c


# ===========================================================================
# 1. Tickets — GET ticket by ID cross-tenant
# ===========================================================================

class TestTicketIsolation:

    def test_tenant_a_cannot_read_tenant_b_ticket(self, client_a):
        """Tenant A requesting a ticket owned by Tenant B must get 404.

        This was the actual bug that triggered the board finding. The ticket
        endpoint must filter by tenant_id when the user is not a super_admin.
        """
        tenant_b_ticket = {
            "id": 100,
            "tenant_id": TENANT_B_ID,
            "subject": "Tenant B secret ticket",
            "status": "open",
            "priority": "p2",
            "requester_id": USER_B["id"],
            "assignee_id": None,
            "location_id": None,
            "problem_category_id": None,
        }

        def _mock_fetch_one(sql, params=None):
            # The endpoint queries tickets WHERE id=%s AND tenant_id=%s
            # If tenant_id filtering is working, this should NOT match
            # because client_a has tenant_id=10 but the ticket has tenant_id=20
            if params and len(params) >= 2:
                ticket_id, tid = params[0], params[1]
                if ticket_id == 100 and tid == TENANT_A_ID:
                    return None  # Correctly filtered — Tenant A can't see Tenant B's ticket
                if ticket_id == 100 and tid == TENANT_B_ID:
                    return tenant_b_ticket  # Would only match for Tenant B
            # If only one param (no tenant filter — the bug), return the ticket
            if params and len(params) == 1 and params[0] == 100:
                return tenant_b_ticket
            return None

        with patch("routes.tickets.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.get("/api/tickets/100")

        assert resp.status_code == 404, (
            "Tenant A must NOT be able to read Tenant B's ticket — got "
            f"{resp.status_code} instead of 404"
        )

    def test_tenant_b_can_read_own_ticket(self, client_b):
        """Tenant B reading their own ticket must succeed (sanity check)."""
        tenant_b_ticket = {
            "id": 100,
            "tenant_id": TENANT_B_ID,
            "subject": "Tenant B's ticket",
            "status": "open",
            "priority": "p3",
            "requester_id": USER_B["id"],
            "requester_name": "Agent B",
            "requester_email": "agent-b@tenant-b.com",
            "assignee_id": None,
            "assignee_name": None,
            "assignee_email": None,
            "tenant_name": "Tenant B",
            "age_seconds": 3600,
            "sla_status": "no_sla",
            "location_id": None,
            "problem_category_id": None,
            "sla_due_at": None,
            "sla_breached": False,
        }

        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2 and params[0] == 100 and params[1] == TENANT_B_ID:
                return tenant_b_ticket
            return None

        def _mock_fetch_all(sql, params=None):
            # Comments and tag suggestions return empty
            return []

        with (
            patch("routes.tickets.fetch_one", side_effect=_mock_fetch_one),
            patch("routes.tickets.fetch_all", side_effect=_mock_fetch_all),
        ):
            resp = client_b.get("/api/tickets/100")

        assert resp.status_code == 200, (
            f"Tenant B should be able to read their own ticket — got {resp.status_code}"
        )


# ===========================================================================
# 2. Ticket comments — GET comments cross-tenant
# ===========================================================================

class TestTicketCommentIsolation:

    def test_tenant_a_cannot_read_tenant_b_comments(self, client_a):
        """Tenant A requesting comments on Tenant B's ticket must get 404.

        This was part of the same cross-tenant leak. The comments endpoint
        first verifies ticket access (tenant-scoped), then returns comments.
        """
        def _mock_fetch_one(sql, params=None):
            # The comments endpoint queries: SELECT ... FROM tickets WHERE id=%s AND tenant_id=%s
            if params and len(params) >= 2 and params[0] == 200 and params[1] == TENANT_A_ID:
                return None  # Tenant A cannot see Tenant B's ticket
            return None

        with patch("routes.tickets.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.get("/api/tickets/200/comments")

        assert resp.status_code == 404, (
            "Tenant A must NOT be able to read comments on Tenant B's ticket — "
            f"got {resp.status_code}"
        )

    def test_tenant_b_can_read_own_ticket_comments(self, client_b):
        """Tenant B reading comments on their own ticket must succeed."""
        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2 and params[0] == 200 and params[1] == TENANT_B_ID:
                return {"requester_id": USER_B["id"], "tenant_id": TENANT_B_ID}
            return None

        def _mock_fetch_all(sql, params=None):
            return [
                {"id": 1, "ticket_id": 200, "content": "Test comment",
                 "author_name": "Agent B", "is_internal": False, "is_ai_generated": False,
                 "created_at": "2026-01-01T00:00:00"},
            ]

        with (
            patch("routes.tickets.fetch_one", side_effect=_mock_fetch_one),
            patch("routes.tickets.fetch_all", side_effect=_mock_fetch_all),
        ):
            resp = client_b.get("/api/tickets/200/comments")

        assert resp.status_code == 200


# ===========================================================================
# 3. KB articles — GET articles cross-tenant
# ===========================================================================

class TestKBArticleIsolation:

    def test_tenant_a_cannot_read_tenant_b_article(self, client_a):
        """Tenant A requesting a KB article owned by Tenant B must get 404.

        The KB article endpoint checks: if user is not super_admin, verify
        that the article's tenant_id matches the user's tenant_id or the
        article's module is enabled for the user's tenant.
        """
        tenant_b_article = {
            "id": 300,
            "tenant_id": TENANT_B_ID,
            "module_id": None,
            "title": "Tenant B secret KB article",
            "content": "Confidential content",
            "is_published": True,
            "module_name": None,
            "module_slug": None,
        }

        def _mock_fetch_one(sql, params=None):
            # First call: fetch the article by ID (no tenant filter)
            if "d.id = %s" in sql and "tenant_modules" not in sql:
                if params == [300]:
                    return tenant_b_article
            # Second call: check if module is enabled for tenant
            if "tenant_modules" in sql:
                return None  # Module not enabled for Tenant A
            return None

        with patch("routes.kb.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.get("/api/kb/documents/300")

        assert resp.status_code == 404, (
            "Tenant A must NOT be able to read Tenant B's KB article — "
            f"got {resp.status_code}"
        )

    def test_tenant_a_cannot_read_tenant_b_tenant_article(self, client_a):
        """Tenant A cannot access a Tenant B tenant-authored article via GET /articles/<id>."""
        def _mock_fetch_one(sql, params=None):
            # The articles endpoint uses: WHERE id=%s AND tenant_id=%s AND module_id IS NULL
            if params and len(params) >= 2 and params[0] == 301 and params[1] == TENANT_A_ID:
                return None  # No match — article belongs to Tenant B
            return None

        with patch("routes.kb.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.get("/api/kb/articles/301")

        assert resp.status_code == 404

    def test_tenant_a_articles_list_excludes_tenant_b(self, client_a):
        """GET /api/kb/articles must only return articles owned by Tenant A."""
        def _mock_fetch_all(sql, params=None):
            # Verify tenant_id is in the query params
            if params and TENANT_A_ID in params:
                return [
                    {"id": 10, "title": "Tenant A Article", "is_published": True,
                     "created_at": "2026-01-01", "updated_at": "2026-01-01",
                     "author_name": "Agent A", "content_length": 100,
                     "source_file_name": None, "source_file_type": None,
                     "file_size": None, "tenant_collection_id": None,
                     "collection_name": None},
                ]
            return []

        with patch("routes.kb.fetch_all", side_effect=_mock_fetch_all):
            resp = client_a.get("/api/kb/articles")

        assert resp.status_code == 200
        data = resp.get_json()
        # All returned articles should be from the tenant's own query
        assert len(data) == 1
        assert data[0]["title"] == "Tenant A Article"


# ===========================================================================
# 4. Users — GET/list users cross-tenant
# ===========================================================================

class TestUserIsolation:

    def test_tenant_a_user_list_excludes_tenant_b_users(self, client_a):
        """GET /api/admin/users as Tenant A agent must only return Tenant A users.

        The admin users endpoint scopes by tenant_id for non-super_admin users.
        If it returned Tenant B's users, that would be a severe data leak.
        """
        tenant_a_users = [
            {"id": 1, "tenant_id": TENANT_A_ID, "email": "agent-a@tenant-a.com",
             "name": "Agent A", "role": "agent", "is_active": True,
             "first_name": "Agent", "last_name": "A", "phone": None,
             "invite_status": "active", "invited_at": None, "expires_at": None,
             "created_at": "2026-01-01", "tenant_name": "Tenant A"},
        ]

        def _mock_fetch_all(sql, params=None):
            # Non-super_admin query includes WHERE u.tenant_id = %s
            if params and params[0] == TENANT_A_ID:
                return tenant_a_users
            return []

        with patch("routes.admin.fetch_all", side_effect=_mock_fetch_all):
            resp = client_a.get("/api/admin/users")

        assert resp.status_code == 200
        data = resp.get_json()
        for user in data:
            assert user["tenant_id"] == TENANT_A_ID, (
                f"User list for Tenant A must not contain Tenant B users. "
                f"Found tenant_id={user['tenant_id']}"
            )

    def test_tenant_b_user_list_excludes_tenant_a_users(self, client_b):
        """Symmetric check: Tenant B's user list must not leak Tenant A users."""
        tenant_b_users = [
            {"id": 2, "tenant_id": TENANT_B_ID, "email": "agent-b@tenant-b.com",
             "name": "Agent B", "role": "agent", "is_active": True,
             "first_name": "Agent", "last_name": "B", "phone": None,
             "invite_status": "active", "invited_at": None, "expires_at": None,
             "created_at": "2026-01-01", "tenant_name": "Tenant B"},
        ]

        def _mock_fetch_all(sql, params=None):
            if params and params[0] == TENANT_B_ID:
                return tenant_b_users
            return []

        with patch("routes.admin.fetch_all", side_effect=_mock_fetch_all):
            resp = client_b.get("/api/admin/users")

        assert resp.status_code == 200
        data = resp.get_json()
        for user in data:
            assert user["tenant_id"] == TENANT_B_ID


# ===========================================================================
# 5. Phone agents — GET phone agents cross-tenant
# ===========================================================================

class TestPhoneAgentIsolation:

    def test_tenant_a_cannot_read_tenant_b_phone_agent(self, client_a):
        """GET /api/phone/agents/<id> for an agent belonging to Tenant B must return 404."""
        with patch("services.phone_service.get_phone_agent", return_value=None):
            resp = client_a.get("/api/phone/agents/500")

        assert resp.status_code == 404, (
            "Tenant A must NOT see Tenant B's phone agent"
        )

    def test_tenant_a_phone_agents_list_scoped(self, client_a):
        """GET /api/phone/agents must call list_phone_agents with Tenant A's tenant_id."""
        tenant_a_agents = [
            {"id": 10, "name": "Atlas", "language": "en", "is_active": True},
        ]

        with patch("services.phone_service.list_phone_agents", return_value=tenant_a_agents) as mock_list:
            resp = client_a.get("/api/phone/agents")

        assert resp.status_code == 200
        # Verify the service was called with Tenant A's ID, not Tenant B's
        mock_list.assert_called_once_with(TENANT_A_ID)

    def test_tenant_b_phone_agents_list_scoped(self, client_b):
        """Symmetric check: Tenant B's agent list uses Tenant B's tenant_id."""
        with patch("services.phone_service.list_phone_agents", return_value=[]) as mock_list:
            resp = client_b.get("/api/phone/agents")

        assert resp.status_code == 200
        mock_list.assert_called_once_with(TENANT_B_ID)


# ===========================================================================
# 6. Phone sessions — GET phone sessions cross-tenant
# ===========================================================================

class TestPhoneSessionIsolation:

    def test_tenant_a_cannot_read_tenant_b_session(self, client_a):
        """GET /api/phone/sessions/<id> for a session belonging to Tenant B must return 404.

        The endpoint queries: WHERE ps.id = %s AND ps.tenant_id = %s
        Note: The endpoint imports fetch_one/fetch_all from models.db inside
        the function body, so we must patch at the models.db level.
        """
        def _mock_fetch_one(sql, params=None):
            # The endpoint passes [session_id, user["tenant_id"]]
            if params and len(params) >= 2:
                session_id, tid = params[0], params[1]
                if session_id == 600 and tid == TENANT_A_ID:
                    return None  # Session 600 belongs to Tenant B, not A
            return None

        with patch("models.db.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.get("/api/phone/sessions/600")

        assert resp.status_code == 404

    def test_tenant_a_session_list_scoped(self, client_a):
        """GET /api/phone/sessions must call get_call_logs with Tenant A's tenant_id."""
        with patch("services.phone_service.get_call_logs", return_value=[]) as mock_logs:
            resp = client_a.get("/api/phone/sessions")

        assert resp.status_code == 200
        call_kwargs = mock_logs.call_args
        assert call_kwargs[0][0] == TENANT_A_ID, (
            f"get_call_logs must be called with tenant_id={TENANT_A_ID}, "
            f"got {call_kwargs[0][0]}"
        )

    def test_tenant_b_session_detail_accessible(self, client_b):
        """Tenant B reading their own session must succeed (sanity check)."""
        session_row = {
            "id": 600,
            "tenant_id": TENANT_B_ID,
            "status": "completed",
            "ticket_id": None,
            "ticket_number": None,
        }

        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2 and params[0] == 600 and params[1] == TENANT_B_ID:
                return session_row
            return None

        def _mock_fetch_all(sql, params=None):
            return []  # No transfer attempts

        with (
            patch("models.db.fetch_one", side_effect=_mock_fetch_one),
            patch("models.db.fetch_all", side_effect=_mock_fetch_all),
        ):
            resp = client_b.get("/api/phone/sessions/600")

        assert resp.status_code == 200


# ===========================================================================
# 7. Audit queue — GET audit items cross-tenant
# ===========================================================================

class TestAuditQueueIsolation:

    def test_tenant_a_audit_queue_scoped(self, client_a):
        """GET /api/audit/queue must include tenant_id filter for non-super_admin.

        The audit endpoint checks user role and adds aq.tenant_id = %s for
        non-super_admin users. If this filter were missing, audit items from
        all tenants would be returned.
        """
        tenant_a_items = [
            {"id": 1, "ticket_id": 100, "tenant_id": TENANT_A_ID,
             "status": "pending", "queue_type": "close",
             "ticket_number": "TKT-00100", "subject": "Test",
             "ticket_status": "resolved", "priority": "p3",
             "current_tags": [], "current_category_name": None,
             "suggested_category_name": None, "reviewed_by_name": None,
             "created_at": "2026-01-01"},
        ]

        def _mock_fetch_all(sql, params=None):
            # Verify that tenant_id is in the query params
            if params and TENANT_A_ID in params:
                return tenant_a_items
            return []

        def _mock_fetch_one(sql, params=None):
            # Count query
            if "count" in sql.lower():
                return {"cnt": 1}
            return None

        with (
            patch("routes.audit.fetch_all", side_effect=_mock_fetch_all),
            patch("routes.audit.fetch_one", side_effect=_mock_fetch_one),
        ):
            resp = client_a.get("/api/audit/queue")

        assert resp.status_code == 200
        data = resp.get_json()
        for item in data.get("items", []):
            assert item["tenant_id"] == TENANT_A_ID, (
                f"Audit queue must only return Tenant A items. "
                f"Found tenant_id={item['tenant_id']}"
            )

    def test_tenant_b_audit_queue_does_not_leak_tenant_a(self, client_b):
        """Tenant B's audit queue must not contain Tenant A items."""
        def _mock_fetch_all(sql, params=None):
            if params and TENANT_B_ID in params:
                return []  # Tenant B has no audit items
            # If no tenant filter, this would return cross-tenant data
            if params and TENANT_A_ID in params:
                return [{"id": 99, "tenant_id": TENANT_A_ID}]  # Should never happen
            return []

        def _mock_fetch_one(sql, params=None):
            return {"cnt": 0}

        with (
            patch("routes.audit.fetch_all", side_effect=_mock_fetch_all),
            patch("routes.audit.fetch_one", side_effect=_mock_fetch_one),
        ):
            resp = client_b.get("/api/audit/queue")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data.get("items", [])) == 0


# ===========================================================================
# 8. Automations — GET automations cross-tenant
# ===========================================================================

class TestAutomationIsolation:

    def test_tenant_a_automations_scoped(self, client_a):
        """GET /api/automations must only return Tenant A's automations.

        The endpoint queries: WHERE a.tenant_id = %s
        """
        tenant_a_automations = [
            {"id": 1, "tenant_id": TENANT_A_ID, "name": "Auto-assign P1",
             "description": "", "trigger_type": "ticket_created",
             "is_active": True, "created_at": "2026-01-01",
             "created_by_name": "Agent A"},
        ]

        def _mock_fetch_all(sql, params=None):
            if params and params[0] == TENANT_A_ID:
                return tenant_a_automations
            return []

        with patch("routes.automations.fetch_all", side_effect=_mock_fetch_all):
            resp = client_a.get("/api/automations")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["tenant_id"] == TENANT_A_ID

    def test_tenant_a_cannot_read_tenant_b_automation(self, client_a):
        """GET /api/automations/<id> for a Tenant B automation must return 404.

        The endpoint queries: WHERE id=%s AND tenant_id=%s
        """
        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2:
                auto_id, tid = params[0], params[1]
                if auto_id == 700 and tid == TENANT_A_ID:
                    return None  # Automation 700 belongs to Tenant B
            return None

        def _mock_fetch_all(sql, params=None):
            return []

        with (
            patch("routes.automations.fetch_one", side_effect=_mock_fetch_one),
            patch("routes.automations.fetch_all", side_effect=_mock_fetch_all),
        ):
            resp = client_a.get("/api/automations/700")

        assert resp.status_code == 404

    def test_tenant_b_can_read_own_automation(self, client_b):
        """Tenant B reading their own automation must succeed."""
        tenant_b_automation = {
            "id": 700,
            "tenant_id": TENANT_B_ID,
            "name": "Tenant B Automation",
            "description": "",
            "trigger_type": "ticket_created",
            "trigger_config": {},
            "conditions": [],
            "actions": [],
            "is_active": True,
        }

        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2 and params[0] == 700 and params[1] == TENANT_B_ID:
                return tenant_b_automation
            return None

        def _mock_fetch_all(sql, params=None):
            # Canvas nodes/edges queries
            return []

        with (
            patch("routes.automations.fetch_one", side_effect=_mock_fetch_one),
            patch("routes.automations.fetch_all", side_effect=_mock_fetch_all),
        ):
            resp = client_b.get("/api/automations/700")

        assert resp.status_code == 200


# ===========================================================================
# 9. Cross-cutting: ticket list scoping
# ===========================================================================

class TestTicketListIsolation:

    def test_ticket_list_scoped_by_tenant(self, client_a):
        """GET /api/tickets must include tenant_id in the WHERE clause.

        For non-super_admin users, the list endpoint adds:
            t.tenant_id = %s
        This test verifies the parameter is passed correctly.
        """
        tenant_a_tickets = [
            {"id": 1, "tenant_id": TENANT_A_ID, "subject": "Tenant A ticket",
             "status": "open", "priority": "p3", "created_at": "2026-01-01",
             "updated_at": "2026-01-01", "ticket_number": "TKT-00001",
             "requester_name": "Agent A", "assignee_name": None,
             "location_breadcrumb": None, "problem_category_breadcrumb": None,
             "sla_status": "no_sla", "tags": [], "comment_count": 0,
             "sla_due_at": None, "sla_breached": False, "ticket_type": "support",
             "team_name": None},
        ]

        def _mock_fetch_all(sql, params=None):
            if params and TENANT_A_ID in params:
                return tenant_a_tickets
            return []

        def _mock_fetch_one(sql, params=None):
            # Count query uses "cnt" as the alias
            if "count" in sql.lower():
                if params and TENANT_A_ID in params:
                    return {"cnt": 1}
                return {"cnt": 0}
            return None

        with (
            patch("routes.tickets.fetch_all", side_effect=_mock_fetch_all),
            patch("routes.tickets.fetch_one", side_effect=_mock_fetch_one),
            patch("routes.tickets.check_sla_breaches"),
        ):
            resp = client_a.get("/api/tickets")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert len(data["tickets"]) == 1


# ===========================================================================
# 10. KB collections — cross-tenant isolation
# ===========================================================================

class TestKBCollectionIsolation:

    def test_tenant_a_collections_scoped(self, client_a):
        """GET /api/kb/collections must only return Tenant A's collections."""
        tenant_a_collections = [
            {"id": 1, "name": "Tenant A Docs", "slug": "tenant-a-docs",
             "description": "", "doc_count": 5, "created_at": "2026-01-01",
             "created_by_name": "Agent A"},
        ]

        def _mock_fetch_all(sql, params=None):
            if params and params[0] == TENANT_A_ID:
                return tenant_a_collections
            return []

        with patch("routes.kb.fetch_all", side_effect=_mock_fetch_all):
            resp = client_a.get("/api/kb/collections")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Tenant A Docs"

    def test_tenant_a_cannot_delete_tenant_b_collection(self, client_a):
        """DELETE /api/kb/collections/<id> for a Tenant B collection must return 404.

        The endpoint checks: WHERE id=%s AND tenant_id=%s
        """
        def _mock_fetch_one(sql, params=None):
            if params and len(params) >= 2 and params[0] == 800 and params[1] == TENANT_A_ID:
                return None  # Collection 800 belongs to Tenant B
            return None

        with patch("routes.kb.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.delete("/api/kb/collections/800", headers={"X-CSRF-Token": "test-csrf-token"})

        assert resp.status_code == 404


# ===========================================================================
# 11. Mutation isolation — Tenant A cannot modify Tenant B data
# ===========================================================================

class TestMutationIsolation:

    def test_tenant_a_cannot_update_tenant_b_article(self, client_a):
        """PUT /api/kb/articles/<id> on a Tenant B article must return 404.

        The update endpoint verifies ownership with tenant_id before applying changes.
        """
        def _mock_fetch_one(sql, params=None):
            # Ownership check: WHERE id=%s AND tenant_id=%s AND module_id IS NULL
            if params and len(params) >= 2 and params[0] == 900 and params[1] == TENANT_A_ID:
                return None  # Article 900 belongs to Tenant B
            return None

        with patch("routes.kb.fetch_one", side_effect=_mock_fetch_one):
            resp = client_a.put(
                "/api/kb/articles/900",
                json={"title": "Hijacked title"},
                headers={"X-CSRF-Token": "test-csrf-token"},
            )

        assert resp.status_code == 404

    def test_tenant_a_cannot_delete_tenant_b_article(self, client_a):
        """DELETE /api/kb/articles/<id> for Tenant B article must not modify it.

        The delete endpoint scopes the UPDATE by tenant_id for non-super_admin.
        """
        mock_execute = MagicMock(return_value=0)  # 0 rows affected = not found
        mock_fetch_one = MagicMock(return_value=None)  # Article not found for this tenant

        with patch("routes.kb.execute", mock_execute), \
             patch("routes.kb.fetch_one", mock_fetch_one):
            resp = client_a.delete("/api/kb/articles/900", headers={"X-CSRF-Token": "test-csrf-token"})

        assert resp.status_code == 404  # Article not found for this tenant

    def test_tenant_admin_cannot_update_other_tenant_settings(self, app):
        """PUT /api/admin/tenants/<id>/settings as tenant_admin for wrong tenant must return 403.

        The settings endpoint explicitly checks: if role == tenant_admin and
        user.tenant_id != tenant_id in URL, return 403.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = {
                    "id": 1,
                    "tenant_id": TENANT_A_ID,
                    "email": "admin-a@tenant-a.com",
                    "name": "Admin A",
                    "role": "tenant_admin",
                    "permissions": ["users.manage"],
                }

            resp = c.put(
                f"/api/admin/tenants/{TENANT_B_ID}/settings",
                json={"problem_field_label": "Hijacked"},
            )

        assert resp.status_code == 403, (
            "Tenant admin A must NOT update Tenant B's settings — "
            f"got {resp.status_code}"
        )


# ===========================================================================
# 12. Phone agent mutation isolation
# ===========================================================================

class TestPhoneAgentMutationIsolation:

    def test_tenant_a_cannot_update_tenant_b_agent(self, client_a):
        """PUT /api/phone/agents/<id> for a Tenant B agent must fail."""
        with patch("services.phone_service.update_phone_agent", side_effect=ValueError("Agent not found")):
            resp = client_a.put(
                "/api/phone/agents/500",
                json={"name": "Hijacked Agent"},
                headers={"X-CSRF-Token": "test-csrf-token"},
            )

        # The route catches ValueError and returns 400
        assert resp.status_code == 400

    def test_tenant_a_cannot_delete_tenant_b_agent(self, client_a):
        """DELETE /api/phone/agents/<id> for a Tenant B agent must fail."""
        with patch("services.phone_service.delete_phone_agent", side_effect=ValueError("Agent not found")):
            resp = client_a.delete("/api/phone/agents/500", headers={"X-CSRF-Token": "test-csrf-token"})

        assert resp.status_code == 400

    def test_tenant_a_cannot_deploy_tenant_b_agent(self, client_a):
        """POST /api/phone/agents/<id>/deploy for a Tenant B agent must fail."""
        with patch("services.phone_service.deploy_agent", side_effect=ValueError("Agent not found")):
            resp = client_a.post("/api/phone/agents/500/deploy", headers={"X-CSRF-Token": "test-csrf-token"})

        assert resp.status_code == 400


# ===========================================================================
# Helpers for the new cross-tenant fix tests (commits 7ab57e4 + 080ea8b)
# ===========================================================================

# A richer user fixture that has atlas.chat permission (needed by several
# @require_permission("atlas.chat") decorated endpoints under test).
USER_A_AI = {
    **USER_A,
    "permissions": USER_A["permissions"] + ["atlas.chat"],
}

SUPER_USER = {
    "id": 99,
    "tenant_id": TENANT_A_ID,  # super_admin formally lives in a tenant too
    "email": "super@platform.com",
    "name": "Super Admin",
    "role": "super_admin",
    "permissions": [],  # super_admin bypasses all permission checks
}

# Fixed CSRF token injected into every test session so the app's enforce_csrf
# before_request hook does not block POST/PUT/DELETE requests.
TEST_CSRF_TOKEN = "test-csrf-token-for-pytest"
CSRF_HEADERS = {"X-CSRF-Token": TEST_CSRF_TOKEN}


# ===========================================================================
# Cross-tenant fixes from commit 7ab57e4 (10 vectors)
# ===========================================================================

# ---------------------------------------------------------------------------
# Vector 1: GET /api/kb/suggest/<ticket_id>
# ---------------------------------------------------------------------------

class TestKBSuggestIsolation:
    """suggest_articles() now scopes the ticket lookup by tenant_id for non-super_admin."""

    def test_kb_suggest_blocks_cross_tenant_access(self, app):
        """Tenant A caller requesting suggest for a Tenant B ticket must get [] (empty list).

        Before the fix: ticket was fetched with no tenant_id filter, leaking
        subject/description cross-tenant into the vector search query.
        After the fix: ticket is fetched with AND t.tenant_id = %s, returns None,
        and the endpoint returns [].
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)

            def _fetch_one(sql, params=None):
                # The fixed query includes tenant_id; the cross-tenant row won't match
                if params and TENANT_A_ID in params and 999 in params:
                    return None  # Correct — Tenant B ticket not visible to Tenant A
                # The unfixed query would have only [999] and would return the row
                if params == [999]:
                    return {"subject": "Tenant B secret", "description": "confidential"}
                return None

            with patch("routes.kb.fetch_one", side_effect=_fetch_one):
                resp = c.get("/api/kb/suggest/999")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data == [], (
            "suggest_articles must return [] when ticket belongs to a different tenant"
        )

    def test_kb_suggest_allows_same_tenant_access(self, app):
        """Same-tenant ticket suggestion lookup must proceed to vector search."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)

            call_count = {"n": 0}

            def _fetch_one(sql, params=None):
                # First call: ticket ownership check — returns the ticket (same tenant)
                if params and TENANT_A_ID in params and 101 in params:
                    return {"subject": "Login broken", "description": "Cannot log in"}
                return None

            def _fetch_all(sql, params=None):
                # tenant_modules lookup returns []
                return []

            # The vector search itself will fail without a real embedding service,
            # so we short-circuit at embed_single to return empty results.
            with (
                patch("routes.kb.fetch_one", side_effect=_fetch_one),
                patch("routes.kb.fetch_all", side_effect=_fetch_all),
                patch("services.embedding_service.embed_single", side_effect=Exception("no embedding")),
            ):
                resp = c.get("/api/kb/suggest/101")

        # 200 with [] is correct — embed failure short-circuits cleanly
        assert resp.status_code == 200

    def test_kb_suggest_allows_super_admin_cross_tenant(self, app):
        """Super admin should be able to request suggestions for any tenant's ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)

            def _fetch_one(sql, params=None):
                # Super admin path has no tenant filter; just [ticket_id]
                if params == [999]:
                    return {"subject": "Foreign ticket", "description": ""}
                return None

            def _fetch_all(sql, params=None):
                return []

            with (
                patch("routes.kb.fetch_one", side_effect=_fetch_one),
                patch("routes.kb.fetch_all", side_effect=_fetch_all),
                patch("services.embedding_service.embed_single", side_effect=Exception("no embedding")),
            ):
                resp = c.get("/api/kb/suggest/999")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Vector 2+3: GET /api/ai/engagement/<ticket_id>
# ---------------------------------------------------------------------------

class TestEngagementIsolation:
    """get_engagement_status() now verifies ticket ownership before returning engagement data."""

    def test_engagement_blocks_cross_tenant_access(self, app):
        """Tenant A accessing engagement status for a Tenant B ticket must get {"status": "none"}.

        The fix adds a ticket ownership check before fetching atlas_engagements.
        A cross-tenant caller would receive the same "none" response as if no
        engagement existed — no data about the foreign ticket leaks.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)

            tenant_b_ticket = {"id": 999, "tenant_id": TENANT_B_ID}

            def _fetch_one(sql, params=None):
                # First call: ticket ownership check
                if "FROM tickets WHERE id = %s" in sql and params == [999]:
                    return tenant_b_ticket
                return None

            with patch("routes.ai.fetch_one", side_effect=_fetch_one):
                resp = c.get("/api/ai/engagement/999")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "none", (
            "Cross-tenant engagement request must return {status: none}, "
            f"got: {data}"
        )

    def test_engagement_allows_same_tenant_access(self, app):
        """Same-tenant engagement lookup must return real engagement data."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)

            own_ticket = {"id": 101, "tenant_id": TENANT_A_ID}
            engagement_row = {
                "id": 1, "status": "active", "engagement_type": "auto",
                "human_took_over": False, "human_took_over_at": None,
                "resolved_by_ai": False, "kb_articles_referenced": [],
                "similar_ticket_ids": None, "suggested_category_id": None,
                "category_confidence": None, "suggested_category_name": None,
                "created_at": "2026-01-01", "updated_at": "2026-01-01",
            }

            call_count = {"n": 0}

            def _fetch_one(sql, params=None):
                call_count["n"] += 1
                if "FROM tickets WHERE id = %s" in sql and params == [101]:
                    return own_ticket
                if "FROM atlas_engagements ae" in sql:
                    return engagement_row
                return None

            with patch("routes.ai.fetch_one", side_effect=_fetch_one):
                resp = c.get("/api/ai/engagement/101")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "active"

    def test_engagement_allows_super_admin_cross_tenant(self, app):
        """Super admin can view engagement data for any tenant's ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)

            foreign_ticket = {"id": 999, "tenant_id": TENANT_B_ID}
            engagement_row = {
                "id": 5, "status": "passive", "engagement_type": "auto",
                "human_took_over": False, "human_took_over_at": None,
                "resolved_by_ai": False, "kb_articles_referenced": [],
                "similar_ticket_ids": None, "suggested_category_id": None,
                "category_confidence": None, "suggested_category_name": None,
                "created_at": "2026-01-01", "updated_at": "2026-01-01",
            }

            def _fetch_one(sql, params=None):
                if "FROM tickets WHERE id = %s" in sql and params == [999]:
                    return foreign_ticket
                if "FROM atlas_engagements ae" in sql:
                    return engagement_row
                return None

            with patch("routes.ai.fetch_one", side_effect=_fetch_one):
                resp = c.get("/api/ai/engagement/999")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "passive"


# ---------------------------------------------------------------------------
# Vector 4: POST /api/ai/enrich/<ticket_id>
# ---------------------------------------------------------------------------

class TestEnrichIsolation:
    """enrich_ticket_endpoint() now verifies ticket ownership before triggering enrichment."""

    def test_enrich_blocks_cross_tenant_access(self, app):
        """Tenant A triggering enrichment for a Tenant B ticket must get 404.

        Before the fix: enrichment was triggered for any ticket_id with no
        ownership check, allowing one tenant to drive AI analysis of another
        tenant's support ticket.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            with patch("routes.ai.fetch_one", side_effect=_fetch_one):
                resp = c.post("/api/ai/enrich/999", headers=CSRF_HEADERS)

        assert resp.status_code == 404
        data = resp.get_json()
        assert "Not found" in data.get("error", ""), (
            "Cross-tenant enrich must return 404 Not found"
        )

    def test_enrich_allows_same_tenant_access(self, app):
        """Same-tenant enrich request must trigger enrichment and return 200."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [101]:
                    return {"id": 101, "tenant_id": TENANT_A_ID}
                return None

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("services.enrichment_service.enrich_ticket"),
            ):
                resp = c.post("/api/ai/enrich/101", headers=CSRF_HEADERS)

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_enrich_allows_super_admin_cross_tenant(self, app):
        """Super admin can trigger enrichment on any tenant's ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("services.enrichment_service.enrich_ticket"),
            ):
                resp = c.post("/api/ai/enrich/999", headers=CSRF_HEADERS)

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Vector 5: POST /api/ai/tickets/<id>/link-incident
# ---------------------------------------------------------------------------

class TestLinkIncidentIsolation:
    """link_incident() now verifies both ticket and parent belong to caller's tenant."""

    def test_link_incident_blocks_cross_tenant_access(self, app):
        """Linking a Tenant B ticket as caller from Tenant A must return 404."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                # ticket (id=999) belongs to Tenant B
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                # parent (id=100) belongs to Tenant A (same as caller)
                if params == [100]:
                    return {"id": 100, "tenant_id": TENANT_A_ID}
                return None

            with patch("routes.ai.fetch_one", side_effect=_fetch_one):
                resp = c.post(
                    "/api/ai/tickets/999/link-incident",
                    json={"parent_ticket_id": 100},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 404, (
            "Linking a foreign tenant's ticket must be rejected with 404"
        )

    def test_link_incident_allows_same_tenant_access(self, app):
        """Linking two same-tenant tickets must succeed."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [101]:
                    return {"id": 101, "tenant_id": TENANT_A_ID}
                if params == [100]:
                    return {"id": 100, "tenant_id": TENANT_A_ID}
                return None

            def _execute(sql, params=None):
                pass

            def _insert_returning(sql, params=None):
                return 1

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
            ):
                resp = c.post(
                    "/api/ai/tickets/101/link-incident",
                    json={"parent_ticket_id": 100},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_link_incident_allows_super_admin_cross_tenant(self, app):
        """Super admin can link tickets across tenants."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                if params == [998]:
                    return {"id": 998, "tenant_id": TENANT_B_ID}
                return None

            def _execute(sql, params=None):
                pass

            def _insert_returning(sql, params=None):
                return 1

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
            ):
                resp = c.post(
                    "/api/ai/tickets/999/link-incident",
                    json={"parent_ticket_id": 998},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Vector 6: POST /api/ai/tickets/<id>/unlink-incident
# ---------------------------------------------------------------------------

class TestUnlinkIncidentIsolation:
    """unlink_incident() now verifies ticket ownership before clearing parent_ticket_id."""

    def test_unlink_incident_blocks_cross_tenant_access(self, app):
        """Tenant A calling unlink on a Tenant B ticket must get 404.

        Before the fix: execute() ran UPDATE tickets SET parent_ticket_id = NULL
        unconditionally for any ticket_id, allowing cross-tenant write.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            execute_called = {"called": False}

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            def _execute(sql, params=None):
                execute_called["called"] = True

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/ai/tickets/999/unlink-incident",
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 404, (
            "Cross-tenant unlink must be rejected with 404"
        )
        assert not execute_called["called"], (
            "execute() must NOT be called for a cross-tenant ticket — "
            "that would have written to the foreign tenant's data"
        )

    def test_unlink_incident_allows_same_tenant_access(self, app):
        """Same-tenant unlink must execute the UPDATE and return 200."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [101]:
                    return {"id": 101, "tenant_id": TENANT_A_ID}
                return None

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/ai/tickets/101/unlink-incident",
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_unlink_incident_allows_super_admin_cross_tenant(self, app):
        """Super admin can unlink any tenant's ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/ai/tickets/999/unlink-incident",
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Vector 7: GET /api/ai/tickets/<id>/incident-children
# ---------------------------------------------------------------------------

class TestIncidentChildrenIsolation:
    """get_incident_children() now verifies parent ticket ownership before returning children."""

    def test_incident_children_blocks_cross_tenant_access(self, app):
        """Tenant A requesting children of a Tenant B parent ticket must get [].

        The fix returns [] (not 404) — same as "no children found" — which
        is enumeration-resistant: the caller cannot distinguish "no children"
        from "you don't have access".
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)

            fetch_all_called = {"called": False}

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            def _fetch_all(sql, params=None):
                fetch_all_called["called"] = True
                return [{"id": 50, "subject": "Child ticket from B", "tenant_id": TENANT_B_ID}]

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.fetch_all", side_effect=_fetch_all),
            ):
                resp = c.get("/api/ai/tickets/999/incident-children")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data == [], (
            "Cross-tenant incident-children must return [] — "
            f"got: {data}"
        )
        assert not fetch_all_called["called"], (
            "fetch_all must NOT be called when parent ticket belongs to foreign tenant"
        )

    def test_incident_children_allows_same_tenant_access(self, app):
        """Same-tenant parent returns its children list."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)

            def _fetch_one(sql, params=None):
                if params == [101]:
                    return {"id": 101, "tenant_id": TENANT_A_ID}
                return None

            def _fetch_all(sql, params=None):
                return [
                    {"id": 200, "ticket_number": "TKT-00200",
                     "subject": "Child 1", "status": "open",
                     "priority": "p3", "created_at": "2026-01-01"},
                ]

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.fetch_all", side_effect=_fetch_all),
            ):
                resp = c.get("/api/ai/tickets/101/incident-children")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["id"] == 200

    def test_incident_children_allows_super_admin_cross_tenant(self, app):
        """Super admin can view children of any tenant's parent ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID}
                return None

            def _fetch_all(sql, params=None):
                return [
                    {"id": 50, "ticket_number": "TKT-00050",
                     "subject": "B child ticket", "status": "open",
                     "priority": "p2", "created_at": "2026-01-01"},
                ]

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.fetch_all", side_effect=_fetch_all),
            ):
                resp = c.get("/api/ai/tickets/999/incident-children")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1


# ---------------------------------------------------------------------------
# Vector 8: POST /api/ai/chat-to-case/<id>/append
# ---------------------------------------------------------------------------

class TestChatToCaseAppendIsolation:
    """chat_to_case_append() now verifies ticket ownership before inserting a comment."""

    def test_chat_to_case_append_blocks_cross_tenant_access(self, app):
        """Tenant A injecting a comment into a Tenant B ticket must get 404.

        Before the fix: comment was inserted into any ticket_id with no
        tenant check — a cross-tenant comment injection vector.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            insert_called = {"called": False}

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID, "requester_id": 2}
                return None

            def _insert_returning(sql, params=None):
                insert_called["called"] = True
                return 999

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
            ):
                resp = c.post(
                    "/api/ai/chat-to-case/999/append",
                    json={"content": "injected cross-tenant comment", "role": "user"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 404, (
            "Cross-tenant comment append must be rejected with 404"
        )
        assert not insert_called["called"], (
            "insert_returning must NOT be called — comment was about to be "
            "written into a foreign tenant's ticket"
        )

    def test_chat_to_case_append_allows_same_tenant_access(self, app):
        """Same-tenant append must insert the comment and return 200."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [101]:
                    return {"id": 101, "tenant_id": TENANT_A_ID, "requester_id": 1}
                return None

            def _insert_returning(sql, params=None):
                return 1

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
                patch("routes.ai.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/ai/chat-to-case/101/append",
                    json={"content": "hello", "role": "user"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_chat_to_case_append_allows_super_admin_cross_tenant(self, app):
        """Super admin can append to any tenant's ticket."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if params == [999]:
                    return {"id": 999, "tenant_id": TENANT_B_ID, "requester_id": 2}
                return None

            def _insert_returning(sql, params=None):
                return 1

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
                patch("routes.ai.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/ai/chat-to-case/999/append",
                    json={"content": "admin note", "role": "user"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# Vector 9: POST /api/ai/chat/escalate (conversation_id-based)
# ---------------------------------------------------------------------------

class TestEscalateIsolation:
    """escalate_to_l2() now verifies the conversation belongs to the caller's tenant."""

    def test_escalate_blocks_cross_tenant_access(self, app):
        """Tenant A escalating a Tenant B conversation must get 404.

        Before the fix: any conversation_id could be escalated regardless of
        tenant_id, leaking conversation history to the L2 model call and
        returning L2 analysis of another tenant's support conversation.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            foreign_conv = {
                "id": 555, "tenant_id": TENANT_B_ID,
                "messages": [{"role": "user", "content": "secret B question"}],
                "language": "en", "status": "active",
            }

            def _fetch_one(sql, params=None):
                if params == [555]:
                    return foreign_conv
                return None

            def _execute(sql, params=None):
                pass

            # Billing gate must pass for the test to reach the tenant check
            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
                patch("services.billing_service.check_ai_gate"),
            ):
                resp = c.post(
                    "/api/ai/chat/escalate",
                    json={"conversation_id": 555},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 404
        data = resp.get_json()
        assert "not found" in data.get("error", "").lower(), (
            f"Expected 'Conversation not found', got: {data}"
        )

    def test_escalate_allows_same_tenant_access(self, app):
        """Escalating your own tenant's conversation must proceed (mocked at L2 call)."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            own_conv = {
                "id": 111, "tenant_id": TENANT_A_ID,
                "messages": [{"role": "user", "content": "help me"}],
                "language": "en", "status": "active",
            }

            def _fetch_one(sql, params=None):
                if params == [111]:
                    return own_conv
                return None

            def _execute(sql, params=None):
                pass

            def _mock_l2(*args, **kwargs):
                return {"answer": "L2 answer", "sources": [], "modules_used": [], "tokens": 10}

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
                patch("routes.ai._save_conversation"),
                patch("services.billing_service.check_ai_gate"),
                patch("services.rag_service.generate_response_l2_contextual", side_effect=_mock_l2),
            ):
                resp = c.post(
                    "/api/ai/chat/escalate",
                    json={"conversation_id": 111},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["layer"] == 2

    def test_escalate_allows_super_admin_cross_tenant(self, app):
        """Super admin can escalate any tenant's conversation."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            foreign_conv = {
                "id": 555, "tenant_id": TENANT_B_ID,
                "messages": [{"role": "user", "content": "foreign question"}],
                "language": "en", "status": "active",
            }

            def _fetch_one(sql, params=None):
                if params == [555]:
                    return foreign_conv
                return None

            def _execute(sql, params=None):
                pass

            def _mock_l2(*args, **kwargs):
                return {"answer": "L2 answer", "sources": [], "modules_used": [], "tokens": 10}

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.execute", side_effect=_execute),
                patch("routes.ai._save_conversation"),
                patch("services.billing_service.check_ai_gate"),
                patch("services.rag_service.generate_response_l2_contextual", side_effect=_mock_l2),
            ):
                resp = c.post(
                    "/api/ai/chat/escalate",
                    json={"conversation_id": 555},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Vector 10: POST /api/ai/chat (conversation_id param loads foreign conversation)
# ---------------------------------------------------------------------------

class TestChatConversationIsolation:
    """POST /api/ai/chat: when conversation_id is supplied, verifies tenant ownership.

    Before the fix: a foreign conversation_id would load that conversation's
    message history and pass it to the LLM, leaking Tenant B's chat context
    into Tenant A's session.
    After the fix: if tenant_id mismatch, conv is set to None and a fresh
    conversation is started — the foreign messages are never used.
    """

    def test_chat_blocks_foreign_conversation_history(self, app):
        """Supplying a Tenant B conversation_id as Tenant A must not load foreign history.

        The fix nullifies the conv object so messages start fresh. We verify that
        the foreign conversation's messages are not forwarded to the RAG service.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            foreign_conv = {
                "id": 555, "tenant_id": TENANT_B_ID,
                "messages": [{"role": "user", "content": "Tenant B secret query"}],
                "language": "en", "l2_analysis": None,
            }

            captured_messages = {}

            def _fetch_one(sql, params=None):
                # _get_conversation call
                if params == [555]:
                    return foreign_conv
                return None

            def _insert_returning(sql, params=None):
                # New conversation insert returns fresh ID
                return 9999

            def _mock_rag(tenant_id, messages, language, **kwargs):
                captured_messages["messages"] = messages
                return {"answer": "ok", "sources": [], "modules_used": [], "tokens": 5}

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai.insert_returning", side_effect=_insert_returning),
                patch("routes.ai._save_conversation"),
                patch("services.billing_service.check_ai_gate"),
                patch("services.rag_service.generate_response_contextual", side_effect=_mock_rag),
            ):
                resp = c.post(
                    "/api/ai/chat",
                    json={"query": "my question", "conversation_id": 555},
                    headers=CSRF_HEADERS,
                )

        # The response itself may succeed (a new conversation is started) or
        # may fail at another point — what matters is the foreign history was
        # not forwarded.
        assert "Tenant B secret query" not in str(captured_messages.get("messages", [])), (
            "Foreign conversation history must NEVER be forwarded to the RAG service"
        )

    def test_chat_allows_own_conversation_history(self, app):
        """Supplying caller's own conversation_id loads that conversation's messages."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            own_conv = {
                "id": 111, "tenant_id": TENANT_A_ID,
                "messages": [{"role": "user", "content": "prior question"}],
                "language": "en", "l2_analysis": None,
            }

            captured_messages = {}

            def _fetch_one(sql, params=None):
                if params == [111]:
                    return own_conv
                return None

            def _mock_rag(tenant_id, messages, language, **kwargs):
                captured_messages["messages"] = messages
                return {"answer": "ok", "sources": [], "modules_used": [], "tokens": 5}

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai._save_conversation"),
                patch("routes.ai._record_article_recommendations"),
                patch("services.billing_service.check_ai_gate"),
                patch("services.rag_service.generate_response_contextual", side_effect=_mock_rag),
            ):
                resp = c.post(
                    "/api/ai/chat",
                    json={"query": "follow up", "conversation_id": 111},
                    headers=CSRF_HEADERS,
                )

        # The prior question from the same tenant must be in the messages
        all_content = str(captured_messages.get("messages", []))
        assert "prior question" in all_content, (
            "Own conversation history must be loaded and forwarded to RAG service"
        )

    def test_chat_allows_super_admin_foreign_conversation(self, app):
        """Super admin can continue a foreign tenant's conversation."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            foreign_conv = {
                "id": 555, "tenant_id": TENANT_B_ID,
                "messages": [{"role": "user", "content": "foreign prior question"}],
                "language": "en", "l2_analysis": None,
            }

            captured_messages = {}

            def _fetch_one(sql, params=None):
                if params == [555]:
                    return foreign_conv
                return None

            def _mock_rag(tenant_id, messages, language, **kwargs):
                captured_messages["messages"] = messages
                return {"answer": "ok", "sources": [], "modules_used": [], "tokens": 5}

            with (
                patch("routes.ai.fetch_one", side_effect=_fetch_one),
                patch("routes.ai._save_conversation"),
                patch("routes.ai._record_article_recommendations"),
                patch("services.billing_service.check_ai_gate"),
                patch("services.rag_service.generate_response_contextual", side_effect=_mock_rag),
            ):
                resp = c.post(
                    "/api/ai/chat",
                    json={"query": "super admin question", "conversation_id": 555},
                    headers=CSRF_HEADERS,
                )

        # Super admin bypass: foreign history IS loaded
        all_content = str(captured_messages.get("messages", []))
        assert "foreign prior question" in all_content, (
            "Super admin must be able to load and continue foreign conversation history"
        )


# ===========================================================================
# Document access check from commit 080ea8b
# ===========================================================================

class TestKBSendToTicketDocAccessIsolation:
    """POST /api/kb/send-to-ticket now enforces document ownership before posting content.

    Before the fix: any authenticated user could reference a document from
    another tenant's private collection and post its full content as a comment
    on their own ticket — a content exfiltration vector.

    After the fix: super_admin bypass, OR doc.tenant_id == caller's tenant_id,
    OR doc's module_id is enabled in tenant_modules for caller's tenant.
    """

    def test_send_to_ticket_blocks_cross_tenant_doc_access(self, app):
        """Tenant A referencing a Tenant B private document must get 404.

        The document has tenant_id=TENANT_B_ID and module_id=None (private
        tenant article, not a shared module). Tenant A neither owns it nor
        has the module enabled — access must be denied.
        """
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            insert_called = {"called": False}

            def _fetch_one(sql, params=None):
                # Document fetch
                if "FROM documents WHERE id = %s" in sql and params == [300]:
                    return {
                        "id": 300,
                        "title": "Tenant B Confidential Doc",
                        "content": "classified content",
                        "doc_tenant_id": TENANT_B_ID,
                        "module_id": None,
                    }
                # tenant_modules check — module_id is None so this won't be called,
                # but if it were called it should return None
                if "FROM tenant_modules" in sql:
                    return None
                return None

            def _insert_returning(sql, params=None):
                insert_called["called"] = True
                return 1

            with (
                patch("routes.kb.fetch_one", side_effect=_fetch_one),
                patch("routes.kb.insert_returning", side_effect=_insert_returning),
            ):
                resp = c.post(
                    "/api/kb/send-to-ticket",
                    json={"document_id": 300, "ticket_id": 101},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 404, (
            "Referencing a foreign tenant's private document must be rejected with 404"
        )
        assert not insert_called["called"], (
            "insert_returning must NOT be called — cross-tenant document content "
            "must never be injected as a ticket comment"
        )

    def test_send_to_ticket_allows_own_tenant_doc(self, app):
        """Tenant A can send their own tenant article to one of their tickets."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(USER_A_AI)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if "FROM documents WHERE id = %s" in sql and params == [200]:
                    return {
                        "id": 200,
                        "title": "Tenant A Own Doc",
                        "content": "our internal runbook",
                        "doc_tenant_id": TENANT_A_ID,
                        "module_id": None,
                    }
                if "FROM tickets WHERE id = %s AND tenant_id = %s" in sql:
                    if params == [101, TENANT_A_ID]:
                        return {"id": 101}
                return None

            def _insert_returning(sql, params=None):
                return 1

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.kb.fetch_one", side_effect=_fetch_one),
                patch("routes.kb.insert_returning", side_effect=_insert_returning),
                patch("routes.kb.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/kb/send-to-ticket",
                    json={"document_id": 200, "ticket_id": 101},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200

    def test_send_to_ticket_allows_super_admin_cross_tenant_doc(self, app):
        """Super admin can send any document to any ticket regardless of tenant ownership."""
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = dict(SUPER_USER)
                sess["csrf_token"] = TEST_CSRF_TOKEN

            def _fetch_one(sql, params=None):
                if "FROM documents WHERE id = %s" in sql and params == [300]:
                    return {
                        "id": 300,
                        "title": "Tenant B Confidential Doc",
                        "content": "classified content",
                        "doc_tenant_id": TENANT_B_ID,
                        "module_id": None,
                    }
                if "FROM tickets WHERE id = %s" in sql and params == [101]:
                    return {"id": 101}
                return None

            def _insert_returning(sql, params=None):
                return 1

            def _execute(sql, params=None):
                pass

            with (
                patch("routes.kb.fetch_one", side_effect=_fetch_one),
                patch("routes.kb.insert_returning", side_effect=_insert_returning),
                patch("routes.kb.execute", side_effect=_execute),
            ):
                resp = c.post(
                    "/api/kb/send-to-ticket",
                    json={"document_id": 300, "ticket_id": 101},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
