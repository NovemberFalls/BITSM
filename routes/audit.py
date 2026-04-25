"""Audit queue blueprint: review closed tickets, bulk management, knowledge gaps."""

import logging
from flask import Blueprint, jsonify, request
from routes.auth import login_required, require_role, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
audit_bp = Blueprint("audit", __name__)


# ============================================================
# Audit Queue
# ============================================================

@audit_bp.route("/queue", methods=["GET"])
@require_permission("audit.view")
def list_audit_queue():
    """List audit queue items with filters."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    queue_type = request.args.get("queue_type")
    status = request.args.get("status", "pending")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    conditions = []
    params = []

    if user["role"] != "super_admin" and tenant_id:
        conditions.append("aq.tenant_id = %s")
        params.append(tenant_id)

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append("aq.status = %s")
            params.append(statuses[0])
        elif statuses:
            placeholders = ", ".join(["%s"] * len(statuses))
            conditions.append(f"aq.status IN ({placeholders})")
            params.extend(statuses)

    if queue_type:
        conditions.append("aq.queue_type = %s")
        params.append(queue_type)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    count_params = list(params)
    params.extend([limit, offset])

    items = fetch_all(
        f"""SELECT aq.*, t.ticket_number, t.subject, t.status as ticket_status,
                   t.priority, t.tags as current_tags,
                   pc.name as current_category_name,
                   pc2.name as suggested_category_name,
                   u.name as reviewed_by_name
            FROM ticket_audit_queue aq
            JOIN tickets t ON t.id = aq.ticket_id
            LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
            LEFT JOIN problem_categories pc2 ON pc2.id = aq.ai_suggested_category_id
            LEFT JOIN users u ON u.id = aq.reviewed_by
            {where}
            ORDER BY aq.created_at DESC
            LIMIT %s OFFSET %s""",
        params,
    )

    total = fetch_one(
        f"SELECT count(*) as cnt FROM ticket_audit_queue aq {where}",
        count_params,
    )

    return jsonify({"items": items, "total": total["cnt"]})


@audit_bp.route("/queue/<int:item_id>/review", methods=["POST"])
@require_permission("audit.review")
def review_audit_item(item_id: int):
    """Review an audit queue item — approve or dismiss."""
    user = get_current_user()
    data = request.json or {}
    action = data.get("action")  # 'approve' or 'dismiss'

    if action not in ("approve", "dismiss"):
        return jsonify({"error": "action must be 'approve' or 'dismiss'"}), 400

    item = fetch_one("SELECT * FROM ticket_audit_queue WHERE id = %s", [item_id])
    if not item:
        return jsonify({"error": "Not found"}), 404

    status = "approved" if action == "approve" else "dismissed"
    execute(
        """UPDATE ticket_audit_queue
           SET status = %s, reviewed_by = %s, reviewed_at = now()
           WHERE id = %s""",
        [status, user["id"], item_id],
    )

    # If approved and it's a KBA candidate, could trigger article creation
    if action == "approve" and item["queue_type"] == "kba_candidate" and item.get("kba_draft"):
        # Create draft article
        article_id = insert_returning(
            """INSERT INTO documents (tenant_id, title, content, tags, is_published)
               VALUES (%s, %s, %s, %s, false) RETURNING id""",
            [item["tenant_id"],
             f"[Draft] {item.get('kba_draft', 'Untitled')[:200]}",
             item.get("kba_draft", ""),
             item.get("ai_suggested_tags", [])],
        )
        execute(
            "UPDATE ticket_audit_queue SET matched_article_id = %s WHERE id = %s",
            [article_id, item_id],
        )

    return jsonify({"ok": True, "status": status})


@audit_bp.route("/queue/<int:item_id>/reopen", methods=["POST"])
@require_permission("audit.review")
def reopen_audit_item(item_id: int):
    """Reopen a reviewed audit queue item back to pending."""
    item = fetch_one("SELECT * FROM ticket_audit_queue WHERE id = %s", [item_id])
    if not item:
        return jsonify({"error": "Not found"}), 404

    execute(
        """UPDATE ticket_audit_queue
           SET status = 'pending', reviewed_by = NULL, reviewed_at = NULL
           WHERE id = %s""",
        [item_id],
    )
    return jsonify({"ok": True})


@audit_bp.route("/queue/bulk", methods=["POST"])
@require_permission("audit.review")
def bulk_manage_queue():
    """Bulk approve/dismiss audit queue items."""
    user = get_current_user()
    data = request.json or {}
    action = data.get("action")
    item_ids = data.get("item_ids", [])
    # If "all_pending" is true, operate on all pending items for the tenant
    all_pending = data.get("all_pending", False)

    if action not in ("approve", "dismiss"):
        return jsonify({"error": "action must be 'approve' or 'dismiss'"}), 400

    tenant_id = get_tenant_id()
    status = "approved" if action == "approve" else "dismissed"

    if all_pending and tenant_id:
        result = execute(
            """UPDATE ticket_audit_queue
               SET status = %s, reviewed_by = %s, reviewed_at = now()
               WHERE tenant_id = %s AND status = 'pending'""",
            [status, user["id"], tenant_id],
        )
        return jsonify({"ok": True, "updated": "all"})
    elif item_ids:
        for item_id in item_ids:
            execute(
                """UPDATE ticket_audit_queue
                   SET status = %s, reviewed_by = %s, reviewed_at = now()
                   WHERE id = %s""",
                [status, user["id"], int(item_id)],
            )
        return jsonify({"ok": True, "updated": len(item_ids)})

    return jsonify({"error": "Provide item_ids or set all_pending=true"}), 400


@audit_bp.route("/queue/stats", methods=["GET"])
@require_permission("audit.view")
def audit_stats():
    """Get audit queue stats for dashboard."""
    tenant_id = get_tenant_id()
    user = get_current_user()

    conditions = ["status = 'pending'"]
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}"

    stats = fetch_one(
        f"""SELECT
                count(*) as total_pending,
                count(*) FILTER (WHERE queue_type = 'auto_resolved') as auto_resolved,
                count(*) FILTER (WHERE queue_type = 'human_resolved') as human_resolved,
                count(*) FILTER (WHERE queue_type = 'low_confidence') as low_confidence,
                count(*) FILTER (WHERE queue_type = 'kba_candidate') as kba_candidates,
                avg(resolution_score) FILTER (WHERE resolution_score IS NOT NULL) as avg_resolution_score
            FROM ticket_audit_queue {where}""",
        params,
    )
    return jsonify(stats)


# ============================================================
# Audit Queue Settings
# ============================================================

@audit_bp.route("/settings", methods=["GET"])
@require_permission("audit.review")
def get_audit_settings():
    """Get tenant's audit queue settings."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({}), 200

    tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    settings = (tenant or {}).get("settings") or {}

    return jsonify({
        "ai_audit_auto_close_days": settings.get("ai_audit_auto_close_days", 7),
        "ai_audit_enabled": settings.get("ai_audit_enabled", True),
        "ai_audit_auto_approve_threshold": settings.get("ai_audit_auto_approve_threshold", 80),
        "ai_audit_auto_dismiss_threshold": settings.get("ai_audit_auto_dismiss_threshold", 0),
    })


