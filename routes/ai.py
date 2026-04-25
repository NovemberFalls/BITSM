"""AI blueprint: RAG chat endpoints (self-service + agent-assist)."""

import json
import logging
import time
from datetime import date, timedelta

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app import limiter
from routes.auth import login_required, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
ai_bp = Blueprint("ai", __name__)

ESCALATE_MARKER = "<<ESCALATE>>"
RESOLVED_MARKER = "[RESOLVED]"


def _auto_resolve_ticket(ticket_id: int, tenant_id: int) -> None:
    """Auto-resolve a ticket when Atlas detects the user confirmed resolution in chat.

    Mirrors the logic in atlas_service._post_follow_up but triggered from the
    RAG chat endpoint rather than the ticket-comment follow-up path.
    Blocks resolution if required-to-close custom fields are still unfilled.
    """
    try:
        # Guard: check required-to-close custom fields before resolving
        from routes.tickets import _get_missing_required_to_close
        ticket_row = fetch_one("SELECT ticket_type FROM tickets WHERE id = %s", [ticket_id])
        ticket_type = ticket_row.get("ticket_type", "support") if ticket_row else "support"
        missing = _get_missing_required_to_close(ticket_id, tenant_id, ticket_type)
        if missing:
            # Do NOT resolve — post an internal note instead
            missing_str = ", ".join(f'"{m}"' for m in missing)
            insert_returning(
                """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                   VALUES (%s, NULL, %s, true, true) RETURNING id""",
                [ticket_id, f"Atlas attempted auto-resolve but blocked: required-to-close fields still unfilled: {missing_str}. Ticket remains open until these are collected."],
            )
            logger.info("Atlas auto-resolve blocked on ticket %s — missing required-to-close: %s", ticket_id, missing)
            return

        execute(
            """UPDATE tickets SET status = 'resolved', updated_at = now()
               WHERE id = %s AND tenant_id = %s AND status IN ('open', 'pending')""",
            [ticket_id, tenant_id],
        )
        execute(
            """UPDATE atlas_engagements SET resolved_by_ai = true, status = 'closed'
               WHERE ticket_id = %s""",
            [ticket_id],
        )
        insert_returning(
            """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
               VALUES (%s, NULL, %s, true, true) RETURNING id""",
            [ticket_id, "Atlas confirmed resolution — ticket auto-resolved via chat."],
        )
        # Kick off the close-time audit pipeline (background — non-blocking)
        try:
            from services.queue_service import enqueue_ticket_close
            enqueue_ticket_close(ticket_id, tenant_id)
        except Exception as qe:
            logger.warning("Auto-resolve: could not enqueue close pipeline for ticket %s: %s", ticket_id, qe)
        logger.info("Atlas auto-resolved ticket %s via chat confirmation", ticket_id)
    except Exception as e:
        logger.error("Auto-resolve ticket %s failed: %s", ticket_id, e)


def _log_chat_step(tenant_id: int, ticket_id: int | None, duration_ms: int,
                   sources: list, turn_number: int, auto_resolved: bool = False,
                   status: str = "success", error: str | None = None) -> None:
    """Write an atlas_chat entry to pipeline_execution_log for auditability."""
    try:
        article_titles = [s.get("title", "?") for s in (sources or [])[:3]]
        summary_parts = [f"Turn {turn_number}"]
        if article_titles:
            summary_parts.append(f"KB: {'; '.join(article_titles)}")
        if auto_resolved:
            summary_parts.append("✓ auto-resolved")
        insert_returning(
            """INSERT INTO pipeline_execution_log
               (tenant_id, ticket_id, step_name, status, duration_ms, attempts, output_summary, error_message)
               VALUES (%s, %s, 'atlas_chat', %s, %s, 1, %s, %s) RETURNING id""",
            [tenant_id, ticket_id, status, duration_ms,
             " · ".join(summary_parts) if summary_parts else None, error],
        )
    except Exception as e:
        logger.warning("Failed to log atlas_chat pipeline step: %s", e)


