"""Pipeline Queue — PostgreSQL-backed job queue with retry, priority, and cron scheduling.

Handles ticket lifecycle steps, notifications, and cron jobs.
Uses FOR UPDATE SKIP LOCKED for multi-worker-safe dispatch.
"""

import json
import logging
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from models.db import fetch_all, fetch_one, execute, insert_returning

logger = logging.getLogger(__name__)

# ── Priority mapping ──────────────────────────────────────────
PRIORITY_MAP = {"p1": 1, "p2": 2, "p3": 3, "p4": 4}
CRON_PRIORITY = 10  # Lower priority than any ticket


def is_queue_alive() -> bool:
    """Return True if the queue poll loop thread is running."""
    return any(
        t.name == "pq-poll" and t.is_alive()
        for t in threading.enumerate()
    )


# ============================================================
# Step Registry — maps step names to (function, uses_llm)
# ============================================================
# Lazy imports to avoid circular dependency at module load time.

def _run_automation_step(ticket_id, tenant_id, payload=None):
    """Queue worker for automation execution."""
    from services.automation_engine import run_automation_worker
    return run_automation_worker(ticket_id, tenant_id, payload)


def _cron_kb_pipeline(ticket_id, tenant_id, payload):
    """KB scrape + pipeline cron job.

    For each active module with a registered scraper: scrape → ingest → tag → embed.
    Modules without scrapers just run the ingest/tag/embed pipeline.
    """
    from services.scrapers import available, run_scraper
    from services.pipeline_service import run_full_pipeline

    slugs = available()
    logger.info("KB pipeline cron: processing %d modules with scrapers: %s", len(slugs), slugs)

    for slug in slugs:
        try:
            logger.info("KB pipeline [%s]: running scraper...", slug)
            scrape_stats = run_scraper(slug)
            logger.info("KB pipeline [%s]: scraper done — %s", slug, scrape_stats)
        except Exception as e:
            logger.warning("KB pipeline [%s]: scraper failed — %s (continuing to pipeline)", slug, e)

        try:
            logger.info("KB pipeline [%s]: running pipeline...", slug)
            pipe_stats = run_full_pipeline(slug)
            logger.info("KB pipeline [%s]: pipeline done — status=%s", slug, pipe_stats.get("status"))
        except Exception as e:
            logger.warning("KB pipeline [%s]: pipeline failed — %s", slug, e)

    # Also run pipeline for modules WITHOUT scrapers (e.g., toast — files added manually)
    from models.db import fetch_all
    all_modules = fetch_all(
        "SELECT slug FROM knowledge_modules WHERE is_active = true"
    )
    for row in all_modules:
        if row["slug"] not in slugs:
            try:
                logger.info("KB pipeline [%s]: running pipeline (no scraper)...", row["slug"])
                run_full_pipeline(row["slug"])
            except Exception as e:
                logger.warning("KB pipeline [%s]: pipeline failed — %s", row["slug"], e)

    logger.info("KB pipeline cron complete")


def _get_step_registry():
    """Build the step registry with lazy imports."""
    from services.tagging_service import _tag_worker
    from services.enrichment_service import _enrichment_worker
    from services.atlas_service import (
        _engage_worker, _routing_worker,
        _audit_close_worker, _effort_worker,
        detect_knowledge_gaps,
    )
    from services.notification_service import notify_ticket_event

    return {
        # ── Ticket create (sequential lane) ───────────────
        "auto_tag": (_tag_worker, True),
        "enrich": (_enrichment_worker, True),
        "engage": (_engage_worker, True),
        "route": (_routing_worker, False),
        # ── Ticket close (parallel) ───────────────────────
        "audit": (_audit_close_worker, True),
        "effort": (_effort_worker, False),
        # ── Notification ──────────────────────────────────
        "notify": (notify_ticket_event, False),
        # ── Cron jobs ─────────────────────────────────────
        "sla_breach_check": (_cron_sla_breach, False),
        "sla_risk_check": (_cron_sla_risk, False),
        "escalation_check": (_cron_escalation, False),
        "audit_auto_close": (_cron_audit_auto_close, False),
        "tenant_health": (_cron_tenant_health, False),
        "knowledge_gaps": (
            lambda ticket_id, tenant_id, payload: detect_knowledge_gaps(
                payload.get("tenant_id") or tenant_id or 1
            ),
            True,
        ),
        "kb_freshness": (_cron_kb_freshness, False),
        "kb_pipeline": (_cron_kb_pipeline, False),
        "trial_expiry": (_cron_trial_expiry, False),
        "demo_purge": (_cron_demo_purge, False),
        # ── Automations ─────────────────────────────────
        "run_automation": (_run_automation_step, False),
    }


