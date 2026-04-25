"""Reports blueprint: tenant-scoped reporting with free/paid tier gating."""

import csv
import io
import logging
from flask import Blueprint, jsonify, request, Response
from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one

logger = logging.getLogger(__name__)
reports_bp = Blueprint("reports", __name__)

# ============================================================
# Helpers
# ============================================================

FREE_REPORTS = ["ticket-volume", "status-breakdown", "category-breakdown", "aging-tickets"]
PAID_REPORTS = ["sla-compliance", "agent-performance", "ai-effectiveness", "routing-insights", "location-breakdown"]
ALL_REPORTS = FREE_REPORTS + PAID_REPORTS


def _get_plan_tier(tenant_id):
    """Return tenant plan tier. Defaults to 'free'."""
    if not tenant_id:
        return "free"
    row = fetch_one("SELECT plan_tier FROM tenants WHERE id = %s", [tenant_id])
    return (row or {}).get("plan_tier") or "free"


def _is_paid_tier(tenant_id):
    return _get_plan_tier(tenant_id) in ("paid", "trial")


def _require_paid(tenant_id):
    if not _is_paid_tier(tenant_id):
        return jsonify({"error": "This report requires a paid plan", "upgrade_required": True}), 403
    return None


def _resolve_tenant(user):
    req_tenant = request.args.get("tenant_id")
    session_tenant = get_tenant_id()

    if user["role"] == "super_admin":
        tid = int(req_tenant) if req_tenant else session_tenant
    else:
        tid = session_tenant
        if req_tenant and str(req_tenant) != str(session_tenant):
            return None, (jsonify({"error": "Access denied"}), 403)

    if not tid:
        return None, (jsonify({"error": "tenant_id required"}), 400)

    return int(tid), None


def _date_conditions(col="t.created_at"):
    """Build date range SQL conditions + params. Default: last 30 days."""
    start = request.args.get("start_date")
    end = request.args.get("end_date")

    conditions = []
    params = []

    if start:
        conditions.append(f"{col} >= %s::date")
        params.append(start)
    else:
        conditions.append(f"{col} >= now() - interval '30 days'")

    if end:
        conditions.append(f"{col} < %s::date + interval '1 day'")
        params.append(end)

    return conditions, params


def _location_cte():
    """Returns (cte_prefix, condition, params) for recursive location subtree filtering."""
    loc_id = request.args.get("location_id")
    if not loc_id:
        return "", "", []
    try:
        loc_id = int(loc_id)
    except (ValueError, TypeError):
        return "", "", []
    cte = (
        "WITH RECURSIVE loc_tree AS (\n"
        "    SELECT id FROM locations WHERE id = %s\n"
        "    UNION ALL\n"
        "    SELECT l.id FROM locations l JOIN loc_tree lt ON l.parent_id = lt.id\n"
        ")\n"
    )
    return cte, "t.location_id IN (SELECT id FROM loc_tree)", [loc_id]


def _team_condition():
    """Returns (condition, params) for team_id filtering on tickets."""
    team_id = request.args.get("team_id")
    if not team_id:
        return "", []
    try:
        return "t.team_id = %s", [int(team_id)]
    except (ValueError, TypeError):
        return "", []


def _csv_response(rows, columns, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c["label"] for c in columns])
    for row in rows:
        writer.writerow([row.get(c["key"], "") for c in columns])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================
# Config
# ============================================================

@reports_bp.route("/config", methods=["GET"])
@require_permission("reports.view")
def report_config():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    tier = _get_plan_tier(tenant_id)
    is_paid = tier in ("paid", "trial")

    reports = []
    for r in ALL_REPORTS:
        is_free = r in FREE_REPORTS
        reports.append({
            "id": r,
            "tier": "free" if is_free else "paid",
            "accessible": is_free or is_paid,
        })

    return jsonify({"plan_tier": tier, "reports": reports})


# ============================================================
# FREE: Ticket Volume
# ============================================================

@reports_bp.route("/ticket-volume", methods=["GET"])
@require_permission("reports.view")
def ticket_volume():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    group_by = request.args.get("group_by", "day")
    if group_by not in ("day", "week", "month"):
        group_by = "day"

    loc_cte, loc_cond, loc_params = _location_cte()
    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")

    where_parts = [f"t.tenant_id = %s"] + date_conds
    if loc_cond:
        where_parts.append(loc_cond)
    if team_cond:
        where_parts.append(team_cond)
    where = " AND ".join(where_parts)
    params = loc_params + [tenant_id] + date_params + team_params

    rows = fetch_all(
        f"""{loc_cte}SELECT date_trunc(%s, t.created_at)::date as period,
                   count(*) as total,
                   count(*) FILTER (WHERE t.status = 'open') as open,
                   count(*) FILTER (WHERE t.status = 'pending') as pending,
                   count(*) FILTER (WHERE t.status = 'resolved') as resolved,
                   count(*) FILTER (WHERE t.status = 'closed_not_resolved') as closed_not_resolved
            FROM tickets t
            WHERE {where}
            GROUP BY 1
            ORDER BY 1""",
        [group_by] + params,
    )

    return jsonify({"rows": rows or []})


# ============================================================
# FREE: Status Breakdown
# ============================================================