def _fetch_ticket_context(ticket_id: int, tenant_id: int) -> dict | None:
    """Fetch structured ticket context for injection into the AI system prompt."""
    ticket = fetch_one(
        """SELECT t.id, t.subject, t.description, t.status, t.priority,
                  t.created_at, t.ticket_type, t.story_points,
                  t.acceptance_criteria, t.work_item_number,
                  req.name AS requester_name,
                  asg.name AS assignee_name,
                  loc.name AS location_name,
                  pc.name  AS category_name,
                  wit.name AS work_item_type_name,
                  sp.name  AS sprint_name
           FROM tickets t
           LEFT JOIN users req ON req.id = t.requester_id
           LEFT JOIN users asg ON asg.id = t.assignee_id
           LEFT JOIN locations loc ON loc.id = t.location_id
           LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
           LEFT JOIN work_item_types wit ON wit.id = t.work_item_type_id
           LEFT JOIN sprints sp ON sp.id = t.sprint_id
           WHERE t.id = %s AND t.tenant_id = %s""",
        [ticket_id, tenant_id],
    )
    if not ticket:
        return None

    # Fetch recent comments (skip AI-generated internal notes to avoid recursion)
    comments = fetch_all(
        """SELECT tc.content, tc.is_internal, tc.created_at,
                  u.name AS author
           FROM ticket_comments tc
           LEFT JOIN users u ON u.id = tc.author_id
           WHERE tc.ticket_id = %s
             AND NOT (tc.is_ai_generated = true AND tc.is_internal = true)
           ORDER BY tc.created_at DESC
           LIMIT 5""",
        [ticket_id],
    )

    # Custom fields: load definitions + values for this ticket
    # Inherits from ancestor categories via recursive CTE
    ticket_type = ticket.get("ticket_type", "support")
    problem_cat_id = ticket.get("problem_category_id")
    if problem_cat_id:
        cf_defs = fetch_all(
            """WITH RECURSIVE cat_ancestors AS (
                   SELECT id FROM problem_categories WHERE id = %s
                   UNION ALL
                   SELECT pc.parent_id
                   FROM problem_categories pc
                   JOIN cat_ancestors ca ON pc.id = ca.id
                   WHERE pc.parent_id IS NOT NULL
               )
               SELECT * FROM custom_field_definitions
               WHERE tenant_id = %s AND is_active = true
                 AND (category_id IN (SELECT id FROM cat_ancestors)
                      OR (category_id IS NULL AND %s = ANY(applies_to)))
               ORDER BY sort_order, id""",
            [problem_cat_id, tenant_id, ticket_type],
        )
    else:
        cf_defs = fetch_all(
            """SELECT * FROM custom_field_definitions
               WHERE tenant_id = %s AND is_active = true
                 AND category_id IS NULL AND %s = ANY(applies_to)
               ORDER BY sort_order, id""",
            [tenant_id, ticket_type],
        )

    cf_values_raw = fetch_all(
        "SELECT field_id, value FROM ticket_custom_field_values WHERE ticket_id = %s",
        [ticket_id],
    ) if cf_defs else []
    cf_values_map = {row["field_id"]: row["value"] for row in cf_values_raw}

    custom_fields_context = []
    unfilled_required_to_close = []
    for f in cf_defs:
        filled = f["id"] in cf_values_map
        entry = {
            "name": f["name"],
            "key": f["field_key"],
            "type": f["field_type"],
            "required_to_create": bool(f.get("is_required_to_create", False)),
            "required_to_close": bool(f.get("is_required_to_close", False)),
            "customer_facing": f.get("is_customer_facing", False),
            "agent_facing": f.get("is_agent_facing", True),
            "value": cf_values_map.get(f["id"]),
            "filled": filled,
        }
        if f.get("field_type") in ("select", "multi_select"):
            entry["options"] = f.get("options") or []
        custom_fields_context.append(entry)
        if f.get("is_required_to_close") and not filled:
            unfilled_required_to_close.append(f["name"])

    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject", ""),
        "description": ticket.get("description", ""),
        "status": ticket.get("status", ""),
        "priority": ticket.get("priority", ""),
        "ticket_type": ticket.get("ticket_type", "support"),
        "requester_name": ticket.get("requester_name", ""),
        "assignee_name": ticket.get("assignee_name", ""),
        "location_name": ticket.get("location_name", ""),
        "category_name": ticket.get("category_name", ""),
        "work_item_type_name": ticket.get("work_item_type_name", ""),
        "sprint_name": ticket.get("sprint_name", ""),
        "story_points": ticket.get("story_points"),
        "acceptance_criteria": ticket.get("acceptance_criteria", ""),
        "work_item_number": ticket.get("work_item_number", ""),
        "custom_fields": custom_fields_context,
        "unfilled_required_to_close": unfilled_required_to_close,
        "recent_comments": [
            {
                "author": c.get("author", "Unknown"),
                "content": c.get("content", ""),
                "is_internal": c.get("is_internal", False),
                "created_at": str(c.get("created_at", "")),
            }
            for c in (comments or [])
        ],
    }


