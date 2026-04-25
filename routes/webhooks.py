"""Webhooks blueprint: API endpoints for pipeline orchestration, scrapers, KB tools, monitoring, and health."""

import hashlib
import json
import logging
import threading

from flask import Blueprint, jsonify, request

from app import limiter
from config import Config
from models.db import fetch_one, fetch_all, execute, insert_returning

logger = logging.getLogger(__name__)
webhooks_bp = Blueprint("webhooks", __name__)


def _validate_api_key() -> bool:
    """Validate X-API-Key header against api_keys table (SHA-256 hash comparison).

    Rejects expired keys (SOC 2 CC6.1 — 90-day expiry policy).
    """
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        return False

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    row = fetch_one(
        """SELECT id, expires_at FROM api_keys
           WHERE key_hash = %s AND is_active = true""",
        [key_hash],
    )
    if not row:
        return False
    # Reject expired keys
    if row.get("expires_at"):
        from datetime import datetime, timezone
        expires = row["expires_at"]
        if hasattr(expires, 'tzinfo') and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            logger.warning("Rejected expired API key id=%s (expired %s)", row["id"], expires)
            return False
    execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", [row["id"]])
    return True


@webhooks_bp.route("/health", methods=["GET"])
def health():
    """Health check endpoint. No auth required.

    Returns 503 if the queue processor poll thread is not alive —
    Atlas stops engaging tickets silently when the queue dies.
    """
    from services.queue_service import is_queue_alive
    queue_alive = is_queue_alive()
    status_code = 200 if queue_alive else 503
    return jsonify({
        "status": "ok" if queue_alive else "degraded",
        "service": "helpdesk",
        "queue_processor": "alive" if queue_alive else "dead",
    }), status_code