@reports_bp.route("/status-breakdown", methods=["GET"])
@require_permission("reports.view")
def status_breakdown():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    loc_cte, loc_cond, loc_params = _location_cte()
    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")

    where_parts = [f"t.tenant_id = %s"] + date_conds
    if loc_cond:
        where_parts.append(loc_cond)
    if team_cond:
        where_parts.append(team_cond)
    where = " AND ".join(where_parts)
    params = loc_params + [tenant_id] + date_params + team_params

    rows = fetch_all(
        f"""{loc_cte}SELECT t.status, t.priority, count(*) as count
           FROM tickets t
           WHERE {where}
           GROUP BY t.status, t.priority
           ORDER BY
             CASE t.status WHEN 'open' THEN 0 WHEN 'pending' THEN 1 WHEN 'resolved' THEN 2 ELSE 3 END,
             CASE t.priority WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 WHEN 'p3' THEN 2 ELSE 3 END""",
        params,
    )

    return jsonify({"rows": rows or []})


# ============================================================
# FREE: Category Breakdown
# ============================================================

@reports_bp.route("/category-breakdown", methods=["GET"])
@require_permission("reports.view")
def category_breakdown():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    limit = min(int(request.args.get("limit", 25)), 100)
    loc_cte, loc_cond, loc_params = _location_cte()
    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")

    ticket_join_conds = [f"t.tenant_id = %s"] + date_conds
    if loc_cond:
        ticket_join_conds.append(loc_cond.replace("t.location_id", "t.location_id"))
    if team_cond:
        ticket_join_conds.append(team_cond)
    join_where = " AND ".join(ticket_join_conds)

    rows = fetch_all(
        f"""{loc_cte}SELECT pc.id as category_id, pc.parent_id, pc.name as category_name,
                   count(t.id) as ticket_count,
                   avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                       FILTER (WHERE t.resolved_at IS NOT NULL) as avg_resolution_hours
            FROM problem_categories pc
            LEFT JOIN tickets t ON t.problem_category_id = pc.id
              AND {join_where}
            WHERE pc.tenant_id = %s AND pc.is_active = true
            GROUP BY pc.id, pc.parent_id, pc.name
            ORDER BY count(t.id) DESC
            LIMIT %s""",
        loc_params + [tenant_id] + date_params + team_params + [tenant_id, limit],
    )

    return jsonify({"rows": rows or []})


# ============================================================
# FREE: Aging Tickets
# ============================================================

@reports_bp.route("/aging-tickets", methods=["GET"])
@require_permission("reports.view")
def aging_tickets():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    loc_cte, loc_cond, loc_params = _location_cte()
    priority_filter = request.args.get("priority")

    where_parts = ["t.tenant_id = %s", "t.status IN ('open', 'pending')"]
    params = loc_params + [tenant_id]

    if priority_filter and priority_filter in ("p1", "p2", "p3", "p4"):
        where_parts.append("t.priority = %s")
        params.append(priority_filter)
    if loc_cond:
        where_parts.append(loc_cond)

    where = " AND ".join(where_parts)

    # Summary buckets
    buckets = fetch_all(
        f"""{loc_cte}SELECT
                CASE
                    WHEN now() - t.updated_at < interval '1 day'   THEN '0_under_1d'
                    WHEN now() - t.updated_at < interval '3 days'  THEN '1_1_to_3d'
                    WHEN now() - t.updated_at < interval '7 days'  THEN '2_3_to_7d'
                    WHEN now() - t.updated_at < interval '14 days' THEN '3_7_to_14d'
                    WHEN now() - t.updated_at < interval '30 days' THEN '4_14_to_30d'
                    ELSE '5_over_30d'
                END as bucket_key,
                CASE
                    WHEN now() - t.updated_at < interval '1 day'   THEN '< 1 day'
                    WHEN now() - t.updated_at < interval '3 days'  THEN '1–3 days'
                    WHEN now() - t.updated_at < interval '7 days'  THEN '3–7 days'
                    WHEN now() - t.updated_at < interval '14 days' THEN '7–14 days'
                    WHEN now() - t.updated_at < interval '30 days' THEN '14–30 days'
                    ELSE '30+ days'
                END as age_bucket,
                count(*) as total,
                count(*) FILTER (WHERE t.priority = 'p1') as p1,
                count(*) FILTER (WHERE t.priority = 'p2') as p2,
                count(*) FILTER (WHERE t.priority = 'p3') as p3,
                count(*) FILTER (WHERE t.priority = 'p4') as p4
            FROM tickets t
            WHERE {where}
            GROUP BY bucket_key, age_bucket
            ORDER BY bucket_key""",
        params,
    )

    # Stale ticket list (not updated in 3+ days), ordered oldest first
    stale_where_parts = where_parts + ["now() - t.updated_at >= interval '3 days'"]
    stale_params = list(params)

    stale = fetch_all(
        f"""{loc_cte}SELECT t.id, t.subject, t.priority, t.status,
                   t.created_at, t.updated_at,
                   u.name as assignee_name,
                   l.name as location_name,
                   pc.name as category_name,
                   ROUND(EXTRACT(EPOCH FROM (now() - t.updated_at)) / 86400, 1) as days_stale
            FROM tickets t
            LEFT JOIN users u ON u.id = t.assignee_id
            LEFT JOIN locations l ON l.id = t.location_id
            LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
            WHERE {' AND '.join(stale_where_parts)}
            ORDER BY t.updated_at ASC
            LIMIT 100""",
        stale_params,
    )

    return jsonify({"buckets": buckets or [], "stale_tickets": stale or []})


# ============================================================
# PAID: SLA Compliance
# ============================================================