@ai_bp.route("/chat", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def chat():
    """RAG-powered chat endpoint. Supports both text and streaming responses."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    # --- Billing cap gate: block before any LLM work ---
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError as e:
        if e.reason == "ai_not_included":
            return jsonify({"error": "ai_not_included", "upgrade_to": "starter"}), 402
        elif e.reason == "byok_required":
            return jsonify({"error": "byok_required"}), 402
        else:
            next_month = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
            return jsonify({
                "error": "api_cap_reached",
                "tier": e.tier,
                "reset_date": next_month.isoformat(),
            }), 402
    except Exception:
        logger.warning("billing gate check failed — failing open", exc_info=True)
        pass  # never let billing errors block user-facing AI

    data = request.json or {}

    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query is required"}), 400

    conversation_id = data.get("conversation_id")
    language = data.get("language", "en")
    ticket_id = data.get("ticket_id")  # Agent-assist mode: context of a ticket
    stream = data.get("stream", False)

    # Derive persona from user role — agents get vendor-specific language,
    # end_users get customer-friendly language (no vendor names)
    persona = "agent" if user["role"] in ("super_admin", "tenant_admin", "agent") else "end_user"

    # Fetch ticket context when in agent-assist mode
    ticket_context = _fetch_ticket_context(ticket_id, tenant_id) if ticket_id else None

    # Load or create conversation
    if conversation_id:
        conv = _get_conversation(conversation_id)
        is_super = user.get("role") == "super_admin"
        if conv and not is_super and conv.get("tenant_id") != tenant_id:
            conv = None  # wrong tenant — start fresh
        messages = conv["messages"] if conv else []
        l2_analysis = (conv or {}).get("l2_analysis")
    else:
        messages = []
        l2_analysis = None
        conversation_id = insert_returning(
            """INSERT INTO ai_conversations (tenant_id, user_id, ticket_id, language, channel)
               VALUES (%s, %s, %s, %s, 'text') RETURNING id""",
            [tenant_id, user["id"], ticket_id, language],
        )

    # Append user message
    messages.append({"role": "user", "content": query})

    # Determine the right caller for usage tracking
    chat_caller = "atlas.follow_up" if ticket_id else "l1_chat"

    # ---- Layer 1 (Haiku) — direct Claude with enriched system prompt ----
    if stream:
        return Response(
            stream_with_context(_stream_response_contextual(
                conversation_id, tenant_id, messages, language, l2_analysis, persona,
                ticket_context=ticket_context, caller=chat_caller, ticket_id=ticket_id,
            )),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        from services.rag_service import generate_response_contextual
        _t0 = time.monotonic()
        result = generate_response_contextual(
            tenant_id, messages, language, l2_analysis=l2_analysis, persona=persona,
            ticket_context=ticket_context, caller=chat_caller, ticket_id=ticket_id,
        )
        _duration_ms = int((time.monotonic() - _t0) * 1000)
        answer = result["answer"]
        sources = result.get("sources", [])

        # Check for auto-escalation signal from L1
        if ESCALATE_MARKER in answer:
            answer = answer.replace(ESCALATE_MARKER, "").rstrip()
            messages.append({"role": "assistant", "content": answer})
            _save_conversation(conversation_id, messages, result.get("modules_used", []), result.get("tokens", 0))
            _record_article_recommendations(conversation_id, tenant_id, sources, layer=1)

            # Fire L2
            l2_result = _auto_escalate_to_l2(conversation_id, tenant_id, messages, language)
            if l2_result:
                return jsonify({
                    "conversation_id": conversation_id,
                    "answer": answer,
                    "sources": sources,
                    "modules_used": result.get("modules_used", []),
                    "escalation": {
                        "answer": l2_result["answer"],
                        "sources": l2_result.get("sources", []),
                        "layer": 2,
                    },
                })

        # Check for resolution signal (ticket-scoped chats only)
        auto_resolved = False
        if RESOLVED_MARKER in answer and ticket_id:
            answer = answer.replace(RESOLVED_MARKER, "").rstrip()
            _auto_resolve_ticket(ticket_id, tenant_id)
            auto_resolved = True

        messages.append({"role": "assistant", "content": answer})
        turn_number = sum(1 for m in messages if m["role"] == "user")
        _save_conversation(conversation_id, messages, result.get("modules_used", []), result.get("tokens", 0))
        _record_article_recommendations(conversation_id, tenant_id, sources, layer=1)
        _log_chat_step(tenant_id, ticket_id, _duration_ms, sources, turn_number, auto_resolved)
        return jsonify({
            "conversation_id": conversation_id,
            "answer": answer,
            "sources": sources,
            "modules_used": result.get("modules_used", []),
            "auto_resolved": auto_resolved,
        })


def _stream_response_contextual(conversation_id, tenant_id, messages, language, l2_analysis=None, persona="agent", ticket_context=None, caller="l1_chat", ticket_id=None):
    """SSE streaming via Layer 1 (Haiku) with contextual KB pre-search. Detects <<ESCALATE>> and auto-fires L2."""
    yield f"data: {json.dumps({'type': 'conversation_id', 'conversation_id': conversation_id})}\n\n"

    from services.rag_service import generate_response_stream_contextual

    full_response = ""
    modules_used = []
    tokens = 0
    sources = []
    _t0 = time.monotonic()

    for chunk in generate_response_stream_contextual(
        tenant_id, messages, language, l2_analysis=l2_analysis, persona=persona,
        ticket_context=ticket_context, caller=caller, ticket_id=ticket_id,
    ):
        if chunk.get("type") == "status":
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "text":
            full_response += chunk["content"]
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "sources":
            modules_used = chunk.get("modules_used", [])
            sources = chunk.get("sources", [])
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "done":
            tokens = chunk.get("tokens", 0)
            # Don't yield done yet — check for escalation first

    # Check for auto-escalation signal
    if ESCALATE_MARKER in full_response:
        full_response = full_response.replace(ESCALATE_MARKER, "").rstrip()
        messages.append({"role": "assistant", "content": full_response})
        _save_conversation(conversation_id, messages, modules_used, tokens)
        _record_article_recommendations(conversation_id, tenant_id, sources, layer=1)

        # Signal escalation to frontend
        yield f"data: {json.dumps({'type': 'escalation', 'content': 'Consulting with a colleague, please allow me a moment...'})}\n\n"

        # Fire L2 synchronously (client sees status messages)
        l2_result = _auto_escalate_to_l2(conversation_id, tenant_id, messages, language)
        if l2_result:
            l2_answer = l2_result["answer"]
            # Stream L2 response in chunks
            chunk_size = 12
            for i in range(0, len(l2_answer), chunk_size):
                yield f"data: {json.dumps({'type': 'text', 'content': l2_answer[i:i + chunk_size]})}\n\n"

            if l2_result.get("sources"):
                yield f"data: {json.dumps({'type': 'sources', 'sources': l2_result['sources'], 'modules_used': l2_result.get('modules_used', [])})}\n\n"

            # L2 response becomes a new assistant message
            messages.append({"role": "assistant", "content": l2_answer})
            _save_conversation(conversation_id, messages, modules_used, tokens + l2_result.get("tokens", 0))
            _record_article_recommendations(conversation_id, tenant_id, l2_result.get("sources", []), layer=2)

        yield f"data: {json.dumps({'type': 'done', 'tokens': tokens})}\n\n"
        return

    # Normal flow — no escalation
    _duration_ms = int((time.monotonic() - _t0) * 1000)
    auto_resolved = False

    # Check for resolution signal (ticket-scoped chats only)
    if RESOLVED_MARKER in full_response and ticket_context:
        full_response = full_response.replace(RESOLVED_MARKER, "").rstrip()
        ticket_id_val = ticket_context.get("id")
        if ticket_id_val:
            _auto_resolve_ticket(ticket_id_val, tenant_id)
            auto_resolved = True
            yield f"data: {json.dumps({'type': 'resolved', 'ticket_id': ticket_id_val})}\n\n"

    ticket_id_val = (ticket_context or {}).get("id")
    turn_number = sum(1 for m in messages if m["role"] == "user")
    messages.append({"role": "assistant", "content": full_response})
    _save_conversation(conversation_id, messages, modules_used, tokens)
    _record_article_recommendations(conversation_id, tenant_id, sources, layer=1)
    _log_chat_step(tenant_id, ticket_id_val, _duration_ms, sources, turn_number, auto_resolved)
    yield f"data: {json.dumps({'type': 'done', 'tokens': tokens})}\n\n"


def _auto_escalate_to_l2(conversation_id, tenant_id, messages, language):
    """Fire L2 Sonnet one-shot and store analysis for future L1 turns."""
    from services.rag_service import generate_response_l2_contextual

    try:
        # Mark conversation as escalated
        execute(
            "UPDATE ai_conversations SET status = 'escalated_l2', updated_at = now() WHERE id = %s",
            [conversation_id],
        )

        result = generate_response_l2_contextual(tenant_id, messages, language)
        l2_answer = result.get("answer", "")

        # Store L2 analysis for subsequent L1 turns (handback)
        if l2_answer:
            execute(
                "UPDATE ai_conversations SET l2_analysis = %s WHERE id = %s",
                [l2_answer, conversation_id],
            )

        return result
    except Exception as e:
        logger.error("Auto-escalation to L2 failed: %s", e)
        return None


def _record_article_recommendations(conversation_id, tenant_id, sources, layer=1):
    """Record which articles were recommended for feedback loop tracking."""
    if not sources:
        return
    try:
        conv = _get_conversation(conversation_id)
        turn_number = (conv or {}).get("turn_count", 1)
        for src in sources:
            doc_id = src.get("document_id")
            if doc_id:
                execute(
                    """INSERT INTO article_recommendations
                       (conversation_id, document_id, tenant_id, turn_number, layer)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [conversation_id, doc_id, tenant_id, turn_number, layer],
                )
    except Exception as e:
        logger.warning("Failed to record article recommendations: %s", e)


