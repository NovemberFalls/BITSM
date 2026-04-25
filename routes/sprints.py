"""Sprint blueprint: sprint CRUD, board data, velocity reporting."""

import logging

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_permission, get_current_user
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
sprints_bp = Blueprint("sprints", __name__)


def _get_tenant_id():
    from routes.auth import get_tenant_id
    return get_tenant_id()


# ============================================================
# Sprint CRUD
# ============================================================

@sprints_bp.route("", methods=["GET"])
@login_required
def list_sprints():
    """List sprints for the current tenant, optionally filtered by team or status."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return jsonify([])

    team_id = request.args.get("team_id")
    status = request.args.get("status")

    conditions = ["s.tenant_id = %s"]
    params = [tenant_id]

    if team_id:
        conditions.append("s.team_id = %s")
        params.append(int(team_id))
    if status:
        conditions.append("s.status = %s")
        params.append(status)

    where = " AND ".join(conditions)
    sprints = fetch_all(
        f"""SELECT s.id, s.name, s.goal, s.start_date, s.end_date, s.status,
                   s.team_id, t.name as team_name, s.created_at,
                   (SELECT COUNT(*) FROM tickets tk WHERE tk.sprint_id = s.id) as ticket_count,
                   (SELECT COALESCE(SUM(tk.story_points), 0) FROM tickets tk WHERE tk.sprint_id = s.id) as total_points,
                   (SELECT COALESCE(SUM(tk.story_points), 0) FROM tickets tk
                    WHERE tk.sprint_id = s.id AND tk.status IN ('done', 'resolved', 'closed_not_resolved')) as completed_points
            FROM sprints s
            JOIN teams t ON t.id = s.team_id
            WHERE {where}
            ORDER BY
                CASE s.status WHEN 'active' THEN 0 WHEN 'planning' THEN 1 ELSE 2 END,
                s.start_date DESC NULLS LAST""",
        params,
    )
    return jsonify(sprints)


@sprints_bp.route("", methods=["POST"])
@login_required
@require_permission("sprints.manage")
def create_sprint():
    """Create a new sprint."""
    user = get_current_user()
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    name = data.get("name", "").strip()
    team_id = data.get("team_id")
    if not name:
        return jsonify({"error": "Sprint name is required"}), 400
    if not team_id:
        return jsonify({"error": "team_id is required"}), 400

    # Verify team belongs to tenant
    team = fetch_one("SELECT id FROM teams WHERE id = %s AND tenant_id = %s", [team_id, tenant_id])
    if not team:
        return jsonify({"error": "Team not found"}), 404

    sprint_id = insert_returning(
        """INSERT INTO sprints (tenant_id, team_id, name, goal, start_date, end_date, status, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, team_id, name, data.get("goal", ""),
         data.get("start_date"), data.get("end_date"),
         data.get("status", "planning"), user["id"]],
    )
    return jsonify({"id": sprint_id}), 201