@reports_bp.route("/sla-compliance", methods=["GET"])
@require_permission("reports.view")
def sla_compliance():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    loc_cte, loc_cond, loc_params = _location_cte()
    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")

    where_parts = [f"t.tenant_id = %s"] + date_conds
    if loc_cond:
        where_parts.append(loc_cond)
    if team_cond:
        where_parts.append(team_cond)
    where = " AND ".join(where_parts)
    params = loc_params + [tenant_id] + date_params + team_params

    performance = fetch_all(
        f"""{loc_cte}SELECT t.priority,
                   count(*) as total,
                   count(*) FILTER (WHERE t.sla_breached = true) as breached,
                   ROUND(100.0 * count(*) FILTER (WHERE t.sla_breached = true) / NULLIF(count(*), 0), 1) as breach_rate,
                   ROUND(avg(EXTRACT(EPOCH FROM (
                       COALESCE(
                           (SELECT min(tc.created_at) FROM ticket_comments tc
                            WHERE tc.ticket_id = t.id AND tc.author_id != t.requester_id AND tc.is_ai_generated = false),
                           t.first_response_at
                       ) - t.created_at)) / 60)
                       FILTER (WHERE t.first_response_at IS NOT NULL
                                  OR EXISTS (SELECT 1 FROM ticket_comments tc2
                                             WHERE tc2.ticket_id = t.id AND tc2.author_id != t.requester_id AND tc2.is_ai_generated = false)),
                   1) as avg_first_response_minutes,
                   ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 60)
                       FILTER (WHERE t.resolved_at IS NOT NULL), 1) as avg_resolution_minutes
            FROM tickets t
            WHERE {where}
            GROUP BY t.priority
            ORDER BY CASE t.priority WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 WHEN 'p3' THEN 2 ELSE 3 END""",
        params,
    )

    policies = fetch_all(
        """SELECT priority, first_response_minutes, resolution_minutes
           FROM sla_policies
           WHERE tenant_id = %s
           ORDER BY CASE priority WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 WHEN 'p3' THEN 2 ELSE 3 END""",
        [tenant_id],
    )

    return jsonify({"performance": performance or [], "policies": policies or []})


# ============================================================
# PAID: Agent Performance
# ============================================================

@reports_bp.route("/ticket-volume/breakdown", methods=["GET"])
@require_permission("reports.view")
def ticket_volume_breakdown():
    """Per-agent breakdown for a single period in the ticket volume report."""
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    period = request.args.get("period")  # e.g. "2026-03-20"
    group_by = request.args.get("group_by", "day")
    if group_by not in ("day", "week", "month"):
        group_by = "day"
    if not period:
        return jsonify({"error": "period required"}), 400

    # Build date range for this single period bucket
    interval_map = {"day": "1 day", "week": "1 week", "month": "1 month"}
    interval = interval_map[group_by]

    loc_cte, loc_cond, loc_params = _location_cte()
    where_parts = [
        "t.tenant_id = %s",
        "t.created_at >= %s::date",
        f"t.created_at < %s::date + interval '{interval}'",
        "t.assignee_id IS NOT NULL",
    ]
    if loc_cond:
        where_parts.append(loc_cond)
    where = " AND ".join(where_parts)
    params = loc_params + [tenant_id, period, period]

    rows = fetch_all(
        f"""{loc_cte}SELECT
                COALESCE(u.name, 'Unknown') as agent_name,
                u.role,
                count(t.id) as ticket_count,
                count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                count(t.id) FILTER (WHERE t.status = 'open') as open_count,
                count(t.id) FILTER (WHERE t.status = 'pending') as pending_count,
                ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                    FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours
            FROM tickets t
            JOIN users u ON u.id = t.assignee_id
            WHERE {where}
            GROUP BY u.id, u.name, u.role
            ORDER BY count(t.id) DESC""",
        params,
    )

    return jsonify({"agents": rows or []})


@reports_bp.route("/agent-performance", methods=["GET"])
@require_permission("reports.view")
def agent_performance():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    loc_cte, loc_cond, loc_params = _location_cte()
    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")

    ticket_join_conds = [f"t.tenant_id = %s"] + date_conds
    if loc_cond:
        ticket_join_conds.append(loc_cond)
    if team_cond:
        ticket_join_conds.append(team_cond)
    join_where = " AND ".join(ticket_join_conds)

    # Start from tickets to capture ANY user who handled tickets (regardless of role)
    agents = fetch_all(
        f"""{loc_cte}SELECT u.id as agent_id,
                   COALESCE(u.name, 'Unknown') as agent_name,
                   u.role,
                   count(t.id) as ticket_count,
                   count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                   ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                       FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours,
                   ROUND(100.0 * count(tm.id) FILTER (WHERE tm.resolved_first_contact = true)
                       / NULLIF(count(tm.id) FILTER (WHERE tm.resolved_first_contact IS NOT NULL), 0), 1) as fcr_rate,
                   ROUND(avg(tm.effort_score)::numeric FILTER (WHERE tm.effort_score IS NOT NULL), 1) as avg_effort,
                   count(tm.id) FILTER (WHERE tm.resolution_type IN ('ai_l1', 'ai_l2')) as ai_resolved,
                   count(tm.id) FILTER (WHERE tm.resolution_type = 'human') as human_resolved,
                   count(tm.id) FILTER (WHERE tm.resolution_type = 'hybrid') as hybrid_resolved
            FROM tickets t
            JOIN users u ON u.id = t.assignee_id
            LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
            WHERE {join_where}
            GROUP BY u.id, u.name, u.role
            ORDER BY count(t.id) DESC""",
        loc_params + [tenant_id] + date_params + team_params,
    )

    # Add Atlas AI as a virtual agent row if it resolved any tickets
    date_conds_ae, date_params_ae = _date_conditions("ae.created_at")
    atlas_row = fetch_one(
        f"""SELECT
                count(ae.id) as ticket_count,
                count(ae.id) FILTER (WHERE ae.resolved_by_ai = true) as resolved_count,
                ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                    FILTER (WHERE ae.resolved_by_ai = true AND t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours
            FROM atlas_engagements ae
            JOIN tickets t ON t.id = ae.ticket_id
            WHERE ae.tenant_id = %s AND {' AND '.join(date_conds_ae)}""",
        [tenant_id] + date_params_ae,
    )

    agent_list = list(agents or [])
    if atlas_row and (atlas_row.get("ticket_count") or 0) > 0:
        agent_list.insert(0, {
            "agent_id": -1,
            "agent_name": "Atlas AI",
            "role": "ai",
            "ticket_count": atlas_row["ticket_count"],
            "resolved_count": atlas_row["resolved_count"] or 0,
            "avg_resolution_hours": atlas_row["avg_resolution_hours"],
            "fcr_rate": None,
            "avg_effort": None,
            "ai_resolved": atlas_row["resolved_count"] or 0,
            "human_resolved": 0,
            "hybrid_resolved": 0,
        })

    return jsonify({"agents": agent_list})