def _get_conversation(conversation_id: int) -> dict | None:
    return fetch_one("SELECT * FROM ai_conversations WHERE id = %s", [conversation_id])


def _save_conversation(conversation_id: int, messages: list, modules_used: list, tokens: int):
    # Store only simple text messages (strip tool-use rounds from history)
    simple_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if isinstance(m.get("content"), str)
    ]
    turn_count = sum(1 for m in simple_messages if m["role"] == "user")
    execute(
        """UPDATE ai_conversations
           SET messages = %s::jsonb, modules_used = %s, tokens_used = tokens_used + %s,
               updated_at = now(), turn_count = %s
           WHERE id = %s""",
        [json.dumps(simple_messages), modules_used, tokens, turn_count, conversation_id],
    )


@ai_bp.route("/chat/escalate", methods=["POST"])
@login_required
def escalate_to_l2():
    """Escalate a conversation from Layer 1 (Haiku) to Layer 2 (Sonnet).

    The frontend calls this when the user says L1's answer didn't help.
    Sonnet receives the full L1 conversation for deep analysis.
    """
    tenant_id = get_tenant_id()

    # --- Billing cap gate: block before any LLM work ---
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError as e:
        if e.reason == "ai_not_included":
            return jsonify({"error": "ai_not_included", "upgrade_to": "starter"}), 402
        elif e.reason == "byok_required":
            return jsonify({"error": "byok_required"}), 402
        else:
            next_month = (date.today().replace(day=1) + timedelta(days=32)).replace(day=1)
            return jsonify({
                "error": "api_cap_reached",
                "tier": e.tier,
                "reset_date": next_month.isoformat(),
            }), 402
    except Exception:
        logger.warning("billing gate check failed — failing open", exc_info=True)
        pass  # never let billing errors block user-facing AI

    data = request.json or {}

    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return jsonify({"error": "conversation_id required"}), 400

    additional_context = data.get("additional_context", "").strip()
    stream = data.get("stream", False)

    conv = _get_conversation(conversation_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404

    user = get_current_user()
    is_super = user.get("role") == "super_admin"
    if not is_super and conv.get("tenant_id") != tenant_id:
        return jsonify({"error": "Conversation not found"}), 404

    messages = conv.get("messages") or []
    language = conv.get("language", "en")

    # Append user's additional context if provided
    if additional_context:
        messages.append({"role": "user", "content": additional_context})

    # Mark conversation as escalated
    execute(
        "UPDATE ai_conversations SET status = 'escalated_l2', updated_at = now() WHERE id = %s",
        [conversation_id],
    )

    if stream:
        return Response(
            stream_with_context(_stream_response_l2(conversation_id, tenant_id, messages, language)),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        from services.rag_service import generate_response_l2_contextual
        result = generate_response_l2_contextual(tenant_id, messages, language)
        messages.append({"role": "assistant", "content": result["answer"]})
        _save_conversation(conversation_id, messages, result.get("modules_used", []), result.get("tokens", 0))
        return jsonify({
            "conversation_id": conversation_id,
            "answer": result["answer"],
            "sources": result.get("sources", []),
            "layer": 2,
        })


def _stream_response_l2(conversation_id, tenant_id, messages, language):
    """SSE streaming via Layer 2 (Sonnet deep analysis)."""
    yield f"data: {json.dumps({'type': 'conversation_id', 'conversation_id': conversation_id})}\n\n"
    yield f"data: {json.dumps({'type': 'escalation', 'content': 'Consulting with a colleague, please allow me a moment...'})}\n\n"

    from services.rag_service import generate_response_stream_l2_contextual

    full_response = ""
    modules_used = []
    tokens = 0

    for chunk in generate_response_stream_l2_contextual(tenant_id, messages, language):
        if chunk.get("type") == "status":
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "text":
            full_response += chunk["content"]
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "sources":
            modules_used = chunk.get("modules_used", [])
            yield f"data: {json.dumps(chunk)}\n\n"
        elif chunk.get("type") == "done":
            tokens = chunk.get("tokens", 0)
            yield f"data: {json.dumps(chunk)}\n\n"

    messages.append({"role": "assistant", "content": full_response})
    _save_conversation(conversation_id, messages, modules_used, tokens)


@ai_bp.route("/conversations", methods=["GET"])
@login_required
def list_conversations():
    """List AI conversations for the current user."""
    user = get_current_user()
    include_archived = request.args.get("include_archived", "false") == "true"
    status_filter = "" if include_archived else "AND (status = 'active' OR status IS NULL)"
    convs = fetch_all(
        f"""SELECT c.id, c.tenant_id, c.ticket_id, c.language, c.channel,
                  c.tokens_used, c.escalated_to_ticket, c.created_at, c.updated_at,
                  c.status, c.turn_count,
                  c.messages->0->>'content' as first_message,
                  t.status as ticket_status,
                  t.ticket_number
           FROM ai_conversations c
           LEFT JOIN tickets t ON t.id = c.ticket_id
           WHERE c.user_id = %s {status_filter.replace('status', 'c.status')}
           ORDER BY COALESCE(c.updated_at, c.created_at) DESC
           LIMIT 50""",
        [user["id"]],
    )
    return jsonify(convs)


@ai_bp.route("/conversations/by-ticket/<int:ticket_id>", methods=["GET"])
@require_permission("atlas.chat")
def get_conversation_by_ticket(ticket_id: int):
    """Get the most recent Atlas conversation for a ticket (agent-assist mode).

    Scopes by tenant_id rather than user_id — agent-assist conversations may
    have NULL user_id when the session user lookup fails at insert time.
    """
    user = get_current_user()
    tenant_id = get_tenant_id()

    if user["role"] == "super_admin":
        conv = fetch_one(
            """SELECT * FROM ai_conversations
               WHERE ticket_id = %s
               ORDER BY COALESCE(updated_at, created_at) DESC
               LIMIT 1""",
            [ticket_id],
        )
    else:
        conv = fetch_one(
            """SELECT * FROM ai_conversations
               WHERE ticket_id = %s AND tenant_id = %s
               ORDER BY COALESCE(updated_at, created_at) DESC
               LIMIT 1""",
            [ticket_id, tenant_id],
        )
    return jsonify(conv)  # None serialises as JSON null


@ai_bp.route("/conversations/<int:conv_id>", methods=["GET"])
@login_required
def get_conversation(conv_id: int):
    """Get full conversation detail including messages."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    # Allow lookup by tenant_id as fallback when user_id is NULL (agent-assist rows)
    if user["role"] == "super_admin":
        conv = fetch_one("SELECT * FROM ai_conversations WHERE id = %s", [conv_id])
    else:
        conv = fetch_one(
            """SELECT * FROM ai_conversations
               WHERE id = %s AND (user_id = %s OR tenant_id = %s)""",
            [conv_id, user["id"], tenant_id],
        )
    if not conv:
        return jsonify({"error": "Not found"}), 404
    return jsonify(conv)


@ai_bp.route("/conversations/<int:conv_id>/feedback", methods=["POST"])
@login_required
def submit_feedback(conv_id: int):
    """Submit thumbs up/down feedback on an assistant message."""
    user = get_current_user()
    data = request.json or {}

    message_index = data.get("message_index")
    rating = data.get("rating")

    if message_index is None or rating not in ("positive", "negative"):
        return jsonify({"error": "message_index (int) and rating ('positive'|'negative') required"}), 400

    tenant_id = get_tenant_id()
    conv = fetch_one(
        """SELECT id, feedback FROM ai_conversations
           WHERE id = %s AND (user_id = %s OR tenant_id = %s)""",
        [conv_id, user["id"], tenant_id],
    )
    if not conv:
        return jsonify({"error": "Not found"}), 404

    feedback_list = conv.get("feedback") or []
    # Remove existing feedback for this message index
    feedback_list = [f for f in feedback_list if f.get("message_index") != message_index]
    feedback_list.append({
        "message_index": message_index,
        "rating": rating,
        "comment": data.get("comment", ""),
    })

    execute(
        "UPDATE ai_conversations SET feedback = %s::jsonb WHERE id = %s",
        [json.dumps(feedback_list), conv_id],
    )
    return jsonify({"ok": True})


@ai_bp.route("/conversations/<int:conv_id>/articles", methods=["GET"])
@login_required
def get_conversation_articles(conv_id: int):
    """Return KB articles recommended during a conversation, for user rating."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    # Verify access to conversation
    if user["role"] == "super_admin":
        conv = fetch_one("SELECT id FROM ai_conversations WHERE id = %s", [conv_id])
    else:
        conv = fetch_one(
            "SELECT id FROM ai_conversations WHERE id = %s AND (user_id = %s OR tenant_id = %s)",
            [conv_id, user["id"], tenant_id],
        )
    if not conv:
        return jsonify({"error": "Not found"}), 404

    rows = fetch_all(
        """SELECT ar.id, ar.document_id, ar.turn_number, ar.user_helpful, ar.rated_at,
                  d.title, d.url, d.module_id
           FROM article_recommendations ar
           JOIN documents d ON d.id = ar.document_id
           WHERE ar.conversation_id = %s
           ORDER BY ar.turn_number DESC, ar.id DESC""",
        [conv_id],
    )
    # Deduplicate by document_id, keeping latest turn's recommendation
    seen = set()
    unique = []
    for row in rows:
        if row["document_id"] not in seen:
            seen.add(row["document_id"])
            unique.append(row)
    return jsonify(unique)


@ai_bp.route("/article-recommendations/<int:rec_id>/rate", methods=["POST"])
@login_required
def rate_article_recommendation(rec_id: int):
    """User rates a KB article recommendation as helpful or not."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}
    helpful = data.get("helpful")  # True, False, or None to clear

    if helpful is not None and not isinstance(helpful, bool):
        return jsonify({"error": "helpful must be true, false, or null"}), 400

    # Verify the recommendation belongs to this tenant
    if user["role"] == "super_admin":
        rec = fetch_one("SELECT id FROM article_recommendations WHERE id = %s", [rec_id])
    else:
        rec = fetch_one(
            "SELECT id FROM article_recommendations WHERE id = %s AND tenant_id = %s",
            [rec_id, tenant_id],
        )
    if not rec:
        return jsonify({"error": "Not found"}), 404

    execute(
        """UPDATE article_recommendations
           SET user_helpful = %s, rated_at = CASE WHEN %s IS NOT NULL THEN now() ELSE NULL END
           WHERE id = %s""",
        [helpful, helpful, rec_id],
    )

    # Recalculate effectiveness score for the rated document (fire-and-forget)
    if helpful is not None:
        doc_row = fetch_one("SELECT document_id FROM article_recommendations WHERE id = %s", [rec_id])
        if doc_row and doc_row.get("document_id"):
            import threading
            from services.rag_service import recalculate_effectiveness
            threading.Thread(
                target=recalculate_effectiveness, args=(doc_row["document_id"],), daemon=True
            ).start()

    return jsonify({"ok": True})


@ai_bp.route("/conversations/<int:conv_id>/archive", methods=["POST"])
@login_required
def archive_conversation(conv_id: int):
    """Archive a conversation (e.g., after inactivity timeout)."""
    user = get_current_user()
    conv = fetch_one(
        "SELECT id, updated_at FROM ai_conversations WHERE id = %s AND user_id = %s",
        [conv_id, user["id"]],
    )
    if not conv:
        return jsonify({"error": "Not found"}), 404

    execute(
        "UPDATE ai_conversations SET status = 'archived' WHERE id = %s",
        [conv_id],
    )
    return jsonify({"ok": True})


@ai_bp.route("/send-to-ticket", methods=["POST"])
@login_required
@require_permission("atlas.chat")
def send_chat_response_to_ticket():
    """Send an AI chat response (raw text) as a reply comment on a ticket."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}

    content = (data.get("content") or "").strip()
    ticket_id = data.get("ticket_id")
    if not content or not ticket_id:
        return jsonify({"error": "content and ticket_id required"}), 400

    # Verify ticket access
    if user["role"] == "super_admin":
        ticket = fetch_one("SELECT id FROM tickets WHERE id = %s", [ticket_id])
    else:
        ticket = fetch_one(
            "SELECT id FROM tickets WHERE id = %s AND tenant_id = %s",
            [ticket_id, tenant_id],
        )
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    # Truncate very long responses
    if len(content) > 5000:
        content = content[:5000] + "\n\n[Response truncated]"

    is_internal = bool(data.get("is_internal", False))

    reply_content = f"**AI Assistant Response**\n\n{content}"

    comment_id = insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, %s, %s, %s, true) RETURNING id""",
        [ticket_id, user["id"], reply_content, is_internal],
    )

    execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

    # Track first response time for agent replies (non-internal only)
    if not is_internal and user["role"] in ("super_admin", "tenant_admin", "agent"):
        execute(
            "UPDATE tickets SET first_response_at = now() WHERE id = %s AND first_response_at IS NULL",
            [ticket_id],
        )

    # Dispatch notifications (email, Teams, Slack, in-app) — mirrors tickets.py comment flow
    try:
        from services.queue_service import enqueue_notify
        author_name = user.get("name", "")
        comment_dict = {"content": reply_content, "author_name": author_name}
        if is_internal:
            enqueue_notify(tenant_id, ticket_id, "internal_note", comment=comment_dict)
        else:
            enqueue_notify(tenant_id, ticket_id, "agent_reply", comment=comment_dict)
    except Exception as e:
        logger.warning("send-to-ticket notification dispatch failed: %s", e)

    # Fire matching automations
    try:
        from services.automation_engine import fire_automations
        fire_automations("comment_added", ticket_id, tenant_id,
                         {"comment_type": "public" if not is_internal else "internal"})
    except Exception as e:
        logger.warning("send-to-ticket automation dispatch failed: %s", e)

    return jsonify({"id": comment_id})


@ai_bp.route("/enrich/<int:ticket_id>", methods=["POST"])
@require_permission("atlas.chat")
def enrich_ticket_endpoint(ticket_id: int):
    """Manually trigger AI enrichment for a ticket."""
    tenant_id = get_tenant_id()
    user = get_current_user()
    is_super = user.get("role") == "super_admin"

    ticket = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    if not is_super and ticket.get("tenant_id") != tenant_id:
        return jsonify({"error": "Not found"}), 404

    from services.enrichment_service import enrich_ticket
    enrich_ticket(ticket_id)
    return jsonify({"ok": True, "message": "Enrichment started"})


# ============================================================
# Atlas engagement status (for AtlasTab UI indicator)
# ============================================================

@ai_bp.route("/engagement/<int:ticket_id>", methods=["GET"])
@login_required
def get_engagement_status(ticket_id: int):
    """Get Atlas engagement status for a ticket (active/passive/closed)."""
    tenant_id = get_tenant_id()
    user = get_current_user()
    is_super = user.get("role") == "super_admin"

    # Verify the caller owns this ticket before exposing engagement data
    ticket_row = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket_row:
        return jsonify({"status": "none"})
    if not is_super and ticket_row.get("tenant_id") != tenant_id:
        return jsonify({"status": "none"})

    engagement = fetch_one(
        """SELECT ae.id, ae.status, ae.engagement_type, ae.human_took_over,
                  ae.human_took_over_at, ae.resolved_by_ai,
                  ae.kb_articles_referenced, ae.similar_ticket_ids,
                  ae.suggested_category_id, ae.category_confidence,
                  pc.name as suggested_category_name,
                  ae.created_at, ae.updated_at
           FROM atlas_engagements ae
           LEFT JOIN problem_categories pc ON pc.id = ae.suggested_category_id
           WHERE ae.ticket_id = %s
           ORDER BY ae.created_at DESC
           LIMIT 1""",
        [ticket_id],
    )

    if not engagement:
        return jsonify({"status": "none"})

    # Fetch similar ticket details if any — scoped to caller's tenant to prevent
    # leaking subjects from other tenants via the similar_ticket_ids array
    similar_tickets = []
    if engagement.get("similar_ticket_ids"):
        ids = engagement["similar_ticket_ids"]
        if ids:
            placeholders = ", ".join(["%s"] * len(ids))
            if is_super:
                similar_tickets = fetch_all(
                    f"""SELECT id, ticket_number, subject, status, priority
                        FROM tickets WHERE id IN ({placeholders})""",
                    ids,
                )
            else:
                similar_tickets = fetch_all(
                    f"""SELECT id, ticket_number, subject, status, priority
                        FROM tickets WHERE id IN ({placeholders}) AND tenant_id = %s""",
                    ids + [tenant_id],
                )

    return jsonify({
        "status": engagement["status"],
        "engagement_type": engagement.get("engagement_type"),
        "human_took_over": engagement.get("human_took_over", False),
        "resolved_by_ai": engagement.get("resolved_by_ai", False),
        "kb_articles_referenced": engagement.get("kb_articles_referenced") or [],
        "suggested_category_id": engagement.get("suggested_category_id"),
        "suggested_category_name": engagement.get("suggested_category_name"),
        "category_confidence": engagement.get("category_confidence"),
        "similar_tickets": similar_tickets,
        "created_at": str(engagement.get("created_at", "")),
    })


# ============================================================
# Incident linking
# ============================================================

@ai_bp.route("/tickets/<int:ticket_id>/link-incident", methods=["POST"])
@require_permission("atlas.chat")
def link_incident(ticket_id: int):
    """Link a ticket as a child of an incident (parent ticket).

    Body: { parent_ticket_id: int }
    """
    data = request.json or {}
    parent_id = data.get("parent_ticket_id")
    if not parent_id:
        return jsonify({"error": "parent_ticket_id required"}), 400

    tenant_id = get_tenant_id()
    user = get_current_user()
    is_super = user.get("role") == "super_admin"

    # Verify both tickets exist and belong to same tenant
    ticket = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    parent = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [parent_id])

    if not ticket or not parent:
        return jsonify({"error": "Not found"}), 404
    if not is_super and ticket.get("tenant_id") != tenant_id:
        return jsonify({"error": "Not found"}), 404
    if not is_super and parent.get("tenant_id") != tenant_id:
        return jsonify({"error": "Not found"}), 404
    if ticket.get("tenant_id") != parent.get("tenant_id"):
        return jsonify({"error": "Tickets must belong to the same tenant"}), 400
    if ticket_id == parent_id:
        return jsonify({"error": "Cannot link ticket to itself"}), 400

    execute(
        "UPDATE tickets SET parent_ticket_id = %s, updated_at = now() WHERE id = %s",
        [parent_id, ticket_id],
    )

    # Post internal note about the link
    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, %s, %s, true, false) RETURNING id""",
        [ticket_id, user["id"],
         f"Linked as related to ticket #{parent_id} (incident group)"],
    )

    return jsonify({"ok": True})