_registry = None
_registry_lock = threading.Lock()


def _registry_get():
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _get_step_registry()
    return _registry


# ============================================================
# Enqueue Helpers
# ============================================================

def enqueue_ticket_create(ticket_id: int, tenant_id: int, priority: str = "p3"):
    """Enqueue lane-based create-pipeline.  Each step chains the next on completion.

    Lane (sequential):
      Phase 0: auto_tag   (LLM — tag suggestions)
      Phase 1: enrich     (LLM — KB search + analysis)   ← chained by auto_tag
      Phase 2: engage     (LLM — triage + L1 RAG)        ← chained by enrich
      Phase 3: route      (no LLM — suggest assignee)    ← chained by engage
    """
    pval = PRIORITY_MAP.get(priority, 3)
    # Only enqueue first step; the rest are chained on completion
    payload = json.dumps({"created_at": datetime.now(timezone.utc).isoformat()})
    insert_returning(
        """INSERT INTO pipeline_queue
           (tenant_id, ticket_id, step_name, priority, uses_llm, phase, payload)
           VALUES (%s, %s, 'auto_tag', %s, true, 0, %s::jsonb) RETURNING id""",
        [tenant_id, ticket_id, pval, payload],
    )
    logger.info("Queued create lane for ticket %s (priority %s) — Phase 0: auto_tag", ticket_id, priority)