# ============================================================
# PAID: AI Effectiveness
# ============================================================

@reports_bp.route("/ai-effectiveness", methods=["GET"])
@require_permission("reports.view")
def ai_effectiveness():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    date_conds_ae, date_params_ae = _date_conditions("ae.created_at")
    date_conds_tu, date_params_tu = _date_conditions("created_at")

    summary = fetch_one(
        f"""SELECT
              count(*) as total_engagements,
              count(*) FILTER (WHERE ae.resolved_by_ai = true) as ai_resolved,
              ROUND(100.0 * count(*) FILTER (WHERE ae.resolved_by_ai = true) / NULLIF(count(*), 0), 1) as ai_resolution_rate,
              count(*) FILTER (WHERE ae.engagement_type = 'l1') as l1_count,
              count(*) FILTER (WHERE ae.engagement_type = 'l2') as l2_count,
              count(*) FILTER (WHERE ae.human_took_over = true) as human_takeover_count,
              ROUND(100.0 * count(*) FILTER (WHERE ae.human_took_over = true) / NULLIF(count(*), 0), 1) as escalation_rate,
              ROUND(avg(tm.ai_turns_before_resolve)::numeric FILTER (WHERE tm.ai_turns_before_resolve > 0), 1) as avg_turns_before_resolve
            FROM atlas_engagements ae
            LEFT JOIN ticket_metrics tm ON tm.ticket_id = ae.ticket_id
            WHERE ae.tenant_id = %s AND {' AND '.join(date_conds_ae)}""",
        [tenant_id] + date_params_ae,
    )

    cost = fetch_one(
        f"""SELECT
              count(DISTINCT ticket_id) as tickets_with_ai_cost,
              ROUND(sum(cost_usd)::numeric, 2) as total_cost,
              ROUND(avg(ticket_cost)::numeric, 4) as avg_cost_per_ticket
            FROM (
              SELECT ticket_id, sum(cost_usd) as ticket_cost
              FROM tenant_token_usage
              WHERE tenant_id = %s
                AND ticket_id IS NOT NULL
                AND {' AND '.join(date_conds_tu)}
              GROUP BY ticket_id
            ) sub""",
        [tenant_id] + date_params_tu,
    )

    cost_data = cost or {}
    if user["role"] != "super_admin":
        cost_data = {
            "avg_cost_per_ticket": (cost or {}).get("avg_cost_per_ticket"),
            "tickets_with_ai_cost": (cost or {}).get("tickets_with_ai_cost"),
        }

    return jsonify({"summary": summary or {}, "cost": cost_data})


# ============================================================
# PAID: Routing Insights (N+1 fixed, date filter added)
# ============================================================