@audit_bp.route("/settings", methods=["PUT"])
@require_permission("audit.review")
def update_audit_settings():
    """Update tenant's audit queue settings."""
    import json as json_mod
    tenant_id = get_tenant_id()
    user = get_current_user()
    if user["role"] == "tenant_admin" and user.get("tenant_id") != tenant_id:
        return jsonify({"error": "Forbidden"}), 403

    data = request.json or {}
    existing = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    if not existing:
        return jsonify({"error": "Tenant not found"}), 404

    settings = existing.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}

    for key in ("ai_audit_auto_close_days", "ai_audit_enabled", "ai_audit_auto_approve_threshold", "ai_audit_auto_dismiss_threshold"):
        if key in data:
            settings[key] = data[key]

    execute(
        "UPDATE tenants SET settings = %s::jsonb, updated_at = now() WHERE id = %s",
        [json_mod.dumps(settings), tenant_id],
    )

    # Re-evaluate pending audit items against new thresholds
    auto_approve = settings.get("ai_audit_auto_approve_threshold", 80)
    auto_dismiss = settings.get("ai_audit_auto_dismiss_threshold", 0)

    if auto_approve and auto_approve > 0:
        result = execute(
            """UPDATE ticket_audit_queue
               SET status = 'auto_closed', auto_close_at = now()
               WHERE tenant_id = %s AND status = 'pending'
                 AND resolution_score IS NOT NULL
                 AND resolution_score >= %s""",
            [tenant_id, auto_approve / 100.0],
        )
        logger.info("Auto-approved %s audit items for tenant %s (threshold %s%%)",
                     getattr(result, 'rowcount', '?'), tenant_id, auto_approve)

    if auto_dismiss and auto_dismiss > 0:
        result = execute(
            """UPDATE ticket_audit_queue
               SET status = 'auto_closed', auto_close_at = now()
               WHERE tenant_id = %s AND status = 'pending'
                 AND resolution_score IS NOT NULL
                 AND resolution_score <= %s""",
            [tenant_id, auto_dismiss / 100.0],
        )
        logger.info("Auto-dismissed %s audit items for tenant %s (threshold %s%%)",
                     getattr(result, 'rowcount', '?'), tenant_id, auto_dismiss)

    return jsonify({"ok": True})