@sprints_bp.route("/<int:sprint_id>", methods=["PUT"])
@login_required
@require_permission("sprints.manage")
def update_sprint(sprint_id: int):
    """Update sprint details or status."""
    tenant_id = _get_tenant_id()
    existing = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    fields, params = [], []
    for col in ("name", "goal", "start_date", "end_date", "status", "capacity_points"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(sprint_id)
    execute(f"UPDATE sprints SET {', '.join(fields)} WHERE id = %s", params)
    return jsonify({"ok": True})


@sprints_bp.route("/<int:sprint_id>", methods=["DELETE"])
@login_required
@require_permission("sprints.manage")
def delete_sprint(sprint_id: int):
    """Delete a sprint. Tickets in this sprint get sprint_id set to NULL."""
    tenant_id = _get_tenant_id()
    existing = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    execute("UPDATE tickets SET sprint_id = NULL WHERE sprint_id = %s", [sprint_id])
    execute("DELETE FROM sprints WHERE id = %s", [sprint_id])
    return jsonify({"ok": True})


# ============================================================
# Sprint Board — tickets grouped by status
# ============================================================

@sprints_bp.route("/<int:sprint_id>/board", methods=["GET"])
@login_required
def sprint_board(sprint_id: int):
    """Return sprint tickets grouped by status for kanban rendering."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id, name, goal, status, team_id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    tickets = fetch_all(
        """SELECT t.id, t.ticket_number, t.subject, t.status, t.priority,
                  t.ticket_type, t.story_points, t.assignee_id,
                  u.name as assignee_name,
                  wit.name as work_item_type_name,
                  wit.icon as work_item_type_icon,
                  wit.color as work_item_type_color
           FROM tickets t
           LEFT JOIN users u ON u.id = t.assignee_id
           LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
           WHERE t.sprint_id = %s
           ORDER BY
               CASE t.priority WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 WHEN 'p3' THEN 3 WHEN 'p4' THEN 4 END,
               t.created_at""",
        [sprint_id],
    )

    # Get the workflow for the ticket type (use first ticket's type, or default to 'task')
    from services.workflow_service import get_workflow
    ticket_type = tickets[0]["ticket_type"] if tickets else "task"
    workflow = get_workflow(tenant_id, ticket_type)

    # Group tickets by status
    columns: dict[str, list] = {s["key"]: [] for s in workflow}
    for t in tickets:
        status_key = t["status"]
        if status_key in columns:
            columns[status_key].append(t)
        else:
            # Ticket has a status not in the workflow — put it in first column
            first_key = workflow[0]["key"] if workflow else "backlog"
            columns.setdefault(first_key, []).append(t)

    return jsonify({
        "sprint": sprint,
        "workflow": workflow,
        "columns": columns,
        "total_points": sum(t.get("story_points") or 0 for t in tickets),
        "completed_points": sum(
            (t.get("story_points") or 0) for t in tickets
            if t["status"] in ("done", "resolved", "closed_not_resolved")
        ),
    })


# ============================================================
# Velocity — story points per sprint per team
# ============================================================

@sprints_bp.route("/velocity", methods=["GET"])
@login_required
def velocity():
    """Return velocity data: completed story points per sprint for a team."""
    tenant_id = _get_tenant_id()
    team_id = request.args.get("team_id")
    if not tenant_id:
        return jsonify([])

    conditions = ["s.tenant_id = %s", "s.status = 'completed'"]
    params = [tenant_id]
    if team_id:
        conditions.append("s.team_id = %s")
        params.append(int(team_id))

    where = " AND ".join(conditions)
    rows = fetch_all(
        f"""SELECT s.id, s.name, s.start_date, s.end_date, t.name as team_name,
                   COUNT(tk.id) as ticket_count,
                   COALESCE(SUM(tk.story_points), 0) as total_points,
                   COALESCE(SUM(tk.story_points) FILTER (
                       WHERE tk.status IN ('done', 'resolved', 'closed_not_resolved')
                   ), 0) as completed_points
            FROM sprints s
            JOIN teams t ON t.id = s.team_id
            LEFT JOIN tickets tk ON tk.sprint_id = s.id
            WHERE {where}
            GROUP BY s.id, s.name, s.start_date, s.end_date, t.name
            ORDER BY s.end_date DESC NULLS LAST
            LIMIT 20""",
        params,
    )
    return jsonify(rows)


# ============================================================
# Sprint Backlog — tickets available to add
# ============================================================

@sprints_bp.route("/<int:sprint_id>/backlog", methods=["GET"])
@login_required
def sprint_backlog(sprint_id: int):
    """List tickets available to add to this sprint (not in any sprint, not completed)."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id, team_id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    conditions = [
        "t.sprint_id IS NULL",
        "t.tenant_id = %s",
        "t.ticket_type IN ('task', 'bug', 'feature')",
        "t.status NOT IN ('done', 'resolved', 'closed_not_resolved', 'cancelled')",
    ]
    params: list = [tenant_id]

    team_only = request.args.get("team_only")
    if team_only == "true":
        conditions.append("(t.team_id = %s OR t.team_id IS NULL)")
        params.append(sprint["team_id"])

    search = request.args.get("search", "").strip()
    if search:
        conditions.append("t.subject ILIKE %s")
        params.append(f"%{search}%")

    where = " AND ".join(conditions)
    tickets = fetch_all(
        f"""SELECT t.id, t.ticket_number, t.subject, t.priority, t.ticket_type,
                   t.story_points, t.status, t.work_item_type_id,
                   u.name as assignee_name
            FROM tickets t
            LEFT JOIN users u ON u.id = t.assignee_id
            LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
            WHERE {where}
            ORDER BY
                CASE t.priority WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 WHEN 'p3' THEN 3 WHEN 'p4' THEN 4 END,
                t.created_at""",
        params,
    )
    return jsonify(tickets)


# ============================================================
# Sprint Items — batch add / remove tickets
# ============================================================

@sprints_bp.route("/<int:sprint_id>/items", methods=["POST"])
@login_required
@require_permission("sprints.manage")
def add_sprint_items(sprint_id: int):
    """Batch add tickets to a sprint."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    ticket_ids = data.get("ticket_ids", [])
    if not ticket_ids:
        return jsonify({"error": "ticket_ids is required"}), 400

    count = execute(
        "UPDATE tickets SET sprint_id = %s WHERE id = ANY(%s) AND tenant_id = %s",
        [sprint_id, ticket_ids, tenant_id],
    )
    return jsonify({"ok": True, "count": count})


@sprints_bp.route("/<int:sprint_id>/items/<int:ticket_id>", methods=["DELETE"])
@login_required
@require_permission("sprints.manage")
def remove_sprint_item(sprint_id: int, ticket_id: int):
    """Remove a ticket from a sprint."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    execute(
        "UPDATE tickets SET sprint_id = NULL WHERE id = %s AND sprint_id = %s",
        [ticket_id, sprint_id],
    )
    return jsonify({"ok": True})


# ============================================================
# Sprint Timeline — completed tickets
# ============================================================