@reports_bp.route("/routing-insights", methods=["GET"])
@require_permission("reports.view")
def routing_insights():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    date_conds, date_params = _date_conditions("t.created_at")
    date_where = " AND ".join(date_conds)

    category_coverage = fetch_all(
        f"""SELECT pc.id as category_id, pc.name as category_name, pc.parent_id,
                  count(DISTINCT t.assignee_id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND {date_where}) as agents_with_experience,
                  count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND {date_where}) as total_resolved,
                  count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_tickets
           FROM problem_categories pc
           LEFT JOIN tickets t ON t.problem_category_id = pc.id AND t.tenant_id = %s
           WHERE pc.tenant_id = %s AND pc.is_active = true
           GROUP BY pc.id, pc.name, pc.parent_id
           ORDER BY pc.name""",
        date_params + date_params + [tenant_id, tenant_id],
    )

    agent_specializations = fetch_all(
        f"""SELECT u.id as agent_id, u.name as agent_name,
                  count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                      AND {date_where}) as total_resolved,
                  count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_tickets,
                  coalesce(avg(tm.effort_score) FILTER (WHERE tm.effort_score IS NOT NULL), 0) as avg_effort
           FROM users u
           LEFT JOIN tickets t ON t.assignee_id = u.id AND t.tenant_id = %s
           LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
           WHERE u.tenant_id = %s AND u.role IN ('agent', 'tenant_admin') AND u.is_active = true
           GROUP BY u.id, u.name
           ORDER BY total_resolved DESC""",
        date_params + [tenant_id, tenant_id],
    )

    # Batch top-categories query (fixes N+1)
    agent_ids = [a["agent_id"] for a in (agent_specializations or [])]
    top_cats_by_agent: dict = {}
    if agent_ids:
        placeholders = ",".join(["%s"] * len(agent_ids))
        all_top_cats = fetch_all(
            f"""SELECT t.assignee_id as agent_id, pc.name as category, count(*) as count
               FROM tickets t
               JOIN problem_categories pc ON pc.id = t.problem_category_id
               WHERE t.assignee_id IN ({placeholders})
                 AND t.tenant_id = %s
                 AND t.status IN ('resolved', 'closed_not_resolved')
                 AND {date_where}
               GROUP BY t.assignee_id, pc.name""",
            agent_ids + [tenant_id] + date_params,
        )
        for row in (all_top_cats or []):
            top_cats_by_agent.setdefault(row["agent_id"], []).append(row)

        for agent_id, cats in top_cats_by_agent.items():
            top_cats_by_agent[agent_id] = sorted(cats, key=lambda x: -x["count"])[:5]

    for agent in (agent_specializations or []):
        agent["top_categories"] = top_cats_by_agent.get(agent["agent_id"], [])

    coverage_gaps = [
        c for c in (category_coverage or [])
        if c.get("agents_with_experience", 0) <= 1
    ]

    return jsonify({
        "category_coverage": category_coverage or [],
        "agent_specializations": agent_specializations or [],
        "coverage_gaps": coverage_gaps,
    })


# ============================================================
# PAID: Location Breakdown
# ============================================================

@reports_bp.route("/location-breakdown", methods=["GET"])
@require_permission("reports.view")
def location_breakdown():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    team_cond, team_params = _team_condition()
    date_conds, date_params = _date_conditions("t.created_at")
    date_where = " AND ".join(date_conds)

    team_where = f" AND {team_cond}" if team_cond else ""

    # Ticket counts per location (only locations that have tickets)
    loc_rows = fetch_all(
        f"""SELECT l.id as location_id, l.name as location_name, l.parent_id,
                   count(t.id) as ticket_count,
                   count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_count,
                   count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                   ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                       FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours,
                   count(t.id) FILTER (WHERE t.sla_breached = true) as breach_count,
                   ROUND(100.0 * count(t.id) FILTER (WHERE t.sla_breached = true) / NULLIF(count(t.id), 0), 1) as breach_rate
            FROM tickets t
            JOIN locations l ON l.id = t.location_id
            WHERE t.tenant_id = %s AND {date_where}{team_where}
            GROUP BY l.id, l.name, l.parent_id
            ORDER BY count(t.id) DESC""",
        [tenant_id] + date_params + team_params,
    )

    # No-location tickets (location_id IS NULL)
    no_loc = fetch_one(
        f"""SELECT count(t.id) as ticket_count,
                   count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_count,
                   count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                   ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                       FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours,
                   count(t.id) FILTER (WHERE t.sla_breached = true) as breach_count,
                   ROUND(100.0 * count(t.id) FILTER (WHERE t.sla_breached = true) / NULLIF(count(t.id), 0), 1) as breach_rate
            FROM tickets t
            WHERE t.tenant_id = %s AND t.location_id IS NULL AND {date_where}{team_where}""",
        [tenant_id] + date_params + team_params,
    )

    rows = list(loc_rows or [])

    # Pull full location tree for this tenant so we can insert ancestor nodes
    # that have 0 direct tickets (needed for proper tree nesting in the frontend)
    all_locs = fetch_all(
        "SELECT id, name, parent_id FROM locations WHERE tenant_id = %s AND is_active = true",
        [tenant_id],
    )
    loc_by_id = {l["id"]: l for l in (all_locs or [])}

    known_ids = {r["location_id"] for r in rows}
    parents_needed = {r["parent_id"] for r in rows if r.get("parent_id") and r["parent_id"] not in known_ids}

    while parents_needed:
        next_needed = set()
        for pid in parents_needed:
            if pid not in known_ids and pid in loc_by_id:
                loc = loc_by_id[pid]
                rows.append({
                    "location_id": loc["id"],
                    "location_name": loc["name"],
                    "parent_id": loc["parent_id"],
                    "ticket_count": 0,
                    "open_count": 0,
                    "resolved_count": 0,
                    "avg_resolution_hours": None,
                    "breach_count": 0,
                    "breach_rate": None,
                })
                known_ids.add(pid)
                if loc["parent_id"] and loc["parent_id"] not in known_ids:
                    next_needed.add(loc["parent_id"])
        parents_needed = next_needed

    if no_loc and (no_loc.get("ticket_count") or 0) > 0:
        rows.append({
            "location_id": None,
            "location_name": "(No Location)",
            "parent_id": None,
            "ticket_count": no_loc.get("ticket_count", 0),
            "open_count": no_loc.get("open_count", 0),
            "resolved_count": no_loc.get("resolved_count", 0),
            "avg_resolution_hours": no_loc.get("avg_resolution_hours"),
            "breach_count": no_loc.get("breach_count", 0),
            "breach_rate": no_loc.get("breach_rate"),
        })

    return jsonify({"rows": rows})


# ============================================================
# PAID: Ticket Export (raw CSV dump with all fields)
# ============================================================