@webhooks_bp.route("/csat/<token>", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def csat_response(token: str):
    """Record a CSAT survey response. No auth required — token-based.

    GET with ?rating=N shows a thank-you page.
    POST with JSON {rating, comment} records the response.
    """
    survey = fetch_one(
        "SELECT id, ticket_id, tenant_id, responded_at FROM csat_surveys WHERE token = %s",
        [token],
    )
    if not survey:
        return jsonify({"error": "Survey not found or expired"}), 404

    if survey.get("responded_at"):
        return jsonify({"message": "Thank you! Your feedback has already been recorded."}), 200

    # Extract rating from query param (email click) or JSON body
    rating = request.args.get("rating") or (request.json or {}).get("rating")
    comment = (request.json or {}).get("comment", "")

    if not rating:
        return jsonify({"error": "Rating is required (1-5)"}), 400

    try:
        rating_int = int(rating)
        if rating_int < 1 or rating_int > 5:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Rating must be between 1 and 5"}), 400

    execute(
        "UPDATE csat_surveys SET rating = %s, comment = %s, responded_at = now() WHERE id = %s",
        [rating_int, comment or None, survey["id"]],
    )

    logger.info("CSAT response recorded for ticket %s: rating=%s", survey["ticket_id"], rating_int)
    return jsonify({
        "message": "Thank you for your feedback!",
        "rating": rating_int,
    })


@webhooks_bp.route("/pipeline/trigger", methods=["POST"])
def trigger_pipeline():
    """Trigger KB pipeline run (API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    module_slug = data.get("module_slug")
    if not module_slug:
        return jsonify({"error": "module_slug is required"}), 400

    force = data.get("force", False)

    from services.pipeline_service import start_pipeline, get_pipeline_status

    current = get_pipeline_status(module_slug)
    if current.get("status") == "running":
        return jsonify({"error": "Pipeline already running", "status": current}), 409

    start_pipeline(module_slug, force=force)
    return jsonify({"ok": True, "message": f"Pipeline started for {module_slug}"})


@webhooks_bp.route("/pipeline/full", methods=["POST"])
def trigger_full_pipeline():
    """Trigger full KB pipeline: ingest + auto-tag + embed (API).

    Body: {"module_slug": "toast", "force": false, "sync": false}
    - sync=true: blocks until complete, returns full stats
    - sync=false: fires and forgets, returns immediately

    See docs/knowledge-pipeline-architecture.md for the full process.
    """
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    module_slug = data.get("module_slug")
    if not module_slug:
        return jsonify({"error": "module_slug is required"}), 400

    force = data.get("force", False)
    sync = data.get("sync", False)

    from services.pipeline_service import (
        get_pipeline_status, start_full_pipeline, run_full_pipeline,
    )

    current = get_pipeline_status(module_slug)
    if current.get("status") == "running":
        return jsonify({"error": "Pipeline already running", "status": current}), 409

    if sync:
        result = run_full_pipeline(module_slug, force=force, verbose=False)
        return jsonify(result)
    else:
        start_full_pipeline(module_slug, force=force)
        return jsonify({"ok": True, "message": f"Full pipeline started for {module_slug}"})


@webhooks_bp.route("/pipeline/status/<module_slug>", methods=["GET"])
def pipeline_status(module_slug: str):
    """Poll pipeline status (API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    from services.pipeline_service import get_pipeline_status
    return jsonify(get_pipeline_status(module_slug))


# ============================================================
# Scraper endpoints
# ============================================================

@webhooks_bp.route("/scrape/run", methods=["POST"])
def scrape_run():
    """Run a KB scraper for a module (API).

    Body: {"module_slug": "solink", "sync": true}
    - sync=true: blocks until complete, returns full stats
    - sync=false: fires and forgets, returns immediately
    """
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    module_slug = data.get("module_slug")
    if not module_slug:
        return jsonify({"error": "module_slug is required"}), 400

    sync = data.get("sync", False)

    from services.scrapers import get_scraper, run_scraper, available

    if not get_scraper(module_slug):
        # Not an error — some modules (e.g., toast) use standalone scrapers.
        # Return 200 so the caller can proceed to the pipeline step.
        return jsonify({
            "ok": True,
            "module_slug": module_slug,
            "skipped": True,
            "message": f"No scraper registered for '{module_slug}'. "
                       f"Available: {available()}",
        })

    if sync:
        try:
            stats = run_scraper(module_slug)
            return jsonify({"ok": True, "module_slug": module_slug, **stats})
        except Exception as e:
            logger.exception("Scraper %s failed", module_slug)
            return jsonify({
                "ok": False, "module_slug": module_slug, "error": str(e),
            }), 500
    else:
        def _run():
            try:
                run_scraper(module_slug)
            except Exception:
                logger.exception("Scraper %s failed (async)", module_slug)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            "ok": True,
            "message": f"Scraper started for {module_slug}",
        })


@webhooks_bp.route("/scrape/available", methods=["GET"])
def scrape_available():
    """List modules with registered scrapers. No auth required."""
    from services.scrapers import available
    return jsonify({"scrapers": available()})


# ============================================================
# KB Tool endpoints (external API consumers)
# ============================================================

# ============================================================
# Ticket lifecycle endpoints (pipeline queue + external API)
# ============================================================