@sprints_bp.route("/<int:sprint_id>/timeline", methods=["GET"])
@login_required
def sprint_timeline(sprint_id: int):
    """Return completed tickets for a sprint, ordered by completion time."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    tickets = fetch_all(
        """SELECT t.id, t.ticket_number, t.subject, t.story_points,
                  t.completed_at, t.ticket_type,
                  u.name as completed_by_name,
                  wit.name as work_item_type_name,
                  wit.icon as work_item_type_icon,
                  wit.color as work_item_type_color
           FROM tickets t
           LEFT JOIN users u ON u.id = t.completed_by
           LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
           WHERE t.sprint_id = %s AND t.completed_at IS NOT NULL
           ORDER BY t.completed_at DESC""",
        [sprint_id],
    )
    return jsonify(tickets)


# ============================================================
# Velocity Averages — team and per-person
# ============================================================

@sprints_bp.route("/velocity/averages", methods=["GET"])
@login_required
def velocity_averages():
    """Return team average and per-person average velocity across completed sprints."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return jsonify({"team_avg": 0, "sprint_count": 0, "person_averages": []})

    team_id = request.args.get("team_id")

    conditions = ["s.tenant_id = %s", "s.status = 'completed'"]
    params: list = [tenant_id]
    if team_id:
        conditions.append("s.team_id = %s")
        params.append(int(team_id))

    where = " AND ".join(conditions)

    # Team average: total completed points / number of completed sprints
    team_row = fetch_one(
        f"""SELECT COUNT(DISTINCT s.id) as sprint_count,
                   COALESCE(SUM(tk.story_points) FILTER (
                       WHERE tk.status IN ('done', 'resolved', 'closed_not_resolved')
                   ), 0) as total_completed
            FROM sprints s
            LEFT JOIN tickets tk ON tk.sprint_id = s.id
            WHERE {where}""",
        params,
    )

    sprint_count = team_row["sprint_count"] if team_row else 0
    total_completed = team_row["total_completed"] if team_row else 0
    team_avg = round(total_completed / sprint_count, 2) if sprint_count > 0 else 0

    # Per-person averages
    person_rows = fetch_all(
        f"""SELECT tk.assignee_id as user_id, u.name,
                   COUNT(DISTINCT s.id) as sprint_count,
                   COALESCE(SUM(tk.story_points), 0) as total_points,
                   ROUND(COALESCE(SUM(tk.story_points), 0)::numeric /
                         NULLIF(COUNT(DISTINCT s.id), 0), 2) as avg_points
            FROM tickets tk
            JOIN sprints s ON s.id = tk.sprint_id
            JOIN users u ON u.id = tk.assignee_id
            WHERE {where}
              AND tk.status IN ('done', 'resolved', 'closed_not_resolved')
              AND tk.assignee_id IS NOT NULL
            GROUP BY tk.assignee_id, u.name
            ORDER BY avg_points DESC""",
        params,
    )

    return jsonify({
        "team_avg": team_avg,
        "sprint_count": sprint_count,
        "person_averages": person_rows,
    })


# ============================================================
# Sprint Reorder — drag-drop ranking within sprint
# ============================================================

@sprints_bp.route("/<int:sprint_id>/reorder", methods=["PUT"])
@login_required
@require_permission("sprints.manage")
def reorder_sprint_items(sprint_id: int):
    """Set sort_order for tickets in a sprint based on position in array."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    ticket_ids = data.get("ticket_ids", [])
    for i, tid in enumerate(ticket_ids):
        execute(
            "UPDATE tickets SET sort_order = %s WHERE id = %s AND sprint_id = %s",
            [i, tid, sprint_id],
        )
    return jsonify({"ok": True})


# ============================================================
# Sprint Capacity — committed vs capacity
# ============================================================

@sprints_bp.route("/<int:sprint_id>/capacity", methods=["GET"])
@login_required
def sprint_capacity(sprint_id: int):
    """Return capacity planning data for a sprint."""
    tenant_id = _get_tenant_id()
    sprint = fetch_one(
        "SELECT id, capacity_points FROM sprints WHERE id = %s AND tenant_id = %s",
        [sprint_id, tenant_id],
    )
    if not sprint:
        return jsonify({"error": "Not found"}), 404

    # Committed points
    committed = fetch_one(
        "SELECT COALESCE(SUM(story_points), 0) as total FROM tickets WHERE sprint_id = %s",
        [sprint_id],
    )

    # Per-person breakdown
    person_rows = fetch_all(
        """SELECT t.assignee_id, u.name,
                  COALESCE(SUM(t.story_points), 0) as committed_points,
                  COUNT(*) as item_count
           FROM tickets t
           LEFT JOIN users u ON u.id = t.assignee_id
           WHERE t.sprint_id = %s
           GROUP BY t.assignee_id, u.name
           ORDER BY committed_points DESC""",
        [sprint_id],
    )

    capacity = sprint["capacity_points"] or 0
    committed_pts = committed["total"] if committed else 0

    return jsonify({
        "capacity_points": capacity,
        "committed_points": committed_pts,
        "remaining": capacity - committed_pts if capacity else None,
        "over_capacity": committed_pts > capacity if capacity else False,
        "person_breakdown": person_rows,
    })