@reports_bp.route("/ticket-export/csv", methods=["GET"])
@require_permission("reports.view")
def ticket_export_csv():
    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    loc_cte, loc_cond, loc_params = _location_cte()
    date_conds, date_params = _date_conditions("t.created_at")

    where_parts = ["t.tenant_id = %s"] + date_conds
    if loc_cond:
        where_parts.append(loc_cond)

    # Optional filters
    status_filter = request.args.get("status")
    priority_filter = request.args.get("priority")
    if status_filter in ("open", "pending", "resolved", "closed_not_resolved"):
        where_parts.append("t.status = %s")
        date_params.append(status_filter)
    if priority_filter in ("p1", "p2", "p3", "p4"):
        where_parts.append("t.priority = %s")
        date_params.append(priority_filter)

    where = " AND ".join(where_parts)
    params = loc_params + [tenant_id] + date_params

    rows = fetch_all(
        f"""{loc_cte}SELECT
                t.id,
                t.subject,
                t.status,
                t.priority,
                t.created_at,
                t.updated_at,
                t.first_response_at,
                t.resolved_at,
                t.sla_breached,
                u_req.name as requester,
                u_req.email as requester_email,
                u_asgn.name as assignee,
                l.name as location,
                pc.name as category,
                te.name as team_name,
                tm.resolved_first_contact as fcr,
                tm.effort_score,
                tm.resolution_type,
                tm.ai_turns_before_resolve,
                ae.created_at as atlas_engaged_at,
                ROUND(EXTRACT(EPOCH FROM (COALESCE(t.resolved_at, now()) - t.created_at)) / 3600, 2) as age_hours
            FROM tickets t
            LEFT JOIN users u_req ON u_req.id = t.requester_id
            LEFT JOIN users u_asgn ON u_asgn.id = t.assignee_id
            LEFT JOIN locations l ON l.id = t.location_id
            LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
            LEFT JOIN teams te ON te.id = t.team_id
            LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
            LEFT JOIN LATERAL (
                SELECT created_at FROM atlas_engagements
                WHERE ticket_id = t.id ORDER BY created_at ASC LIMIT 1
            ) ae ON true
            WHERE {where}
            ORDER BY t.created_at DESC
            LIMIT 5000""",
        params,
    )

    columns = [
        {"key": "id", "label": "Ticket ID"},
        {"key": "subject", "label": "Subject"},
        {"key": "status", "label": "Status"},
        {"key": "priority", "label": "Priority"},
        {"key": "created_at", "label": "Created At"},
        {"key": "updated_at", "label": "Updated At"},
        {"key": "first_response_at", "label": "First Response At"},
        {"key": "resolved_at", "label": "Resolved At"},
        {"key": "atlas_engaged_at", "label": "Atlas Engaged At"},
        {"key": "sla_breached", "label": "SLA Breached"},
        {"key": "age_hours", "label": "Age (hrs)"},
        {"key": "requester", "label": "Requester"},
        {"key": "requester_email", "label": "Requester Email"},
        {"key": "assignee", "label": "Assignee"},
        {"key": "location", "label": "Location"},
        {"key": "category", "label": "Category"},
        {"key": "team_name", "label": "Team"},
        {"key": "fcr", "label": "FCR"},
        {"key": "effort_score", "label": "Effort Score"},
        {"key": "resolution_type", "label": "Resolution Type"},
        {"key": "ai_turns_before_resolve", "label": "AI Turns"},
    ]

    from datetime import date
    filename = f"tickets-export-{date.today().isoformat()}.csv"
    return _csv_response(rows or [], columns, filename)


# ============================================================
# CSV Export (existing reports)
# ============================================================

_CSV_COLUMNS = {
    "ticket-volume": [
        {"key": "period", "label": "Period"},
        {"key": "total", "label": "Total"},
        {"key": "open", "label": "Open"},
        {"key": "pending", "label": "Pending"},
        {"key": "resolved", "label": "Resolved"},
        {"key": "closed_not_resolved", "label": "Closed (Not Resolved)"},
    ],
    "status-breakdown": [
        {"key": "status", "label": "Status"},
        {"key": "priority", "label": "Priority"},
        {"key": "count", "label": "Count"},
    ],
    "category-breakdown": [
        {"key": "category_name", "label": "Category"},
        {"key": "ticket_count", "label": "Tickets"},
        {"key": "avg_resolution_hours", "label": "Avg Resolution (hrs)"},
    ],
    "aging-tickets": [
        {"key": "age_bucket", "label": "Age"},
        {"key": "total", "label": "Total"},
        {"key": "p1", "label": "P1 (Urgent)"},
        {"key": "p2", "label": "P2 (High)"},
        {"key": "p3", "label": "P3 (Medium)"},
        {"key": "p4", "label": "P4 (Low)"},
    ],
    "sla-compliance": [
        {"key": "priority", "label": "Priority"},
        {"key": "total", "label": "Total"},
        {"key": "breached", "label": "Breached"},
        {"key": "breach_rate", "label": "Breach Rate (%)"},
        {"key": "avg_first_response_minutes", "label": "Avg First Response (min)"},
        {"key": "avg_resolution_minutes", "label": "Avg Resolution (min)"},
    ],
    "agent-performance": [
        {"key": "agent_name", "label": "Agent"},
        {"key": "ticket_count", "label": "Tickets"},
        {"key": "resolved_count", "label": "Resolved"},
        {"key": "avg_resolution_hours", "label": "Avg Resolution (hrs)"},
        {"key": "fcr_rate", "label": "FCR Rate (%)"},
        {"key": "avg_effort", "label": "Avg Effort"},
        {"key": "ai_resolved", "label": "AI Resolved"},
        {"key": "human_resolved", "label": "Human Resolved"},
        {"key": "hybrid_resolved", "label": "Hybrid"},
    ],
    "ai-effectiveness": [
        {"key": "total_engagements", "label": "Total Engagements"},
        {"key": "ai_resolved", "label": "AI Resolved"},
        {"key": "ai_resolution_rate", "label": "AI Resolution Rate (%)"},
        {"key": "l1_count", "label": "L1 Count"},
        {"key": "l2_count", "label": "L2 Count"},
        {"key": "human_takeover_count", "label": "Human Takeovers"},
        {"key": "escalation_rate", "label": "Escalation Rate (%)"},
        {"key": "avg_turns_before_resolve", "label": "Avg Turns"},
    ],
    "location-breakdown": [
        {"key": "location_name", "label": "Location"},
        {"key": "parent_name", "label": "Parent"},
        {"key": "ticket_count", "label": "Total Tickets"},
        {"key": "open_count", "label": "Open"},
        {"key": "resolved_count", "label": "Resolved"},
        {"key": "avg_resolution_hours", "label": "Avg Resolution (hrs)"},
        {"key": "breach_count", "label": "SLA Breaches"},
        {"key": "breach_rate", "label": "Breach Rate (%)"},
    ],
}


