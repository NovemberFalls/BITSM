"""Pipeline queue monitoring API — stats, executions, failures, retry/cancel."""

import logging
from flask import Blueprint, jsonify, request
from routes.auth import login_required, require_role
from models.db import fetch_all, fetch_one, execute

logger = logging.getLogger(__name__)
queue_bp = Blueprint("queue", __name__)


@queue_bp.route("/stats", methods=["GET"])
@login_required
@require_role("super_admin")
def queue_stats():
    """Real-time queue statistics."""
    stats = fetch_one("""
        SELECT
            (SELECT count(*) FROM pipeline_queue WHERE status = 'pending') as queue_depth,
            (SELECT count(*) FROM pipeline_queue WHERE status = 'running') as running,
            (SELECT count(*) FROM pipeline_queue WHERE status = 'running' AND uses_llm = true) as running_llm,
            (SELECT count(*) FROM pipeline_queue WHERE status = 'failed') as failed_total,
            (SELECT count(*) FROM pipeline_execution_log
             WHERE status = 'success' AND created_at > now() - interval '1 hour') as completed_last_hour,
            (SELECT count(*) FROM pipeline_execution_log
             WHERE status = 'error' AND created_at > now() - interval '1 hour') as failed_last_hour,
            (SELECT coalesce(round(avg(duration_ms)), 0) FROM pipeline_execution_log
             WHERE created_at > now() - interval '1 hour') as avg_duration_ms,
            (SELECT extract(epoch from (now() - min(created_at)))::int
             FROM pipeline_queue WHERE status = 'pending') as oldest_pending_age_seconds
    """)
    return jsonify(stats or {})


@queue_bp.route("/active", methods=["GET"])
@login_required
@require_role("super_admin")
def queue_active():
    """Currently running tasks."""
    tasks = fetch_all("""
        SELECT pq.*, t.ticket_number
        FROM pipeline_queue pq
        LEFT JOIN tickets t ON t.id = pq.ticket_id
        WHERE pq.status = 'running'
        ORDER BY pq.started_at ASC
    """)
    return jsonify({"tasks": tasks})


@queue_bp.route("/recent", methods=["GET"])
@login_required
@require_role("super_admin")
def queue_recent():
    """Recent execution history (paginated, filterable)."""
    status_filter = request.args.get("status")
    step_filter = request.args.get("step")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    conditions = []
    params = []

    if status_filter:
        conditions.append("pel.status = %s")
        params.append(status_filter)

    if step_filter:
        conditions.append("pel.step_name = %s")
        params.append(step_filter)

    tenant_filter = request.args.get("tenant_id")
    if tenant_filter:
        conditions.append("pel.tenant_id = %s")
        params.append(int(tenant_filter))

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    count_params = list(params)
    params.extend([limit, offset])

    executions = fetch_all(f"""
        SELECT pel.*, t.ticket_number
        FROM pipeline_execution_log pel
        LEFT JOIN tickets t ON t.id = pel.ticket_id
        {where}
        ORDER BY pel.created_at DESC
        LIMIT %s OFFSET %s
    """, params)

    total = fetch_one(f"SELECT count(*) as cnt FROM pipeline_execution_log pel {where}", count_params)

    return jsonify({"executions": executions, "total": (total or {}).get("cnt", 0)})


@queue_bp.route("/failures", methods=["GET"])
@login_required
@require_role("super_admin")
def queue_failures():
    """Failed tasks with error details."""
    tasks = fetch_all("""
        SELECT pq.*, t.ticket_number
        FROM pipeline_queue pq
        LEFT JOIN tickets t ON t.id = pq.ticket_id
        WHERE pq.status = 'failed'
        ORDER BY pq.completed_at DESC
        LIMIT 100
    """)
    return jsonify({"tasks": tasks})


@queue_bp.route("/<int:task_id>/retry", methods=["POST"])
@login_required
@require_role("super_admin")
def queue_retry(task_id):
    """Manually retry a failed task."""
    task = fetch_one("SELECT * FROM pipeline_queue WHERE id = %s", [task_id])
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task["status"] not in ("failed", "cancelled"):
        return jsonify({"error": f"Cannot retry task with status '{task['status']}'"}), 400

    execute(
        """UPDATE pipeline_queue
           SET status = 'pending', attempts = 0, last_error = NULL,
               next_run_at = now(), locked_by = NULL, locked_at = NULL,
               started_at = NULL, completed_at = NULL, duration_ms = NULL
           WHERE id = %s""",
        [task_id],
    )
    logger.info("Manual retry: task %d (%s)", task_id, task["step_name"])
    return jsonify({"ok": True, "task_id": task_id})


@queue_bp.route("/<int:task_id>/cancel", methods=["POST"])
@login_required
@require_role("super_admin")
def queue_cancel(task_id):
    """Cancel a pending task."""
    task = fetch_one("SELECT * FROM pipeline_queue WHERE id = %s", [task_id])
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task["status"] != "pending":
        return jsonify({"error": f"Cannot cancel task with status '{task['status']}'"}), 400

    execute(
        "UPDATE pipeline_queue SET status = 'cancelled', completed_at = now() WHERE id = %s",
        [task_id],
    )
    logger.info("Cancelled: task %d (%s)", task_id, task["step_name"])
    return jsonify({"ok": True, "task_id": task_id})


@queue_bp.route("/schedules", methods=["GET"])
@login_required
@require_role("super_admin")
def queue_schedules():
    """List cron schedules."""
    schedules = fetch_all("SELECT * FROM pipeline_schedules ORDER BY step_name")
    return jsonify({"schedules": schedules})


@queue_bp.route("/schedules/<int:schedule_id>/toggle", methods=["POST"])
@login_required
@require_role("super_admin")
def toggle_schedule(schedule_id):
    """Enable/disable a cron schedule."""
    data = request.json or {}
    enabled = data.get("enabled", True)
    execute(
        "UPDATE pipeline_schedules SET enabled = %s WHERE id = %s",
        [enabled, schedule_id],
    )
    return jsonify({"ok": True, "schedule_id": schedule_id, "enabled": enabled})