@ai_bp.route("/tickets/<int:ticket_id>/unlink-incident", methods=["POST"])
@require_permission("atlas.chat")
def unlink_incident(ticket_id: int):
    """Remove incident link from a ticket."""
    tenant_id = get_tenant_id()
    ticket = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    user = get_current_user()
    is_super = user.get("role") == "super_admin"
    if not is_super and ticket.get("tenant_id") != tenant_id:
        return jsonify({"error": "Not found"}), 404
    execute(
        "UPDATE tickets SET parent_ticket_id = NULL, updated_at = now() WHERE id = %s",
        [ticket_id],
    )
    return jsonify({"ok": True})


@ai_bp.route("/tickets/<int:ticket_id>/incident-children", methods=["GET"])
@login_required
def get_incident_children(ticket_id: int):
    """Get child tickets linked to this parent (incident group members)."""
    tenant_id = get_tenant_id()
    parent = fetch_one("SELECT id, tenant_id FROM tickets WHERE id = %s", [ticket_id])
    if not parent:
        return jsonify([])
    user = get_current_user()
    is_super = user.get("role") == "super_admin"
    if not is_super and parent.get("tenant_id") != tenant_id:
        return jsonify([])
    children = fetch_all(
        """SELECT id, ticket_number, subject, status, priority, created_at
           FROM tickets WHERE parent_ticket_id = %s AND tenant_id = %s
           ORDER BY created_at DESC""",
        [ticket_id, tenant_id if not is_super else parent.get("tenant_id")],
    )
    return jsonify(children)