@webhooks_bp.route("/ticket/auto-tag", methods=["POST"])
def ticket_auto_tag():
    """Auto-tag a ticket via LLM (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400

    try:
        from services.tagging_service import _tag_worker
        _tag_worker(int(ticket_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "auto_tag"})
    except Exception as e:
        logger.exception("Webhook auto-tag failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "auto_tag"}), 500


@webhooks_bp.route("/ticket/enrich", methods=["POST"])
def ticket_enrich():
    """Enrich a ticket with KB context (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400

    try:
        from services.enrichment_service import _enrichment_worker
        _enrichment_worker(int(ticket_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "enrich"})
    except Exception as e:
        logger.exception("Webhook enrich failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "enrich"}), 500


@webhooks_bp.route("/ticket/engage", methods=["POST"])
def ticket_engage():
    """Atlas auto-engage on new ticket (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    tenant_id = data.get("tenant_id")
    if not ticket_id or not tenant_id:
        return jsonify({"error": "ticket_id and tenant_id are required"}), 400

    try:
        from services.atlas_service import _engage_worker, is_ticket_review_enabled
        if not is_ticket_review_enabled(int(tenant_id)):
            return jsonify({"ok": True, "skipped": True, "reason": "ticket_review not enabled"})
        _engage_worker(int(ticket_id), int(tenant_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "engage"})
    except Exception as e:
        logger.exception("Webhook engage failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "engage"}), 500


@webhooks_bp.route("/ticket/route", methods=["POST"])
def ticket_route():
    """Smart routing suggestion (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    tenant_id = data.get("tenant_id")
    if not ticket_id or not tenant_id:
        return jsonify({"error": "ticket_id and tenant_id are required"}), 400

    try:
        from services.atlas_service import _routing_worker
        _routing_worker(int(ticket_id), int(tenant_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "route"})
    except Exception as e:
        logger.exception("Webhook route failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "route"}), 500


@webhooks_bp.route("/ticket/audit", methods=["POST"])
def ticket_audit():
    """Close-time audit pipeline (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    tenant_id = data.get("tenant_id")
    if not ticket_id or not tenant_id:
        return jsonify({"error": "ticket_id and tenant_id are required"}), 400

    try:
        from services.atlas_service import _audit_close_worker, is_ticket_review_enabled
        if not is_ticket_review_enabled(int(tenant_id)):
            return jsonify({"ok": True, "skipped": True, "reason": "ticket_review not enabled"})
        _audit_close_worker(int(ticket_id), int(tenant_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "audit"})
    except Exception as e:
        logger.exception("Webhook audit failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "audit"}), 500


@webhooks_bp.route("/ticket/effort", methods=["POST"])
def ticket_effort():
    """Customer effort scoring (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    ticket_id = data.get("ticket_id")
    tenant_id = data.get("tenant_id")
    if not ticket_id or not tenant_id:
        return jsonify({"error": "ticket_id and tenant_id are required"}), 400

    try:
        from services.atlas_service import _effort_worker
        _effort_worker(int(ticket_id), int(tenant_id))
        return jsonify({"ok": True, "ticket_id": ticket_id, "step": "effort"})
    except Exception as e:
        logger.exception("Webhook effort failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "effort"}), 500


@webhooks_bp.route("/ticket/notify", methods=["POST"])
def ticket_notify():
    """Send notifications for a ticket event (pipeline API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    tenant_id = data.get("tenant_id")
    ticket_id = data.get("ticket_id")
    event = data.get("event")
    comment = data.get("comment")
    if not tenant_id or not ticket_id or not event:
        return jsonify({"error": "tenant_id, ticket_id, and event are required"}), 400

    try:
        from services.notification_service import notify_ticket_event
        notify_ticket_event(int(tenant_id), int(ticket_id), event, comment=comment)
        return jsonify({"ok": True, "ticket_id": ticket_id, "event": event, "step": "notify"})
    except Exception as e:
        logger.exception("Webhook notify failed for ticket %s", ticket_id)
        return jsonify({"ok": False, "error": str(e), "step": "notify"}), 500


@webhooks_bp.route("/ticket/sla-check", methods=["POST"])
def ticket_sla_check():
    """SLA breach detection across all active tickets (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        from models.db import fetch_all as fa
        from services.sla_service import check_sla_breaches

        # Find all active tickets with SLA deadlines
        active = fa(
            """SELECT id FROM tickets
               WHERE sla_due_at IS NOT NULL
                 AND sla_breached = false
                 AND status NOT IN ('resolved', 'closed_not_resolved')"""
        )
        ticket_ids = [r["id"] for r in active]
        if ticket_ids:
            check_sla_breaches(ticket_ids)
        return jsonify({
            "ok": True, "checked": len(ticket_ids),
            "step": "sla_check",
        })
    except Exception as e:
        logger.exception("Webhook SLA check failed")
        return jsonify({"ok": False, "error": str(e), "step": "sla_check"}), 500


@webhooks_bp.route("/ticket/knowledge-gaps", methods=["POST"])
def ticket_knowledge_gaps():
    """Run knowledge gap detection for a tenant (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400

    try:
        from services.atlas_service import detect_knowledge_gaps
        detect_knowledge_gaps(int(tenant_id))
        return jsonify({"ok": True, "tenant_id": tenant_id, "step": "knowledge_gaps"})
    except Exception as e:
        logger.exception("Webhook knowledge gaps failed for tenant %s", tenant_id)
        return jsonify({"ok": False, "error": str(e), "step": "knowledge_gaps"}), 500


# ============================================================
# KB Tool endpoints (external API consumers)
# ============================================================

# ============================================================
# Phase 8 — Advanced Atlas webhook endpoints
# ============================================================

@webhooks_bp.route("/ticket/audit-auto-close", methods=["POST"])
def ticket_audit_auto_close():
    """Auto-close stale audit queue items past their auto_close_at deadline (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        from models.db import fetch_all as fa
        closed = fa(
            """UPDATE ticket_audit_queue
               SET status = 'auto_closed'
               WHERE status = 'pending' AND auto_close_at < now()
               RETURNING id, ticket_id, tenant_id"""
        )
        logger.info("Audit auto-close: %d items closed", len(closed))
        return jsonify({"ok": True, "closed_count": len(closed), "step": "audit_auto_close"})
    except Exception as e:
        logger.exception("Webhook audit-auto-close failed")
        return jsonify({"ok": False, "error": str(e), "step": "audit_auto_close"}), 500


@webhooks_bp.route("/ticket/sla-risk", methods=["POST"])
def ticket_sla_risk():
    """Calculate SLA risk levels for open tickets approaching deadlines (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        # Reset all risks first
        execute(
            """UPDATE tickets SET sla_risk = 'normal'
               WHERE sla_risk != 'normal'
                 AND status NOT IN ('resolved', 'closed_not_resolved')"""
        )

        # Critical: < 30min to SLA breach
        critical = execute(
            """UPDATE tickets SET sla_risk = 'critical'
               WHERE sla_due_at IS NOT NULL
                 AND sla_breached = false
                 AND status NOT IN ('resolved', 'closed_not_resolved')
                 AND sla_due_at < now() + interval '30 minutes'
                 AND sla_due_at > now()"""
        )

        # At risk: < 2 hours to SLA breach (but not critical)
        at_risk = execute(
            """UPDATE tickets SET sla_risk = 'at_risk'
               WHERE sla_due_at IS NOT NULL
                 AND sla_breached = false
                 AND sla_risk = 'normal'
                 AND status NOT IN ('resolved', 'closed_not_resolved')
                 AND sla_due_at < now() + interval '2 hours'
                 AND sla_due_at > now()"""
        )

        logger.info("SLA risk scan: %d critical, %d at_risk", critical, at_risk)
        return jsonify({
            "ok": True, "critical": critical, "at_risk": at_risk,
            "step": "sla_risk",
        })
    except Exception as e:
        logger.exception("Webhook SLA risk failed")
        return jsonify({"ok": False, "error": str(e), "step": "sla_risk"}), 500


@webhooks_bp.route("/ticket/escalation-check", methods=["POST"])
def ticket_escalation_check():
    """Auto-escalate stale tickets with no agent response (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        from models.db import fetch_all as fa

        # Find open tickets with no agent reply in > 4 hours
        stale = fa(
            """SELECT t.id, t.tenant_id, t.priority, t.ticket_number
               FROM tickets t
               WHERE t.status = 'open'
                 AND t.assignee_id IS NOT NULL
                 AND t.updated_at < now() - interval '4 hours'
                 AND NOT EXISTS (
                     SELECT 1 FROM ticket_comments tc
                     WHERE tc.ticket_id = t.id
                       AND tc.is_internal = false
                       AND tc.author_id = t.assignee_id
                       AND tc.created_at > now() - interval '4 hours'
                 )"""
        )

        escalated = 0
        priority_map = {"p4": "p3", "p3": "p2"}  # p2 and p1 don't auto-escalate

        for ticket in stale:
            old_priority = ticket["priority"]
            new_priority = priority_map.get(old_priority)
            if not new_priority:
                continue  # Already P1 or P2, don't escalate

            execute(
                "UPDATE tickets SET priority = %s, updated_at = now() WHERE id = %s",
                [new_priority, ticket["id"]],
            )

            # Post internal note
            from models.db import insert_returning
            insert_returning(
                """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal)
                   VALUES (%s, NULL, %s, true) RETURNING id""",
                [ticket["id"],
                 f"[Atlas] Auto-escalated from {old_priority.upper()} to {new_priority.upper()}: "
                 f"no agent response in 4+ hours"],
            )
            escalated += 1

        logger.info("Escalation check: %d tickets escalated out of %d stale", escalated, len(stale))
        return jsonify({"ok": True, "escalated": escalated, "checked": len(stale), "step": "escalation_check"})
    except Exception as e:
        logger.exception("Webhook escalation check failed")
        return jsonify({"ok": False, "error": str(e), "step": "escalation_check"}), 500


@webhooks_bp.route("/kb/freshness-check", methods=["POST"])
def kb_freshness_check():
    """Flag KB documents not updated in 90+ days (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        from models.db import fetch_all as fa

        stale = fa(
            """SELECT d.id, d.title, km.slug as module_slug, km.name as module_name
               FROM documents d
               JOIN knowledge_modules km ON km.id = d.module_id
               WHERE d.updated_at < now() - interval '90 days'
                 AND d.is_active = true
               ORDER BY d.updated_at ASC"""
        )

        if stale:
            # Group by module
            modules = {}
            for doc in stale:
                modules.setdefault(doc["module_slug"], []).append(doc["title"])

            # Insert as knowledge gaps (tenant_id = NULL for system-wide KB staleness)
            for mod_slug, titles in modules.items():
                topic = f"Stale KB content: {mod_slug} ({len(titles)} docs)"
                existing = fetch_one(
                    "SELECT id FROM knowledge_gaps WHERE topic = %s AND status = 'detected'",
                    [topic],
                )
                if not existing:
                    from models.db import insert_returning
                    insert_returning(
                        """INSERT INTO knowledge_gaps (tenant_id, topic, ticket_count, suggested_title, status)
                           VALUES (NULL, %s, %s, %s, 'detected') RETURNING id""",
                        [topic, len(titles), f"Review stale {mod_slug} documentation"],
                    )

        logger.info("KB freshness: %d stale documents found", len(stale))
        return jsonify({
            "ok": True, "stale_docs": len(stale),
            "modules_affected": list({d["module_slug"] for d in stale}),
            "step": "freshness_check",
        })
    except Exception as e:
        logger.exception("Webhook freshness check failed")
        return jsonify({"ok": False, "error": str(e), "step": "freshness_check"}), 500


@webhooks_bp.route("/tenant/health-check", methods=["POST"])
def tenant_health_check():
    """Check tenant health: inactive paid, expiring plans, high SLA breach rates (cron API)."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    try:
        from models.db import fetch_all as fa

        # Paying tenants with no ticket activity in 7+ days
        inactive = fa(
            """SELECT t.id, t.name, t.plan_tier,
                      max(tk.created_at) as last_ticket_at
               FROM tenants t
               LEFT JOIN tickets tk ON tk.tenant_id = t.id
               WHERE t.plan_tier NOT IN ('free') AND t.is_active = true
               GROUP BY t.id
               HAVING max(tk.created_at) IS NULL
                  OR max(tk.created_at) < now() - interval '7 days'"""
        )

        # Tenants with plan expiring in < 14 days
        expiring = fa(
            """SELECT id, name, plan_tier, plan_expires_at
               FROM tenants
               WHERE plan_expires_at IS NOT NULL
                 AND plan_expires_at < now() + interval '14 days'
                 AND plan_expires_at > now()
                 AND is_active = true"""
        )

        # Tenants with > 50% SLA breach rate (last 30 days)
        unhealthy = fa(
            """SELECT t.id, t.name,
                      count(*) FILTER (WHERE tk.sla_breached = true) as breached,
                      count(*) as total,
                      ROUND(
                          count(*) FILTER (WHERE tk.sla_breached = true)::numeric / NULLIF(count(*), 0) * 100, 1
                      ) as breach_rate
               FROM tenants t
               JOIN tickets tk ON tk.tenant_id = t.id
               WHERE tk.created_at > now() - interval '30 days'
                 AND t.is_active = true
               GROUP BY t.id
               HAVING count(*) FILTER (WHERE tk.sla_breached = true)::numeric / NULLIF(count(*), 0) > 0.5"""
        )

        logger.info(
            "Tenant health: %d inactive, %d expiring, %d unhealthy",
            len(inactive), len(expiring), len(unhealthy),
        )
        return jsonify({
            "ok": True,
            "inactive": inactive,
            "expiring": expiring,
            "unhealthy": unhealthy,
            "step": "tenant_health",
        })
    except Exception as e:
        logger.exception("Webhook tenant health check failed")
        return jsonify({"ok": False, "error": str(e), "step": "tenant_health"}), 500


# ============================================================
# KB Tool endpoints (external API consumers)
# ============================================================

@webhooks_bp.route("/kb/search", methods=["POST"])
def kb_search():
    """Vector similarity search — KB API tool."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    tenant_id = data.get("tenant_id")
    query = data.get("query", "")
    module = data.get("module")
    tags = data.get("tags")  # Phase 2: optional tag pre-filter
    limit = min(data.get("limit", 5), 10)

    if not tenant_id or not query:
        return jsonify({"error": "tenant_id and query are required"}), 400

    from services.rag_service import _tool_kb_search
    result = _tool_kb_search(
        query=query, module=module, tags=tags, limit=limit, tenant_id=tenant_id,
    )
    return jsonify(json.loads(result))


@webhooks_bp.route("/kb/lookup", methods=["POST"])
def kb_lookup():
    """Full document retrieval — KB API tool."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    tenant_id = data.get("tenant_id")
    document_id = data.get("document_id")

    if not tenant_id or not document_id:
        return jsonify({"error": "tenant_id and document_id are required"}), 400

    from services.rag_service import _tool_kb_lookup
    result = _tool_kb_lookup(document_id, tenant_id)
    return jsonify(json.loads(result))


@webhooks_bp.route("/kb/articles", methods=["POST"])
def kb_articles():
    """List available articles — KB API tool."""
    if not _validate_api_key():
        return jsonify({"error": "Invalid API key"}), 401

    data = request.json or {}
    tenant_id = data.get("tenant_id")

    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400

    from services.rag_service import _tool_list_articles
    result = _tool_list_articles(
        module=data.get("module"),
        search=data.get("search"),
        tenant_id=tenant_id,
    )
    return jsonify(json.loads(result))


# ============================================================
# Inbound email → ticket
# ============================================================

@webhooks_bp.route("/inbound-email", methods=["POST"])
def inbound_email():
    """Create or update a ticket from an inbound email (Cloudflare Email Worker)."""
    # Authenticate via shared secret
    secret = request.headers.get("X-Webhook-Secret", "")
    if not Config.INBOUND_EMAIL_SECRET or secret != Config.INBOUND_EMAIL_SECRET:
        logger.warning("Inbound email: invalid or missing webhook secret")
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    slug        = (data.get("slug")        or "").lower().strip()
    from_addr   = (data.get("from")        or "").strip()
    subject     = (data.get("subject")     or "(no subject)").strip()
    body_text   = (data.get("text")        or "").strip()
    in_reply_to = (data.get("in_reply_to") or "").strip()

    if not slug or not from_addr:
        return jsonify({"error": "slug and from are required"}), 400

    from services.inbound_email_service import process_inbound_email
    result = process_inbound_email(slug, from_addr, subject, body_text, in_reply_to)
    return jsonify(result), 200