def enqueue_ticket_close(ticket_id: int, tenant_id: int, priority: str = "p3"):
    """Enqueue 2 parallel close-pipeline steps."""
    pval = PRIORITY_MAP.get(priority, 3)
    for step_name, uses_llm in [("audit", True), ("effort", False)]:
        insert_returning(
            """INSERT INTO pipeline_queue
               (tenant_id, ticket_id, step_name, priority, uses_llm)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            [tenant_id, ticket_id, step_name, pval, uses_llm],
        )
    logger.info("Queued close pipeline for ticket %s", ticket_id)


def enqueue_notify(tenant_id: int, ticket_id: int, event: str, comment: dict | None = None):
    """Enqueue a notification step."""
    insert_returning(
        """INSERT INTO pipeline_queue
           (tenant_id, ticket_id, step_name, priority, uses_llm, payload)
           VALUES (%s, %s, 'notify', %s, false, %s::jsonb) RETURNING id""",
        [tenant_id, ticket_id, PRIORITY_MAP.get("p2", 2),
         json.dumps({"event": event, "comment": comment})],
    )


# ============================================================
# Step output formatting — human-readable summaries
# ============================================================

def _format_step_output(step_name: str, result) -> str | None:
    """Produce a short human-readable summary of a step's output for the pipeline log."""
    try:
        if step_name == "auto_tag" and result:
            if isinstance(result, list):
                return f"Tags suggested: {', '.join(str(t) for t in result[:10])}"
            return str(result)[:200]
        if step_name == "enrich" and result:
            if isinstance(result, list) and result:
                titles = [r.get("title", "?") for r in result[:3]]
                sims = [f"{r.get('similarity', 0):.2f}" for r in result[:3]]
                parts = [f"{t} ({s})" for t, s in zip(titles, sims)]
                return f"KB match ({len(result)} articles): {'; '.join(parts)}"
        return None
    except Exception:
        return None


# ============================================================
# Queue Processor — background worker
# ============================================================

class QueueProcessor:
    """Polls pipeline_queue and executes tasks in a ThreadPoolExecutor.

    Multi-worker safe via FOR UPDATE SKIP LOCKED.
    """

    def __init__(
        self,
        max_llm_concurrency: int = 5,
        poll_interval: float = 2.0,
    ):
        self._max_llm = max_llm_concurrency
        self._poll_interval = poll_interval
        self._worker_id = f"w-{os.getpid()}"
        self._shutdown = False
        self._executor = ThreadPoolExecutor(
            max_workers=max_llm_concurrency + 5,
            thread_name_prefix="pq",
        )
        logger.info(
            "QueueProcessor init: worker=%s, llm_cap=%d, poll=%.1fs",
            self._worker_id, max_llm_concurrency, poll_interval,
        )

    def start(self):
        """Start the poll loop and cron scheduler as daemon threads."""
        self._recover_stale_tasks()
        threading.Thread(target=self._poll_loop, daemon=True, name="pq-poll").start()
        threading.Thread(target=self._cron_loop, daemon=True, name="pq-cron").start()
        logger.info("QueueProcessor started: %s", self._worker_id)

    def shutdown(self):
        self._shutdown = True
        self._executor.shutdown(wait=False)

    # ── Poll loop ─────────────────────────────────────────

    def _poll_loop(self):
        while not self._shutdown:
            try:
                task = self._claim_next()
                if task:
                    self._executor.submit(self._execute_task, task)
                else:
                    time.sleep(self._poll_interval)
            except Exception:
                logger.exception("Queue poll error")
                time.sleep(5)

    def _claim_next(self):
        """Claim the next runnable task using FOR UPDATE SKIP LOCKED."""
        # Check LLM concurrency
        llm_running = fetch_one(
            "SELECT count(*) as cnt FROM pipeline_queue WHERE status = 'running' AND uses_llm = true"
        )
        llm_at_cap = (llm_running or {}).get("cnt", 0) >= self._max_llm

        # Build query — skip LLM tasks if at cap
        extra = " AND uses_llm = false" if llm_at_cap else ""

        row = fetch_one(f"""
            UPDATE pipeline_queue
            SET status = 'running',
                locked_by = %s,
                locked_at = now(),
                started_at = now(),
                attempts = attempts + 1
            WHERE id = (
                SELECT id FROM pipeline_queue
                WHERE status = 'pending'
                  AND next_run_at <= now()
                  {extra}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
        """, [self._worker_id])

        return row

    # ── Task execution ────────────────────────────────────

    def _execute_task(self, task):
        """Execute a single queue task with error handling and retry."""
        step_name = task["step_name"]
        ticket_id = task.get("ticket_id")
        tenant_id = task.get("tenant_id")
        payload = task.get("payload") or {}
        task_id = task["id"]

        t0 = time.time()
        try:
            registry = _registry_get()
            entry = registry.get(step_name)
            if not entry:
                raise ValueError(f"Unknown step: {step_name}")

            fn, _ = entry

            # Call the worker function with appropriate args
            result = None
            if step_name == "notify":
                fn(tenant_id, ticket_id, payload.get("event", ""), comment=payload.get("comment"))
            elif step_name == "engage":
                fn(ticket_id, tenant_id, payload=payload)
            elif step_name in ("route", "audit", "effort"):
                fn(ticket_id, tenant_id)
            elif step_name in ("auto_tag", "enrich"):
                result = fn(ticket_id)
            else:
                # Cron jobs — pass all args
                fn(ticket_id, tenant_id, payload)

            duration = int((time.time() - t0) * 1000)
            execute(
                """UPDATE pipeline_queue
                   SET status = 'completed', completed_at = now(), duration_ms = %s
                   WHERE id = %s""",
                [duration, task_id],
            )
            output_summary = _format_step_output(step_name, result)
            self._log_execution(task, "success", duration, output_summary=output_summary)

            # ── Phase chaining: enqueue next phase on completion ──
            self._chain_next(task, result)

        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            error_msg = f"{type(e).__name__}: {e}"[:500]
            attempts = task["attempts"]
            max_attempts = task["max_attempts"]

            if attempts >= max_attempts:
                # Final failure
                execute(
                    """UPDATE pipeline_queue
                       SET status = 'failed', last_error = %s, duration_ms = %s,
                           completed_at = now()
                       WHERE id = %s""",
                    [error_msg, duration, task_id],
                )
                logger.error(
                    "Pipeline step %s FAILED (attempt %d/%d) for ticket %s: %s",
                    step_name, attempts, max_attempts, ticket_id, error_msg,
                )
            else:
                # Retry with exponential backoff: 30s, 120s, 480s
                backoff_seconds = 30 * (4 ** (attempts - 1))
                execute(
                    """UPDATE pipeline_queue
                       SET status = 'pending', last_error = %s, duration_ms = %s,
                           next_run_at = now() + interval '%s seconds',
                           locked_by = NULL, locked_at = NULL, started_at = NULL
                       WHERE id = %s""",
                    [error_msg, duration, backoff_seconds, task_id],
                )
                logger.warning(
                    "Pipeline step %s failed (attempt %d/%d) for ticket %s, retry in %ds: %s",
                    step_name, attempts, max_attempts, ticket_id, backoff_seconds, error_msg,
                )

            self._log_execution(task, "error", duration, error_msg)

    # ── Phase chaining ──────────────────────────────────────

    def _chain_next(self, task, result):
        """After a step completes, enqueue the next step in the lane.

        Create lane (sequential):
          Phase 0: auto_tag  → chains enrich
          Phase 1: enrich    → chains engage (with KB results)
          Phase 2: engage    → chains route
          Phase 3: route     → done
        """
        step_name = task["step_name"]
        ticket_id = task.get("ticket_id")
        tenant_id = task.get("tenant_id")
        if not ticket_id or not tenant_id:
            return

        # Propagate created_at timestamp through the entire lane
        prev_payload = task.get("payload") or {}
        created_at = prev_payload.get("created_at")

        try:
            if step_name == "auto_tag":
                # Dev items stop after tagging — no triage/engage/route
                if prev_payload.get("dev_item"):
                    logger.info("auto_tag done for dev item ticket %s — no further chaining", ticket_id)
                    return
                # auto_tag done → enqueue enrich (Phase 1)
                chain_payload = {"created_at": created_at} if created_at else {}
                insert_returning(
                    """INSERT INTO pipeline_queue
                       (tenant_id, ticket_id, step_name, priority, uses_llm, phase, payload)
                       VALUES (%s, %s, 'enrich', %s, true, 1, %s::jsonb) RETURNING id""",
                    [tenant_id, ticket_id, task["priority"],
                     json.dumps(chain_payload)],
                )
                logger.info("Chained: auto_tag → enrich (Phase 1) for ticket %s", ticket_id)

            elif step_name == "enrich":
                # Enrich done → enqueue engage (Phase 2) with KB results
                kb_payload = {
                    "kb_results": result or [],
                    "created_at": created_at,
                }
                insert_returning(
                    """INSERT INTO pipeline_queue
                       (tenant_id, ticket_id, step_name, priority, uses_llm, phase, payload)
                       VALUES (%s, %s, 'engage', %s, true, 2, %s::jsonb) RETURNING id""",
                    [tenant_id, ticket_id, task["priority"],
                     json.dumps(kb_payload)],
                )
                logger.info("Chained: enrich → engage (Phase 2) for ticket %s", ticket_id)

            elif step_name == "engage":
                # Engage done → enqueue route (Phase 3)
                insert_returning(
                    """INSERT INTO pipeline_queue
                       (tenant_id, ticket_id, step_name, priority, uses_llm, phase)
                       VALUES (%s, %s, 'route', %s, false, 3) RETURNING id""",
                    [tenant_id, ticket_id, task["priority"]],
                )
                logger.info("Chained: engage → route (Phase 3) for ticket %s", ticket_id)

        except Exception as e:
            logger.error("Lane chaining failed after %s for ticket %s: %s", step_name, ticket_id, e)

    # ── Execution logging ─────────────────────────────────

    def _log_execution(self, task, status, duration_ms, error_message=None, output_summary=None):
        """Log every execution to pipeline_execution_log."""
        try:
            insert_returning(
                """INSERT INTO pipeline_execution_log
                   (queue_id, tenant_id, ticket_id, step_name, status,
                    error_message, output_summary, duration_ms, attempts)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                [
                    task["id"], task.get("tenant_id"), task.get("ticket_id"),
                    task["step_name"], status, error_message, output_summary,
                    duration_ms, task["attempts"],
                ],
            )
        except Exception:
            logger.exception("Failed to log pipeline execution")

    # ── Stale task recovery ───────────────────────────────

    def _recover_stale_tasks(self):
        """On startup, reset any 'running' tasks older than 5 minutes back to pending."""
        try:
            result = execute(
                """UPDATE pipeline_queue
                   SET status = 'pending', locked_by = NULL, locked_at = NULL,
                       started_at = NULL, last_error = 'Recovered: stale running task'
                   WHERE status = 'running'
                     AND locked_at < now() - interval '5 minutes'"""
            )
            if result:
                logger.info("Recovered %d stale running tasks", result)
        except Exception:
            logger.exception("Stale task recovery failed")

    # ── Cron scheduler ────────────────────────────────────

    def _cron_loop(self):
        """Check pipeline_schedules every 30s and enqueue due tasks."""
        while not self._shutdown:
            try:
                self._check_schedules()
            except Exception:
                logger.exception("Cron scheduler error")
            time.sleep(30)

    def _check_schedules(self):
        """Evaluate cron expressions and enqueue tasks that are due."""
        # Only let one worker run cron to avoid races
        got_lock = fetch_one("SELECT pg_try_advisory_lock(73001) AS locked")
        if not got_lock or not got_lock["locked"]:
            return
        try:
            self._check_schedules_inner()
        finally:
            execute("SELECT pg_advisory_unlock(73001)")

    def _check_schedules_inner(self):
        """Actual cron evaluation (called under advisory lock)."""
        schedules = fetch_all(
            "SELECT * FROM pipeline_schedules WHERE enabled = true"
        )
        now = datetime.now(timezone.utc)

        for sched in schedules:
            if self._is_cron_due(sched["cron_expression"], now, sched.get("last_enqueued_at")):
                # Don't double-enqueue — check if one is already pending/running
                existing = fetch_one(
                    """SELECT id FROM pipeline_queue
                       WHERE step_name = %s AND status IN ('pending', 'running')""",
                    [sched["step_name"]],
                )
                if existing:
                    continue

                payload = sched.get("payload") or {}
                insert_returning(
                    """INSERT INTO pipeline_queue
                       (tenant_id, ticket_id, step_name, priority, uses_llm, payload, max_attempts)
                       VALUES (%s, NULL, %s, %s, %s, %s::jsonb, 1) RETURNING id""",
                    [
                        payload.get("tenant_id"),
                        sched["step_name"],
                        CRON_PRIORITY,
                        sched["step_name"] == "knowledge_gaps",  # only this one uses LLM
                        json.dumps(payload),
                    ],
                )
                execute(
                    "UPDATE pipeline_schedules SET last_enqueued_at = now() WHERE id = %s",
                    [sched["id"]],
                )
                logger.info("Cron enqueued: %s", sched["step_name"])

    def _is_cron_due(self, cron_expr: str, now: datetime, last_run: datetime | None) -> bool:
        """Cron evaluation with catch-up: fires if due now OR if overdue since last run."""
        try:
            # Normalize both to UTC-aware for safe comparison
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            if last_run is not None and last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)

            # Check current minute match
            if self._matches_cron(cron_expr, now):
                if not last_run or last_run.replace(second=0, microsecond=0) < now.replace(second=0, microsecond=0):
                    return True

            # Catch-up: scan back through missed windows (up to 25h) in 1-min steps
            if last_run:
                check = last_run.replace(second=0, microsecond=0) + timedelta(minutes=1)
                limit = now.replace(second=0, microsecond=0)
                steps = 0
                while check <= limit and steps < 1500:  # max 25h look-back
                    if self._matches_cron(cron_expr, check):
                        return True
                    check += timedelta(minutes=1)
                    steps += 1

            return False
        except Exception:
            logger.exception("_is_cron_due failed for expr=%s", cron_expr)
            return False

    def _matches_cron(self, cron_expr: str, dt: datetime) -> bool:
        """Check if a datetime matches a cron expression."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        minute, hour, dom, month, dow = parts
        # cron dow: 0=Sunday, 1=Monday ... 6=Saturday
        # Python weekday(): 0=Monday ... 6=Sunday → convert
        cron_dow = (dt.weekday() + 1) % 7
        return (
            self._cron_field_matches(minute, dt.minute) and
            self._cron_field_matches(hour, dt.hour) and
            self._cron_field_matches(dom, dt.day) and
            self._cron_field_matches(month, dt.month) and
            self._cron_field_matches(dow, cron_dow)
        )

    @staticmethod
    def _cron_field_matches(field: str, value: int) -> bool:
        """Check if a cron field matches a value. Supports: *, */N, N, N-M, N,M."""
        if field == "*":
            return True
        if field.startswith("*/"):
            divisor = int(field[2:])
            return value % divisor == 0
        for part in field.split(","):
            if "-" in part:
                lo, hi = part.split("-", 1)
                if int(lo) <= value <= int(hi):
                    return True
            elif int(part) == value:
                return True
        return False


# ============================================================
# Cron job functions (extracted from webhooks.py)
# ============================================================

def _cron_sla_breach(ticket_id, tenant_id, payload):
    """SLA breach detection across all active tickets."""
    from services.sla_service import check_sla_breaches
    active = fetch_all(
        """SELECT id FROM tickets
           WHERE sla_due_at IS NOT NULL
             AND sla_breached = false
             AND status NOT IN ('resolved', 'closed_not_resolved')"""
    )
    ids = [r["id"] for r in active]
    if ids:
        check_sla_breaches(ids)
    logger.info("SLA breach check: %d tickets checked", len(ids))


def _cron_sla_risk(ticket_id, tenant_id, payload):
    """Calculate SLA risk levels for open tickets approaching deadlines."""
    execute(
        """UPDATE tickets SET sla_risk = 'normal'
           WHERE sla_risk != 'normal'
             AND status NOT IN ('resolved', 'closed_not_resolved')"""
    )
    execute(
        """UPDATE tickets SET sla_risk = 'critical'
           WHERE sla_due_at IS NOT NULL
             AND sla_breached = false
             AND status NOT IN ('resolved', 'closed_not_resolved')
             AND sla_due_at < now() + interval '30 minutes'
             AND sla_due_at > now()"""
    )
    execute(
        """UPDATE tickets SET sla_risk = 'at_risk'
           WHERE sla_due_at IS NOT NULL
             AND sla_breached = false
             AND sla_risk = 'normal'
             AND status NOT IN ('resolved', 'closed_not_resolved')
             AND sla_due_at < now() + interval '2 hours'
             AND sla_due_at > now()"""
    )
    logger.info("SLA risk scan complete")


def _cron_escalation(ticket_id, tenant_id, payload):
    """Auto-escalate stale tickets with no agent response in 4+ hours."""
    stale = fetch_all(
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
    priority_map = {"p4": "p3", "p3": "p2"}
    escalated = 0
    for ticket in stale:
        new_pri = priority_map.get(ticket["priority"])
        if not new_pri:
            continue
        execute(
            "UPDATE tickets SET priority = %s, updated_at = now() WHERE id = %s",
            [new_pri, ticket["id"]],
        )
        insert_returning(
            """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal)
               VALUES (%s, NULL, %s, true) RETURNING id""",
            [ticket["id"],
             f"[Atlas] Auto-escalated from {ticket['priority'].upper()} to {new_pri.upper()}: "
             f"no agent response in 4+ hours"],
        )
        escalated += 1
    logger.info("Escalation check: %d/%d escalated", escalated, len(stale))


def _cron_audit_auto_close(ticket_id, tenant_id, payload):
    """Auto-close stale audit queue items past their auto_close_at deadline."""
    closed = fetch_all(
        """UPDATE ticket_audit_queue
           SET status = 'auto_closed'
           WHERE status = 'pending' AND auto_close_at < now()
           RETURNING id"""
    )
    logger.info("Audit auto-close: %d items closed", len(closed))


def _cron_tenant_health(ticket_id, tenant_id, payload):
    """Check tenant health: inactive paid, expiring plans, high breach rates."""
    inactive = fetch_all(
        """SELECT t.id, t.name FROM tenants t
           LEFT JOIN tickets tk ON tk.tenant_id = t.id
           WHERE t.plan_tier NOT IN ('free') AND t.is_active = true
           GROUP BY t.id
           HAVING max(tk.created_at) IS NULL
              OR max(tk.created_at) < now() - interval '7 days'"""
    )
    expiring = fetch_all(
        """SELECT id, name FROM tenants
           WHERE plan_expires_at IS NOT NULL
             AND plan_expires_at < now() + interval '14 days'
             AND plan_expires_at > now()
             AND is_active = true"""
    )
    logger.info("Tenant health: %d inactive, %d expiring", len(inactive), len(expiring))


def _cron_kb_freshness(ticket_id, tenant_id, payload):
    """Flag KB documents not updated in 90+ days."""
    stale = fetch_all(
        """SELECT d.id, d.title, km.slug as module_slug
           FROM documents d
           JOIN knowledge_modules km ON km.id = d.module_id
           WHERE d.updated_at < now() - interval '90 days'
             AND km.is_active = true"""
    )
    if stale:
        modules = {}
        for doc in stale:
            modules.setdefault(doc["module_slug"], []).append(doc["title"])
        for mod_slug, titles in modules.items():
            topic = f"Stale KB content: {mod_slug} ({len(titles)} docs)"
            existing = fetch_one(
                "SELECT id FROM knowledge_gaps WHERE topic = %s AND status = 'detected'",
                [topic],
            )
            if not existing:
                insert_returning(
                    """INSERT INTO knowledge_gaps (tenant_id, topic, ticket_count, suggested_title, status)
                       VALUES (%s, %s, %s, %s, 'detected') RETURNING id""",
                    [tenant_id, topic, len(titles), f"Review stale {mod_slug} documentation"],
                )
    logger.info("KB freshness: %d stale documents", len(stale))


def _cron_trial_expiry(ticket_id, tenant_id, payload):
    """Expire trials: move tenants past plan_expires_at from trial → free."""
    from models.db import execute as db_execute
    expired = fetch_all(
        """SELECT id, name FROM tenants
           WHERE plan_tier = 'trial'
             AND plan_expires_at IS NOT NULL
             AND plan_expires_at < now()
             AND is_active = true"""
    )
    for t in expired:
        db_execute(
            "UPDATE tenants SET plan_tier = 'free', plan_expires_at = NULL WHERE id = %s",
            [t["id"]],
        )
        logger.info("Trial expired: tenant %s (%s) → free", t["id"], t["name"])
    if expired:
        logger.info("Trial expiry: %d tenants moved to free", len(expired))


def _cron_demo_purge(ticket_id, tenant_id, payload):
    """Hard-delete all data for expired demo tenants (privacy/cost purge).

    Finds demo tenants whose plan has expired and permanently removes all their
    data in FK-safe order.  One tenant failure does not block the others — each
    tenant's deletion is wrapped in its own try/except so a partial failure is
    logged and that tenant is skipped rather than leaving half-deleted rows.
    """
    from models.db import execute as db_execute, _get_conn, _put_conn

    expired = fetch_all(
        """SELECT id, name FROM tenants
           WHERE plan_tier = 'demo'
             AND plan_expires_at IS NOT NULL
             AND plan_expires_at < now()
             AND is_active = true"""
    )

    if not expired:
        logger.info("Demo purge: no expired demo tenants found")
        return

    logger.info("Demo purge: found %d expired demo tenant(s) to purge", len(expired))

    for t in expired:
        tid = t["id"]
        tenant_name = t["name"]
        conn = _get_conn()
        try:
            cur = conn.cursor()

            def _exec(sql, params=None):
                cur.execute(sql, params or [])

            # ── Phase 1: Ticket children ────────────────────────────────
            # ticket_activity has no ON DELETE CASCADE on ticket_id — must go first
            _exec("DELETE FROM ticket_activity WHERE tenant_id = %s", [tid])
            _exec(
                "DELETE FROM ticket_tasks WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM ticket_comments WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM ticket_attachments WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM tag_suggestions WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM ticket_audit_queue WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM ticket_metrics WHERE ticket_id IN "
                "(SELECT id FROM tickets WHERE tenant_id = %s)", [tid]
            )
            # csat_surveys has tenant_id FK (no CASCADE) and ticket_id FK (no CASCADE);
            # must be deleted before tickets to avoid FK violation (Finding 4)
            _exec("DELETE FROM csat_surveys WHERE tenant_id = %s", [tid])

            # ── Phase 2: Atlas / AI ─────────────────────────────────────
            # article_recommendations has no ON DELETE CASCADE on conversation_id — delete before ai_conversations
            _exec("DELETE FROM article_recommendations WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM atlas_engagements WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM ai_conversations WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM knowledge_gaps WHERE tenant_id = %s", [tid])

            # ── Phase 3: Phone / Messaging children ────────────────────
            # phone_transfer_attempts → phone_sessions (must go before sessions)
            _exec(
                "DELETE FROM phone_transfer_attempts WHERE session_id IN "
                "(SELECT id FROM phone_sessions WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM phone_sessions WHERE tenant_id = %s", [tid])
            # messages → messaging_conversations (must go before conversations)
            _exec(
                "DELETE FROM messages WHERE conversation_id IN "
                "(SELECT id FROM messaging_conversations WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM messaging_conversations WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM messaging_templates WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM phone_agents WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM phone_configs WHERE tenant_id = %s", [tid])

            # ── Phase 4: Documents / KB ─────────────────────────────────
            # document_chunks → documents (ON DELETE CASCADE, but explicit for clarity)
            _exec(
                "DELETE FROM document_chunks WHERE document_id IN "
                "(SELECT id FROM documents WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM documents WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM tenant_collections WHERE tenant_id = %s", [tid])

            # ── Phase 5: Automations ────────────────────────────────────
            # automation_runs, automation_nodes, automation_edges → automations (CASCADE, but explicit)
            _exec(
                "DELETE FROM automation_runs WHERE automation_id IN "
                "(SELECT id FROM automations WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM automation_nodes WHERE automation_id IN "
                "(SELECT id FROM automations WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM automation_edges WHERE automation_id IN "
                "(SELECT id FROM automations WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM automations WHERE tenant_id = %s", [tid])

            # ── Phase 6: Sprints ────────────────────────────────────────
            # tickets.sprint_id is ON DELETE SET NULL, so tickets can be deleted after sprints
            _exec("DELETE FROM sprints WHERE tenant_id = %s", [tid])

            # ── Phase 7: Custom fields / Forms ──────────────────────────
            _exec("DELETE FROM custom_field_definitions WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM form_templates WHERE tenant_id = %s", [tid])

            # ── Phase 8: Status incidents ────────────────────────────────
            # status_incident_updates → status_incidents (ON DELETE CASCADE, but explicit)
            _exec(
                "DELETE FROM status_incident_updates WHERE incident_id IN "
                "(SELECT id FROM status_incidents WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM status_incidents WHERE tenant_id = %s", [tid])

            # ── Phase 9: Locations / Contacts / Categories ──────────────
            # contact_location_history → contact_profiles (ON DELETE CASCADE, but explicit)
            _exec(
                "DELETE FROM contact_location_history WHERE contact_id IN "
                "(SELECT id FROM contact_profiles WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM contact_profiles WHERE tenant_id = %s", [tid])
            _exec(
                "DELETE FROM user_locations WHERE user_id IN "
                "(SELECT id FROM users WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM locations WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM problem_categories WHERE tenant_id = %s", [tid])

            # ── Phase 10: Notifications ──────────────────────────────────
            # notification_group_events → notification_groups (ON DELETE CASCADE, but explicit)
            _exec(
                "DELETE FROM notification_group_events WHERE group_id IN "
                "(SELECT id FROM notification_groups WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM notification_groups WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM notifications WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM notification_templates WHERE tenant_id = %s", [tid])

            # ── Phase 11: Users / Auth (RBAC children first) ─────────────
            _exec(
                "DELETE FROM user_permission_overrides WHERE user_id IN "
                "(SELECT id FROM users WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM user_group_memberships WHERE user_id IN "
                "(SELECT id FROM users WHERE tenant_id = %s)", [tid]
            )
            _exec(
                "DELETE FROM team_members WHERE user_id IN "
                "(SELECT id FROM users WHERE tenant_id = %s)", [tid]
            )

            # ── Phase 12: Tickets (all children cleared) ─────────────────
            _exec("DELETE FROM tickets WHERE tenant_id = %s", [tid])

            # ── Phase 13: Remaining tenant-level tables ──────────────────
            # system_errors has tenant_id ON DELETE SET NULL — rows with stack traces
            # and request paths persist as orphaned records without this delete (Finding 7)
            _exec("DELETE FROM system_errors WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM connectors WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM api_keys WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM sla_policies WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM teams WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM audit_events WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM pipeline_queue WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM pipeline_execution_log WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM tenant_token_usage WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM tenant_module_features WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM tenant_modules WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM api_usage_monthly WHERE tenant_id = %s", [tid])
            # work_item_types: only delete tenant-scoped rows (tenant_id = NULL are system defaults)
            _exec("DELETE FROM work_item_types WHERE tenant_id = %s", [tid])
            _exec("DELETE FROM ticket_status_workflows WHERE tenant_id = %s", [tid])
            # group_permissions → groups (must delete group_permissions before groups)
            _exec(
                "DELETE FROM group_permissions WHERE group_id IN "
                "(SELECT id FROM groups WHERE tenant_id = %s)", [tid]
            )
            _exec("DELETE FROM groups WHERE tenant_id = %s", [tid])

            # ── Phase 14: Users ──────────────────────────────────────────
            # password_reset_tokens and email_verification_tokens cascade from users
            _exec("DELETE FROM users WHERE tenant_id = %s", [tid])

            # ── Phase 15: Tenant record ──────────────────────────────────
            # Clear BYOK keys first (columns on the tenant row), then delete the row
            _exec(
                "UPDATE tenants SET byok_anthropic_key = NULL, byok_openai_key = NULL, "
                "byok_voyage_key = NULL WHERE id = %s",
                [tid],
            )
            _exec("DELETE FROM tenants WHERE id = %s", [tid])

            cur.close()
            conn.commit()
            logger.info(
                "Demo purge: tenant %s (%s) — hard-deleted all data", tid, tenant_name
            )

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.error(
                "Demo purge: tenant %s (%s) FAILED — rolled back, skipping: %s",
                tid, tenant_name, e,
            )
        finally:
            _put_conn(conn)

    logger.info("Demo purge complete: %d tenants purged", len(expired))