# ============================================================
# Chat-to-case: create ticket from ChatWidget conversation
# ============================================================

@ai_bp.route("/chat-to-case", methods=["POST"])
@login_required
def chat_to_case():
    """Create a ticket from a ChatWidget conversation.

    Body: { conversation_id: int, subject: string, transcript: string }
    Returns: { ticket_id, ticket_number }
    """
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}

    conversation_id = data.get("conversation_id")
    subject = (data.get("subject") or "Chat conversation").strip()[:100]
    transcript = (data.get("transcript") or "").strip()

    if not conversation_id:
        return jsonify({"error": "conversation_id required"}), 400

    # Check if this conversation already has a linked ticket
    existing = fetch_one(
        "SELECT id, ticket_number FROM tickets WHERE source = 'chat_widget' AND tenant_id = %s AND description LIKE %s",
        [tenant_id, f"%[chat:{conversation_id}]%"],
    )
    if existing:
        return jsonify({"ticket_id": existing["id"], "ticket_number": existing["ticket_number"]})

    # Create ticket
    from routes.tickets import _next_ticket_number, _apply_sla

    ticket_number = _next_ticket_number(tenant_id)
    description = f"{transcript}\n\n[chat:{conversation_id}]"

    ticket_id = insert_returning(
        """INSERT INTO tickets (tenant_id, ticket_number, subject, description,
                                priority, requester_id, source)
           VALUES (%s, %s, %s, %s, 'p3', %s, 'chat_widget')
           RETURNING id""",
        [tenant_id, ticket_number, subject, description, user["id"]],
    )

    _apply_sla(ticket_id, tenant_id, "p3")

    # Dispatch pipeline (auto_tag, enrich — but engage will skip for chat_widget source)
    try:
        from services.queue_service import enqueue_ticket_create
        enqueue_ticket_create(ticket_id, tenant_id, "p3")
    except Exception as e:
        logger.warning("Pipeline dispatch for chat-to-case failed: %s", e)

    logger.info("Chat-to-case: created ticket %s (TKT %s) from conversation %s", ticket_id, ticket_number, conversation_id)

    return jsonify({"ticket_id": ticket_id, "ticket_number": ticket_number}), 201


@ai_bp.route("/chat-to-case/<int:ticket_id>/append", methods=["POST"])
@login_required
def chat_to_case_append(ticket_id: int):
    """Append a chat message as a comment on the linked ticket.

    Body: { content: string, role: 'user' | 'assistant' }
    """
    user = get_current_user()
    data = request.json or {}
    content = (data.get("content") or "").strip()
    role = data.get("role", "user")

    if not content:
        return jsonify({"error": "content required"}), 400

    # Verify ticket access
    tenant_id = get_tenant_id()
    ticket = fetch_one("SELECT id, tenant_id, requester_id FROM tickets WHERE id = %s", [ticket_id])
    if not ticket:
        return jsonify({"error": "Not found"}), 404
    is_super = user.get("role") == "super_admin"
    if not is_super and ticket.get("tenant_id") != tenant_id:
        return jsonify({"error": "Not found"}), 404

    is_ai = role == "assistant"
    author_id = None if is_ai else user["id"]

    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, %s, %s, false, %s) RETURNING id""",
        [ticket_id, author_id, content, is_ai],
    )

    execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

    return jsonify({"ok": True})