# ============================================================
# Knowledge Gaps
# ============================================================

@audit_bp.route("/knowledge-gaps", methods=["GET"])
@require_permission("audit.view")
def list_knowledge_gaps():
    """List detected knowledge gaps."""
    tenant_id = get_tenant_id()
    user = get_current_user()

    conditions = []
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("kg.tenant_id = %s")
        params.append(tenant_id)

    status = request.args.get("status")
    if status:
        conditions.append("kg.status = %s")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    gaps = fetch_all(
        f"""SELECT kg.*, d.title as article_title
            FROM knowledge_gaps kg
            LEFT JOIN documents d ON d.id = kg.created_article_id
            {where}
            ORDER BY kg.ticket_count DESC, kg.updated_at DESC
            LIMIT 50""",
        params,
    )
    return jsonify(gaps)


@audit_bp.route("/knowledge-gaps/<int:gap_id>", methods=["PUT"])
@require_permission("audit.kba")
def update_knowledge_gap(gap_id: int):
    """Update a knowledge gap status."""
    data = request.json or {}
    status = data.get("status")
    if status not in ("detected", "acknowledged", "article_created", "dismissed"):
        return jsonify({"error": "Invalid status"}), 400

    execute(
        "UPDATE knowledge_gaps SET status = %s, updated_at = now() WHERE id = %s",
        [status, gap_id],
    )
    return jsonify({"ok": True})