@reports_bp.route("/<report_id>/csv", methods=["GET"])
@require_permission("reports.view")
def export_csv(report_id: str):
    if report_id not in _CSV_COLUMNS:
        return jsonify({"error": f"Unknown report: {report_id}"}), 404

    user = get_current_user()
    tenant_id, err = _resolve_tenant(user)
    if err:
        return err

    paid_err = _require_paid(tenant_id)
    if paid_err:
        return paid_err

    columns = _CSV_COLUMNS[report_id]
    loc_cte, loc_cond, loc_params = _location_cte()

    if report_id == "ticket-volume":
        group_by = request.args.get("group_by", "day")
        if group_by not in ("day", "week", "month"):
            group_by = "day"
        date_conds, date_params = _date_conditions("t.created_at")
        where_parts = ["t.tenant_id = %s"] + date_conds
        if loc_cond:
            where_parts.append(loc_cond)
        where = " AND ".join(where_parts)
        rows = fetch_all(
            f"""{loc_cte}SELECT date_trunc(%s, t.created_at)::date as period,
                       count(*) as total,
                       count(*) FILTER (WHERE t.status = 'open') as open,
                       count(*) FILTER (WHERE t.status = 'pending') as pending,
                       count(*) FILTER (WHERE t.status = 'resolved') as resolved,
                       count(*) FILTER (WHERE t.status = 'closed_not_resolved') as closed_not_resolved
                FROM tickets t WHERE {where} GROUP BY 1 ORDER BY 1""",
            [group_by] + loc_params + [tenant_id] + date_params,
        )

    elif report_id == "status-breakdown":
        date_conds, date_params = _date_conditions("t.created_at")
        where_parts = ["t.tenant_id = %s"] + date_conds
        if loc_cond:
            where_parts.append(loc_cond)
        where = " AND ".join(where_parts)
        rows = fetch_all(
            f"""{loc_cte}SELECT t.status, t.priority, count(*) as count
               FROM tickets t WHERE {where}
               GROUP BY t.status, t.priority ORDER BY t.status, t.priority""",
            loc_params + [tenant_id] + date_params,
        )

    elif report_id == "category-breakdown":
        limit = min(int(request.args.get("limit", 25)), 100)
        date_conds, date_params = _date_conditions("t.created_at")
        join_conds = ["t.tenant_id = %s"] + date_conds
        if loc_cond:
            join_conds.append(loc_cond)
        rows = fetch_all(
            f"""{loc_cte}SELECT pc.name as category_name, count(t.id) as ticket_count,
                       avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                           FILTER (WHERE t.resolved_at IS NOT NULL) as avg_resolution_hours
                FROM problem_categories pc
                LEFT JOIN tickets t ON t.problem_category_id = pc.id AND {' AND '.join(join_conds)}
                WHERE pc.tenant_id = %s AND pc.is_active = true
                GROUP BY pc.id, pc.name ORDER BY count(t.id) DESC LIMIT %s""",
            loc_params + [tenant_id] + date_params + [tenant_id, limit],
        )

    elif report_id == "aging-tickets":
        where_parts = ["t.tenant_id = %s", "t.status IN ('open', 'pending')"]
        params = loc_params + [tenant_id]
        if loc_cond:
            where_parts.append(loc_cond)
        where = " AND ".join(where_parts)
        rows = fetch_all(
            f"""{loc_cte}SELECT
                    CASE
                        WHEN now() - t.updated_at < interval '1 day'   THEN '< 1 day'
                        WHEN now() - t.updated_at < interval '3 days'  THEN '1–3 days'
                        WHEN now() - t.updated_at < interval '7 days'  THEN '3–7 days'
                        WHEN now() - t.updated_at < interval '14 days' THEN '7–14 days'
                        WHEN now() - t.updated_at < interval '30 days' THEN '14–30 days'
                        ELSE '30+ days'
                    END as age_bucket,
                    count(*) as total,
                    count(*) FILTER (WHERE t.priority = 'p1') as p1,
                    count(*) FILTER (WHERE t.priority = 'p2') as p2,
                    count(*) FILTER (WHERE t.priority = 'p3') as p3,
                    count(*) FILTER (WHERE t.priority = 'p4') as p4
                FROM tickets t WHERE {where}
                GROUP BY age_bucket ORDER BY min(now() - t.updated_at)""",
            params,
        )

    elif report_id == "sla-compliance":
        date_conds, date_params = _date_conditions("t.created_at")
        where_parts = ["t.tenant_id = %s"] + date_conds
        if loc_cond:
            where_parts.append(loc_cond)
        where = " AND ".join(where_parts)
        rows = fetch_all(
            f"""{loc_cte}SELECT t.priority, count(*) as total,
                       count(*) FILTER (WHERE t.sla_breached = true) as breached,
                       ROUND(100.0 * count(*) FILTER (WHERE t.sla_breached = true) / NULLIF(count(*), 0), 1) as breach_rate,
                       ROUND(avg(EXTRACT(EPOCH FROM (t.first_response_at - t.created_at)) / 60)
                           FILTER (WHERE t.first_response_at IS NOT NULL), 1) as avg_first_response_minutes,
                       ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 60)
                           FILTER (WHERE t.resolved_at IS NOT NULL), 1) as avg_resolution_minutes
                FROM tickets t WHERE {where}
                GROUP BY t.priority ORDER BY t.priority""",
            loc_params + [tenant_id] + date_params,
        )

    elif report_id == "agent-performance":
        date_conds, date_params = _date_conditions("t.created_at")
        join_conds = ["t.tenant_id = %s"] + date_conds
        if loc_cond:
            join_conds.append(loc_cond)
        rows = fetch_all(
            f"""{loc_cte}SELECT u.name as agent_name, count(t.id) as ticket_count,
                       count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                       ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                           FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours,
                       ROUND(100.0 * count(tm.id) FILTER (WHERE tm.resolved_first_contact = true)
                           / NULLIF(count(tm.id) FILTER (WHERE tm.resolved_first_contact IS NOT NULL), 0), 1) as fcr_rate,
                       ROUND(avg(tm.effort_score)::numeric FILTER (WHERE tm.effort_score IS NOT NULL), 1) as avg_effort,
                       count(tm.id) FILTER (WHERE tm.resolution_type IN ('ai_l1', 'ai_l2')) as ai_resolved,
                       count(tm.id) FILTER (WHERE tm.resolution_type = 'human') as human_resolved,
                       count(tm.id) FILTER (WHERE tm.resolution_type = 'hybrid') as hybrid_resolved
                FROM users u
                LEFT JOIN tickets t ON t.assignee_id = u.id AND {' AND '.join(join_conds)}
                LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
                WHERE u.tenant_id = %s AND u.role IN ('agent', 'tenant_admin') AND u.is_active = true
                GROUP BY u.id, u.name ORDER BY count(t.id) DESC""",
            loc_params + [tenant_id] + date_params + [tenant_id],
        )

    elif report_id == "ai-effectiveness":
        date_conds, date_params = _date_conditions("ae.created_at")
        summary = fetch_one(
            f"""SELECT count(*) as total_engagements,
                       count(*) FILTER (WHERE ae.resolved_by_ai = true) as ai_resolved,
                       ROUND(100.0 * count(*) FILTER (WHERE ae.resolved_by_ai = true) / NULLIF(count(*), 0), 1) as ai_resolution_rate,
                       count(*) FILTER (WHERE ae.engagement_type = 'l1') as l1_count,
                       count(*) FILTER (WHERE ae.engagement_type = 'l2') as l2_count,
                       count(*) FILTER (WHERE ae.human_took_over = true) as human_takeover_count,
                       ROUND(100.0 * count(*) FILTER (WHERE ae.human_took_over = true) / NULLIF(count(*), 0), 1) as escalation_rate,
                       ROUND(avg(tm.ai_turns_before_resolve)::numeric FILTER (WHERE tm.ai_turns_before_resolve > 0), 1) as avg_turns_before_resolve
                FROM atlas_engagements ae
                LEFT JOIN ticket_metrics tm ON tm.ticket_id = ae.ticket_id
                WHERE ae.tenant_id = %s AND {' AND '.join(date_conds)}""",
            [tenant_id] + date_params,
        )
        rows = [summary] if summary else []

    elif report_id == "location-breakdown":
        date_conds, date_params = _date_conditions("t.created_at")
        where_parts = ["t.tenant_id = %s"] + date_conds
        where = " AND ".join(where_parts)
        rows = fetch_all(
            f"""SELECT COALESCE(l.name, '(No Location)') as location_name,
                       pl.name as parent_name,
                       count(t.id) as ticket_count,
                       count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_count,
                       count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')) as resolved_count,
                       ROUND(avg(EXTRACT(EPOCH FROM (t.resolved_at - t.created_at)) / 3600)
                           FILTER (WHERE t.resolved_at IS NOT NULL)::numeric, 1) as avg_resolution_hours,
                       count(t.id) FILTER (WHERE t.sla_breached = true) as breach_count,
                       ROUND(100.0 * count(t.id) FILTER (WHERE t.sla_breached = true) / NULLIF(count(t.id), 0), 1) as breach_rate
                FROM tickets t
                LEFT JOIN locations l ON l.id = t.location_id
                LEFT JOIN locations pl ON pl.id = l.parent_id
                WHERE {where}
                GROUP BY l.id, l.name, pl.name
                ORDER BY count(t.id) DESC""",
            [tenant_id] + date_params,
        )

    else:
        rows = []

    return _csv_response(rows or [], columns, f"{report_id}-report.csv")