@audit_bp.route("/knowledge-gaps/detect", methods=["POST"])
@require_permission("audit.kba")
def trigger_gap_detection():
    """Manually trigger knowledge gap detection."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    from services.atlas_service import detect_knowledge_gaps
    detect_knowledge_gaps(tenant_id)
    return jsonify({"ok": True, "message": "Detection started"})


# ============================================================
# Ticket Metrics
# ============================================================

@audit_bp.route("/metrics", methods=["GET"])
@require_permission("metrics.view")
def get_metrics_summary():
    """Get aggregate ticket metrics for the tenant."""
    tenant_id = get_tenant_id()
    user = get_current_user()

    conditions = []
    params = []
    if user["role"] != "super_admin" and tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    metrics = fetch_one(
        f"""SELECT
                count(*) as total_tickets,
                avg(effort_score) as avg_effort_score,
                count(*) FILTER (WHERE resolved_first_contact = true) as fcr_count,
                count(*) FILTER (WHERE resolved_first_contact IS NOT NULL) as fcr_total,
                avg(reply_count) as avg_replies,
                avg(escalation_count) as avg_escalations,
                count(*) FILTER (WHERE resolution_type IN ('ai_l1', 'ai_l2')) as ai_resolved_count,
                count(*) FILTER (WHERE resolution_type IS NOT NULL) as resolution_total,
                count(*) FILTER (WHERE was_escalated_from_ai = true) as escalated_from_ai_count,
                avg(ai_turns_before_resolve) FILTER (WHERE ai_turns_before_resolve > 0) as avg_ai_turns,
                count(*) FILTER (WHERE high_effort = true) as high_effort_count,
                count(*) FILTER (WHERE resolution_type = 'ai_l1') as ai_l1_count,
                count(*) FILTER (WHERE resolution_type = 'ai_l2') as ai_l2_count,
                count(*) FILTER (WHERE resolution_type = 'human') as human_count,
                count(*) FILTER (WHERE resolution_type = 'hybrid') as hybrid_count
            FROM ticket_metrics {where}""",
        params,
    )

    # FCR rate
    if metrics and metrics.get("fcr_total") and metrics["fcr_total"] > 0:
        metrics["fcr_rate"] = round(metrics["fcr_count"] / metrics["fcr_total"] * 100, 1)
    else:
        metrics["fcr_rate"] = None

    # AI resolution rate
    if metrics and metrics.get("resolution_total") and metrics["resolution_total"] > 0:
        metrics["ai_resolution_rate"] = round(
            metrics["ai_resolved_count"] / metrics["resolution_total"] * 100, 1
        )
        metrics["escalation_rate"] = round(
            metrics["escalated_from_ai_count"] / metrics["resolution_total"] * 100, 1
        )
    else:
        metrics["ai_resolution_rate"] = None
        metrics["escalation_rate"] = None

    # Article effectiveness metrics
    doc_conditions = ["is_published = true"]
    doc_params = []
    if user["role"] != "super_admin" and tenant_id:
        doc_conditions.append("tenant_id = %s")
        doc_params.append(tenant_id)
    doc_where = "WHERE " + " AND ".join(doc_conditions)

    effectiveness = fetch_one(
        f"""SELECT
              avg(effectiveness_score) as avg_effectiveness,
              count(*) FILTER (WHERE effectiveness_score IS NOT NULL AND rating_count >= 3) as rated_articles,
              count(*) FILTER (WHERE effectiveness_score < 0.3 AND rating_count >= 5) as low_effectiveness_count
           FROM documents
           {doc_where}""",
        doc_params,
    )
    if effectiveness:
        metrics["article_effectiveness_avg"] = round(effectiveness["avg_effectiveness"] or 0, 3)
        metrics["rated_articles"] = effectiveness["rated_articles"]
        metrics["low_effectiveness_count"] = effectiveness["low_effectiveness_count"]

    # Top and bottom articles by effectiveness
    top_articles = fetch_all(
        f"""SELECT id, title, effectiveness_score, rating_count
           FROM documents
           WHERE effectiveness_score IS NOT NULL AND rating_count >= 3 AND is_published = true
           {"AND tenant_id = %s" if doc_params else ""}
           ORDER BY effectiveness_score DESC
           LIMIT 5""",
        doc_params,
    )
    low_articles = fetch_all(
        f"""SELECT id, title, effectiveness_score, rating_count
           FROM documents
           WHERE effectiveness_score IS NOT NULL AND rating_count >= 3 AND is_published = true
           {"AND tenant_id = %s" if doc_params else ""}
           ORDER BY effectiveness_score ASC
           LIMIT 5""",
        doc_params,
    )
    metrics["top_articles"] = top_articles or []
    metrics["low_effectiveness_articles"] = low_articles or []

    return jsonify(metrics)


@audit_bp.route("/metrics/ticket/<int:ticket_id>", methods=["GET"])
@require_permission("audit.view")
def get_ticket_metrics(ticket_id: int):
    """Get metrics for a specific ticket."""
    metrics = fetch_one(
        """SELECT tm.*, u.name as suggested_assignee_name
           FROM ticket_metrics tm
           LEFT JOIN users u ON u.id = tm.suggested_assignee_id
           WHERE tm.ticket_id = %s""",
        [ticket_id],
    )
    return jsonify(metrics or {})
