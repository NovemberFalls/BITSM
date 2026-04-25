"""Atlas AI service: ticket auto-engagement, close-time audit, knowledge gaps,
effort scoring, and smart routing.

All heavy LLM calls run in background threads (fire-and-forget).
"""

import json
import logging
import threading

from config import Config
from models.db import fetch_one, fetch_all, insert_returning, execute
from services.llm_provider import complete

logger = logging.getLogger(__name__)


# ============================================================
# Feature-flag check helpers
# ============================================================

def _is_feature_enabled(tenant_id: int, feature_slug: str) -> bool:
    """Check if an AI sub-feature is enabled for a tenant.

    Defaults: agent_chat and client_chat are on when no toggle record exists.
    ticket_review and phone_service default to off.
    """
    # First check if the AI module itself is enabled
    ai_module = fetch_one(
        """SELECT 1 FROM tenant_modules tm
           JOIN knowledge_modules km ON km.id = tm.module_id
           WHERE tm.tenant_id = %s AND km.slug IN ('ai', 'ai_chat')""",
        [tenant_id],
    )
    if not ai_module:
        return False

    row = fetch_one(
        """SELECT tmf.enabled
           FROM tenant_module_features tmf
           JOIN module_features mf ON mf.id = tmf.feature_id
           JOIN knowledge_modules km ON km.id = mf.module_id
           WHERE tmf.tenant_id = %s
             AND km.slug = 'ai'
             AND mf.slug = %s""",
        [tenant_id, feature_slug],
    )
    if row is not None:
        return bool(row.get("enabled"))
    # No toggle record — use defaults
    return feature_slug in ("agent_chat", "client_chat")


def is_ticket_review_enabled(tenant_id: int) -> bool:
    return _is_feature_enabled(tenant_id, "ticket_review")


def is_agent_chat_enabled(tenant_id: int) -> bool:
    return _is_feature_enabled(tenant_id, "agent_chat")


def is_client_chat_enabled(tenant_id: int) -> bool:
    return _is_feature_enabled(tenant_id, "client_chat")


# ============================================================
# 1. Auto-engage on ticket creation (L1)
# ============================================================

DEFAULT_AI_FALLBACK_MESSAGE = (
    "Thank you for reaching out. We've received your request and our team "
    "is actively reviewing it. We'll follow up with you shortly."
)


def _get_fallback_message(tenant_id: int) -> str:
    """Get the AI fallback message for a tenant (customizable in tenant settings)."""
    tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
    settings = (tenant or {}).get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    return settings.get("ai_fallback_message") or DEFAULT_AI_FALLBACK_MESSAGE


def _post_fallback_reply(ticket_id: int, tenant_id: int):
    """Post the tenant's fallback message as a client-facing comment."""
    msg = _get_fallback_message(tenant_id)
    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, NULL, %s, false, true) RETURNING id""",
        [ticket_id, msg],
    )
    execute(
        "UPDATE tickets SET first_response_at = COALESCE(first_response_at, now()), updated_at = now() WHERE id = %s",
        [ticket_id],
    )
    logger.info("Posted AI fallback message for ticket %s", ticket_id)


def _post_buying_time_message(ticket_id: int, tenant_id: int, ticket: dict,
                              similar_tickets: list[dict]):
    """Post a quick interim message while full L1 RAG runs.

    Gives the customer an illusion of motion when pipeline takes >15s.
    Contextually asks for location if missing, mentions similar cases.
    """
    parts = [
        "Thanks for reaching out! I'm reviewing our knowledge base for the best solution."
    ]

    # Ask for location if not set on the ticket
    if not ticket.get("location_name"):
        parts.append(
            "Quick question — which location is this for? "
            "That'll help me find the most relevant information."
        )

    # Mention similar open case if found
    if similar_tickets:
        tkt = similar_tickets[0]
        tkt_num = tkt.get("ticket_number", tkt.get("id"))
        parts.append(
            f"I also noticed a similar open case (TKT-{tkt_num}: "
            f"{tkt.get('subject', '')}) — I'll check if the same solution applies."
        )

    msg = "\n\n".join(parts)
    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, NULL, %s, false, true) RETURNING id""",
        [ticket_id, msg],
    )
    execute(
        "UPDATE tickets SET first_response_at = COALESCE(first_response_at, now()), updated_at = now() WHERE id = %s",
        [ticket_id],
    )
    logger.info("Posted buying-time message for ticket %s", ticket_id)


def auto_engage_ticket(ticket_id: int, tenant_id: int):
    """Fire-and-forget: Atlas L1 engages a newly created ticket."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        # No AI providers — post fallback acknowledgment
        if is_ticket_review_enabled(tenant_id):
            _post_fallback_reply(ticket_id, tenant_id)
        return
    if not is_ticket_review_enabled(tenant_id):
        return

    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background engage", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    thread = threading.Thread(
        target=_engage_worker, args=(ticket_id, tenant_id), daemon=True
    )
    thread.start()


def _build_location_note(ticket_id: int, tenant_id: int) -> str | None:
    """Check contact profile for this ticket's requester and return a location
    note explaining what Atlas did and why.

    Possible outcomes:
    - Auto-assigned location (high confidence, no location set)     → note says why
    - Location confirmed (high confidence, matches profile)         → note confirms
    - Location mismatch (high confidence, different from profile)   → note flags it
    - Multiple known locations                                       → note lists them
    - Not enough history / no profile                               → None
    """
    try:
        from services.contact_profile_service import (
            get_or_create_profile,
            get_location_suggestion,
            record_location,
        )

        ticket = fetch_one(
            "SELECT requester_id, location_id FROM tickets WHERE id = %s", [ticket_id]
        )
        if not ticket or not ticket.get("requester_id"):
            return None

        user_rec = fetch_one(
            "SELECT email, phone, name FROM users WHERE id = %s",
            [ticket["requester_id"]],
        )
        if not user_rec:
            return None

        profile = get_or_create_profile(
            tenant_id=tenant_id,
            user_id=ticket["requester_id"],
            email=user_rec.get("email"),
            phone=user_rec.get("phone"),
            name=user_rec.get("name"),
        )
        if not profile:
            return None

        suggestion = get_location_suggestion(profile["id"])
        if not suggestion:
            return None

        current_loc_id = ticket.get("location_id")
        confidence = suggestion["confidence"]
        primary = suggestion["primary"]
        total = suggestion["total_tickets"]

        if confidence >= 85:
            if not current_loc_id:
                # Auto-assign the location on the ticket
                execute(
                    "UPDATE tickets SET location_id = %s WHERE id = %s",
                    [primary["location_id"], ticket_id],
                )
                record_location(profile["id"], primary["location_id"], tenant_id)
                return (
                    f"📍 **Location auto-assigned: {primary['location_name']}**\n"
                    f"{primary['ticket_count']} of {total} prior tickets from this contact "
                    f"originated from this location ({confidence}% confidence). "
                    f"Update the location field if this is incorrect."
                )
            elif current_loc_id == primary["location_id"]:
                return (
                    f"📍 **Location confirmed: {primary['location_name']}**\n"
                    f"Matches this contact's primary location "
                    f"({primary['ticket_count']} of {total} prior tickets, {confidence}% confidence)."
                )
            else:
                # Submitted from a different location than expected
                return (
                    f"📍 **Location note:** This contact's usual location is "
                    f"**{primary['location_name']}** "
                    f"({primary['ticket_count']} of {total} prior tickets), "
                    f"but this ticket was submitted for a different location. "
                    f"Please verify before closing."
                )

        elif len(suggestion["all_locations"]) >= 2:
            loc_list = " | ".join(
                f"{l['location_name']} ({l['ticket_count']})"
                for l in suggestion["all_locations"][:4]
            )
            return (
                f"📍 **Multiple locations on record** for this contact: {loc_list}\n"
                f"No single location has enough history to auto-assign. "
                f"Please confirm the correct location."
            )

    except Exception as e:
        logger.warning("_build_location_note failed for ticket %s: %s", ticket_id, e)

    return None


def _engage_worker(ticket_id: int, tenant_id: int, payload: dict | None = None):
    """Background: KB-aware analysis of new ticket with similar ticket detection.

    Args:
        payload: Optional dict with 'kb_results' from the enrich phase.
                 If provided, skips duplicate KB search.
    """
    try:
        # Idempotency guard: skip if Atlas already engaged this ticket
        existing = fetch_one(
            "SELECT id FROM atlas_engagements WHERE ticket_id = %s LIMIT 1",
            [ticket_id],
        )
        if existing:
            logger.info("Atlas already engaged ticket %s (engagement %s), skipping", ticket_id, existing["id"])
            return

        ticket = fetch_one(
            """SELECT t.subject, t.description, t.priority, t.source,
                      t.problem_category_id,
                      pc.name as category_name, loc.name as location_name,
                      u.name as requester_name
               FROM tickets t
               LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
               LEFT JOIN locations loc ON loc.id = t.location_id
               LEFT JOIN users u ON u.id = t.requester_id
               WHERE t.id = %s""",
            [ticket_id],
        )
        if not ticket:
            return

        # --- KB pre-search for relevant articles ---
        # Use results from enrich phase if available (avoids duplicate vector search)
        kb_results = (payload or {}).get("kb_results", [])
        kb_article_titles = []
        if kb_results:
            kb_article_titles = [r.get("title", "") for r in kb_results[:5] if r.get("title")]
            logger.info("Engage using %d KB results from enrich phase for ticket %s", len(kb_results), ticket_id)
        else:
            # Fallback: own KB search if enrich didn't provide results
            try:
                from services.rag_service import _pre_search_kb
                search_query = f"{ticket['subject']} {(ticket.get('description') or '')[:300]}"
                kb_results = _pre_search_kb(query=search_query, tenant_id=tenant_id)
                kb_article_titles = [r.get("title", "") for r in kb_results[:5] if r.get("title")]
            except Exception as e:
                logger.warning("KB pre-search failed for engage ticket %s: %s", ticket_id, e)

        # --- Find similar open tickets ---
        similar_tickets = _find_similar_tickets(ticket_id, tenant_id, ticket["subject"])
        similar_ticket_ids = [s["id"] for s in similar_tickets]

        # --- Buying-time message: post interim if >15s since creation ---
        created_at_str = (payload or {}).get("created_at")
        if created_at_str:
            from datetime import datetime as _dt, timezone as _tz
            try:
                _created = _dt.fromisoformat(created_at_str).replace(tzinfo=_tz.utc)
                _elapsed = (_dt.now(tz=_tz.utc) - _created).total_seconds()
                if _elapsed > 15:
                    # Only for end_user tickets — check requester role
                    _creator = fetch_one(
                        "SELECT u.role FROM users u JOIN tickets t ON t.requester_id = u.id WHERE t.id = %s",
                        [ticket_id],
                    )
                    if _creator and _creator.get("role") == "end_user":
                        _post_buying_time_message(ticket_id, tenant_id, ticket, similar_tickets)
                        logger.info("Buying-time message posted for ticket %s (%.1fs elapsed)", ticket_id, _elapsed)
            except Exception as bt_err:
                logger.warning("Buying-time check failed for ticket %s: %s", ticket_id, bt_err)

        # --- Get available categories with hierarchy paths ---
        categories = fetch_all(
            """WITH RECURSIVE cat_path AS (
                 SELECT id, name, parent_id, name::text AS path
                 FROM problem_categories
                 WHERE tenant_id = %s AND is_active = true AND parent_id IS NULL
                 UNION ALL
                 SELECT c.id, c.name, c.parent_id, cp.path || ' > ' || c.name
                 FROM problem_categories c
                 JOIN cat_path cp ON cp.id = c.parent_id
                 WHERE c.is_active = true
               )
               SELECT id, name, path FROM cat_path ORDER BY path""",
            [tenant_id],
        )
        category_list = ", ".join(f"{c['path']}" for c in categories) if categories else "none configured"

        # --- Get available teams for triage ---
        teams = fetch_all(
            "SELECT id, name, description FROM teams WHERE tenant_id = %s AND is_active = true ORDER BY name",
            [tenant_id],
        )
        team_list = ", ".join(f"{t['name']} ({t['description']})" for t in teams) if teams else ""

        # Create AI conversation linked to ticket
        conversation_id = insert_returning(
            """INSERT INTO ai_conversations (tenant_id, user_id, ticket_id, language, channel, status)
               VALUES (%s, NULL, %s, 'en', 'text', 'active') RETURNING id""",
            [tenant_id, ticket_id],
        )

        # Create engagement record
        engagement_id = insert_returning(
            """INSERT INTO atlas_engagements
                   (ticket_id, tenant_id, conversation_id, status, engagement_type,
                    kb_articles_referenced, similar_ticket_ids)
               VALUES (%s, %s, %s, 'active', 'l1', %s, %s) RETURNING id""",
            [ticket_id, tenant_id, conversation_id,
             kb_article_titles or None, similar_ticket_ids or None],
        )

        # Build context
        context_parts = [f"Subject: {ticket['subject']}"]
        if ticket.get("description"):
            context_parts.append(f"Description: {ticket['description'][:1000]}")
        if ticket.get("category_name"):
            context_parts.append(f"Category: {ticket['category_name']}")
        if ticket.get("location_name"):
            context_parts.append(f"Location: {ticket['location_name']}")
        if ticket.get("requester_name"):
            context_parts.append(f"Requester: {ticket['requester_name']}")

        # Inject KB results into context (ranked by similarity, include %)
        if kb_results:
            # Sort by similarity descending (most relevant first)
            sorted_kb = sorted(kb_results[:5], key=lambda x: x.get("similarity", 0), reverse=True)
            context_parts.append("\n--- RELEVANT KB ARTICLES (ranked by relevance) ---")
            for r in sorted_kb:
                sim = r.get("similarity", 0)
                pct = int(sim * 100) if sim else 0
                snippet = r.get("content", "")[:300]
                context_parts.append(f"[{r.get('title', 'Untitled')}] (relevance: {pct}%, module: {r.get('module', 'kb')})\n{snippet}")

        # Inject similar tickets
        if similar_tickets:
            context_parts.append("\n--- SIMILAR OPEN TICKETS ---")
            for s in similar_tickets:
                context_parts.append(
                    f"TKT-{s.get('ticket_number', s['id'])}: {s['subject']} "
                    f"(status: {s['status']}, priority: {s['priority']}, similarity: {s.get('similarity', 'N/A')})"
                )

        # Inject custom fields — inherits from ancestor categories
        try:
            from models.db import fetch_all as _fa
            _ticket_type = ticket.get("ticket_type", "support")
            _cat_id = ticket.get("problem_category_id")
            if _cat_id:
                _cf_defs = _fa(
                    """WITH RECURSIVE cat_ancestors AS (
                           SELECT id FROM problem_categories WHERE id = %s
                           UNION ALL
                           SELECT pc.parent_id
                           FROM problem_categories pc
                           JOIN cat_ancestors ca ON pc.id = ca.id
                           WHERE pc.parent_id IS NOT NULL
                       )
                       SELECT name, field_key, is_required_to_create, is_required_to_close
                       FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true
                         AND (category_id IN (SELECT id FROM cat_ancestors)
                              OR (category_id IS NULL AND %s = ANY(applies_to)))
                       ORDER BY sort_order""",
                    [_cat_id, tenant_id, _ticket_type],
                )
            else:
                _cf_defs = _fa(
                    """SELECT name, field_key, is_required_to_create, is_required_to_close
                       FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true
                         AND category_id IS NULL AND %s = ANY(applies_to)
                       ORDER BY sort_order""",
                    [tenant_id, _ticket_type],
                )
            _cf_vals_raw = _fa(
                "SELECT cf.field_key, cv.value FROM ticket_custom_field_values cv "
                "JOIN custom_field_definitions cf ON cf.id = cv.field_id WHERE cv.ticket_id = %s",
                [ticket_id],
            ) if _cf_defs else []
            _cf_vals = {r["field_key"]: r["value"] for r in _cf_vals_raw}
            if _cf_defs:
                cf_lines = []
                for fd in _cf_defs:
                    val = _cf_vals.get(fd["field_key"])
                    val_str = str(val) if val is not None else "(not set)"
                    req_parts = []
                    if fd.get("is_required_to_create"):
                        req_parts.append("req-create")
                    if fd.get("is_required_to_close"):
                        req_parts.append("req-close")
                    req = f" [{', '.join(req_parts)}]" if req_parts else ""
                    cf_lines.append(f"  {fd['name']}{req}: {val_str}")
                context_parts.append("Custom Fields:\n" + "\n".join(cf_lines))
        except Exception:
            pass

        ticket_context = "\n".join(context_parts)

        # ── Phase 1: Quick internal triage (one-shot, for agents) ──────────
        team_section = ""
        if team_list:
            team_section = (
                f"3. **Team Assignment** — Pick the BEST team from this list: [{team_list}]. "
                "Match based on each team's description and the ticket's subject matter. "
                "State the EXACT team name. If unsure, say 'Unassigned'.\n\n"
                "Be concise — three sections only. Do NOT include priority, KB articles, next steps, or other sections."
            )
        else:
            team_section = "Be concise — two sections only. Do NOT include priority, KB articles, next steps, or other sections."

        # Adjust tone for custom form tickets — Atlas can triage but may lack domain context
        custom_note = ""
        if _ticket_type == "custom":
            custom_note = (
                "\nNote: This ticket was submitted via a custom form template. "
                "The content may not align with standard support categories. "
                "Do your best to assess and categorize, but acknowledge if the form content "
                "is outside your usual domain. Focus on gathering context for the agent.\n"
            )

        system_prompt = (
            "You are Atlas, an AI support assistant performing initial triage on a new ticket. "
            f"{custom_note}"
            "Produce a structured internal analysis with EXACTLY these sections:\n"
            "1. **Issue Summary** — One-sentence description of the problem.\n"
            "2. **Category Assessment** — Pick the MOST SPECIFIC (deepest child) category from this list: "
            f"[{category_list}]. Always prefer a child category over a parent "
            "(e.g. pick 'Point of Sale (POS) > Printer Failure' not just 'Point of Sale (POS)'). "
            "State the EXACT full path as shown in the list. "
            "If no good match exists, say 'Other'. "
            "If none of the categories are even close, say 'FLAG_NEW_CATEGORY: <suggested name>'.\n\n"
            f"{team_section}"
        )

        result = complete(
            model=Config.AI_MODEL_ROUTER,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"New ticket submitted:\n\n{ticket_context}"}],
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            caller="atlas.triage",
        )

        internal_analysis = result.text.strip()

        # Build location note — auto-assign or explain based on contact history
        location_note = _build_location_note(ticket_id, tenant_id)

        # Post internal triage note (agents only)
        triage_content = f"**Atlas Analysis**\n\n{internal_analysis}"
        if location_note:
            triage_content = f"{location_note}\n\n---\n\n{triage_content}"
        insert_returning(
            """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
               VALUES (%s, NULL, %s, true, true) RETURNING id""",
            [ticket_id, triage_content],
        )

        # Auto-assign category if none set
        if categories and not ticket.get("problem_category_id"):
            _auto_assign_category(ticket_id, engagement_id, internal_analysis, categories)

        # Auto-assign team if none set and teams exist
        if teams and not ticket.get("team_id"):
            _auto_assign_team(ticket_id, engagement_id, internal_analysis, teams)

        # ── Phase 2: Full L1 RAG pipeline for client-facing response ─────
        creator = fetch_one(
            "SELECT u.role FROM users u JOIN tickets t ON t.requester_id = u.id WHERE t.id = %s",
            [ticket_id],
        )
        if creator and creator["role"] == "end_user":
            from services.rag_service import generate_response_contextual

            # Build user message — same as if they typed in Atlas tab
            user_msg = ticket["subject"]
            if ticket.get("description"):
                user_msg += f"\n\n{ticket['description']}"
            l1_messages = [{"role": "user", "content": user_msg}]

            # Build ticket context (same format Atlas tab uses)
            tc = {
                "subject": ticket["subject"],
                "description": ticket.get("description", ""),
                "category_name": ticket.get("category_name"),
                "location_name": ticket.get("location_name"),
                "requester_name": ticket.get("requester_name"),
            }

            # L1 uses Haiku for speed + cost (~$0.04/turn vs $0.20 for Sonnet)
            l1_result = generate_response_contextual(
                tenant_id=tenant_id,
                messages=l1_messages,
                language="en",
                persona="end_user",
                ticket_context=tc,
                model_override=Config.AI_MODEL_ROUTER,  # Haiku 4.5 for L1
            )

            answer = l1_result.get("answer", "").strip()
            sources = l1_result.get("sources", [])

            # Detect RAG failure — never show internal error messages to customers
            _rag_fail = (not answer
                         or l1_result.get("fallback")
                         or "unable to find" in answer.lower()
                         or "search rounds" in answer.lower())
            if _rag_fail:
                logger.warning("L1 RAG returned failure message for ticket %s, posting fallback", ticket_id)
                _post_fallback_reply(ticket_id, tenant_id)
                answer = None  # Skip normal flow

            if answer:
                # Handle escalation signal
                escalated = "<<ESCALATE>>" in answer
                answer = answer.replace("<<ESCALATE>>", "").strip()

                # Append source citations if available
                if sources:
                    source_lines = []
                    for s in sources[:5]:
                        title = s.get("title", "")
                        url = s.get("url", "")
                        module = s.get("module", "")
                        if title:
                            label = f"**{module}** — " if module else ""
                            if url:
                                source_lines.append(f"- {label}[{title}]({url})")
                            else:
                                source_lines.append(f"- {label}{title}")
                    if source_lines:
                        answer += "\n\n---\n**Sources:**\n" + "\n".join(source_lines)

                # Post as client-facing comment
                insert_returning(
                    """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                       VALUES (%s, NULL, %s, false, true) RETURNING id""",
                    [ticket_id, answer],
                )
                execute(
                    "UPDATE tickets SET first_response_at = COALESCE(first_response_at, now()) WHERE id = %s",
                    [ticket_id],
                )
                # Email notification to requester
                try:
                    from services.email_service import dispatch_ticket_emails
                    dispatch_ticket_emails(tenant_id, ticket_id, "agent_reply",
                                          comment={"content": answer, "author_name": "Atlas"})
                except Exception as email_err:
                    logger.warning("Email dispatch failed for ticket %s: %s", ticket_id, email_err)
                logger.info("Atlas L1 RAG replied to ticket %s (escalated=%s, sources=%d)", ticket_id, escalated, len(sources))

                # Track article recommendations for feedback loop
                for s in sources[:5]:
                    doc_id = s.get("document_id")
                    if doc_id:
                        try:
                            insert_returning(
                                """INSERT INTO article_recommendations
                                       (conversation_id, document_id, tenant_id, turn_number, layer)
                                   VALUES (%s, %s, %s, 1, 'l1') RETURNING id""",
                                [conversation_id, doc_id, tenant_id],
                            )
                        except Exception:
                            pass  # duplicate or missing doc — skip

                # Save L1 conversation so Atlas tab shows history
                full_msgs = l1_messages + [{"role": "assistant", "content": answer}]
                execute(
                    """UPDATE ai_conversations
                       SET messages = %s::jsonb, turn_count = 1, updated_at = now()
                       WHERE id = %s""",
                    [json.dumps(full_msgs), conversation_id],
                )
        else:
            # Agent-created ticket or no requester — save triage conversation only
            triage_msgs = [
                {"role": "user", "content": f"Triage: {ticket_context}"},
                {"role": "assistant", "content": internal_analysis},
            ]
            execute(
                """UPDATE ai_conversations
                   SET messages = %s::jsonb, turn_count = 1, updated_at = now()
                   WHERE id = %s""",
                [json.dumps(triage_msgs), conversation_id],
            )

        execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

        logger.info(
            "Atlas engaged ticket %s: triage + L1 RAG, kb_articles=%d, similar_tickets=%d",
            ticket_id, len(kb_article_titles), len(similar_tickets),
        )

    except Exception as e:
        logger.error("Atlas engage failed for ticket %s: %s", ticket_id, e)
        # Post fallback so end-user still gets an acknowledgment
        try:
            _post_fallback_reply(ticket_id, tenant_id)
        except Exception as fb_err:
            logger.error("Fallback reply also failed for ticket %s: %s", ticket_id, fb_err)


def _find_similar_tickets(ticket_id: int, tenant_id: int, subject: str) -> list[dict]:
    """Find open tickets with similar subjects using trigram similarity.

    Returns up to 5 similar tickets (similarity > 0.2), excluding self.
    Falls back to ILIKE if pg_trgm is not available.
    """
    try:
        # Try trigram similarity first (requires pg_trgm extension)
        similar = fetch_all(
            """SELECT id, ticket_number, subject, status, priority,
                      similarity(subject, %s) as similarity
               FROM tickets
               WHERE tenant_id = %s
                 AND id != %s
                 AND status IN ('open', 'pending')
                 AND similarity(subject, %s) > 0.2
               ORDER BY similarity(subject, %s) DESC
               LIMIT 5""",
            [subject, tenant_id, ticket_id, subject, subject],
        )
        return similar
    except Exception:
        # Fallback: simple keyword matching if pg_trgm not available
        try:
            # Extract key words (3+ chars) from subject for ILIKE matching
            words = [w for w in subject.split() if len(w) >= 3]
            if not words:
                return []
            # Match tickets containing any 2+ key words
            conditions = " OR ".join(["subject ILIKE %s"] * len(words))
            params = [f"%{w}%" for w in words[:5]]
            params.extend([tenant_id, ticket_id])
            similar = fetch_all(
                f"""SELECT id, ticket_number, subject, status, priority
                    FROM tickets
                    WHERE ({conditions})
                      AND tenant_id = %s
                      AND id != %s
                      AND status IN ('open', 'pending')
                    ORDER BY created_at DESC
                    LIMIT 5""",
                params,
            )
            return similar
        except Exception as e:
            logger.warning("Similar ticket search failed: %s", e)
            return []


def _auto_assign_category(ticket_id: int, engagement_id: int, analysis: str, categories: list[dict]):
    """Parse LLM analysis to extract category and auto-assign it to the ticket.

    Also applies the category's default_priority if set.
    """
    try:
        analysis_lower = analysis.lower()
        best_match = None
        best_confidence = 0.0
        best_depth = 0

        for cat in categories:
            cat_name_lower = cat["name"].lower()
            cat_path_lower = cat.get("path", cat["name"]).lower()
            depth = cat_path_lower.count(">") + 1  # 1 = root, 2 = child, etc.

            # Match full path first (higher confidence), then leaf name
            if cat_path_lower in analysis_lower:
                confidence = 0.9
            elif cat_name_lower in analysis_lower:
                confidence = 0.7
            else:
                continue
            # Boost if near Category Assessment section
            for marker in ("category", "suggest", "classify", "categorize", "best match", "assessment"):
                idx = analysis_lower.find(marker)
                if idx >= 0:
                    nearby = analysis_lower[max(0, idx - 100):idx + 300]
                    if cat_path_lower in nearby or cat_name_lower in nearby:
                        confidence = max(confidence, 0.85)
                        break
            # Prefer deeper (more specific) categories on equal confidence
            if confidence > best_confidence or (confidence == best_confidence and depth > best_depth):
                best_confidence = confidence
                best_match = cat
                best_depth = depth

        # Fallback: look for "Other" category if LLM said "Other"
        if not best_match and "other" in analysis_lower:
            for cat in categories:
                if cat["name"].lower() == "other":
                    best_match = cat
                    best_confidence = 0.5
                    break

        if best_match:
            # Store on engagement for audit trail
            execute(
                """UPDATE atlas_engagements
                   SET suggested_category_id = %s, category_confidence = %s, updated_at = now()
                   WHERE id = %s""",
                [best_match["id"], best_confidence, engagement_id],
            )
            # Auto-assign category to the ticket
            execute(
                "UPDATE tickets SET problem_category_id = %s, updated_at = now() WHERE id = %s",
                [best_match["id"], ticket_id],
            )
            logger.info("Atlas auto-assigned category '%s' (confidence %.2f) to ticket %s",
                        best_match["name"], best_confidence, ticket_id)

            # Apply category's default_priority if set
            cat_detail = fetch_one(
                "SELECT default_priority FROM problem_categories WHERE id = %s",
                [best_match["id"]],
            )
            if cat_detail and cat_detail.get("default_priority"):
                execute(
                    "UPDATE tickets SET priority = %s, updated_at = now() WHERE id = %s",
                    [cat_detail["default_priority"], ticket_id],
                )
                logger.info("Atlas applied default_priority '%s' from category '%s' to ticket %s",
                            cat_detail["default_priority"], best_match["name"], ticket_id)

        # Log if LLM flagged need for new category
        if "flag_new_category" in analysis_lower:
            logger.info("Atlas flagged new category creation for ticket %s", ticket_id)

    except Exception as e:
        logger.warning("Category auto-assign failed: %s", e)


def _auto_assign_team(ticket_id: int, engagement_id: int, analysis: str, teams: list[dict]):
    """Parse LLM analysis to extract team assignment and apply it to the ticket."""
    try:
        analysis_lower = analysis.lower()
        best_match = None
        best_confidence = 0.0

        for team in teams:
            team_name_lower = team["name"].lower()
            if team_name_lower in analysis_lower:
                # Check proximity to "Team Assignment" section
                confidence = 0.7
                for marker in ("team assignment", "team:", "assign to"):
                    idx = analysis_lower.find(marker)
                    if idx >= 0:
                        nearby = analysis_lower[max(0, idx - 50):idx + 200]
                        if team_name_lower in nearby:
                            confidence = 0.9
                            break
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = team

        if best_match:
            execute(
                """UPDATE atlas_engagements
                   SET suggested_team_id = %s, team_confidence = %s, updated_at = now()
                   WHERE id = %s""",
                [best_match["id"], best_confidence, engagement_id],
            )
            execute(
                "UPDATE tickets SET team_id = %s, updated_at = now() WHERE id = %s",
                [best_match["id"], ticket_id],
            )
            logger.info("Atlas auto-assigned team '%s' (confidence %.2f) to ticket %s",
                        best_match["name"], best_confidence, ticket_id)
    except Exception as e:
        logger.warning("Team auto-assign failed: %s", e)


# ============================================================
# 2. Set Atlas to passive when human takes over
# ============================================================

def set_passive_on_assignment(ticket_id: int):
    """When a human agent gets assigned, Atlas goes passive."""
    try:
        execute(
            """UPDATE atlas_engagements
               SET status = 'passive', human_took_over = true, human_took_over_at = now(),
                   updated_at = now()
               WHERE ticket_id = %s AND status = 'active'""",
            [ticket_id],
        )
    except Exception as e:
        logger.warning("Failed to set Atlas passive for ticket %s: %s", ticket_id, e)


# ============================================================
# 2b. Atlas follow-up when end-user replies
# ============================================================

def atlas_follow_up(ticket_id: int, tenant_id: int, user_comment: str):
    """When an end-user replies to a ticket with an active Atlas engagement,
    Atlas generates a follow-up response as a client-visible comment.

    Runs in a daemon thread (fire-and-forget).
    """
    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background follow_up", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    def _follow_up_worker():
        try:
            # Only follow up if Atlas is actively engaged on this ticket
            engagement = fetch_one(
                """SELECT ae.id, ae.conversation_id, ae.status
                   FROM atlas_engagements ae
                   WHERE ae.ticket_id = %s AND ae.status = 'active'
                   ORDER BY ae.created_at DESC LIMIT 1""",
                [ticket_id],
            )
            if not engagement:
                return

            # Get conversation history
            conv = fetch_one(
                "SELECT messages FROM ai_conversations WHERE id = %s",
                [engagement["conversation_id"]],
            )
            messages = (conv.get("messages") or []) if conv else []

            # Get ticket context for grounding
            ticket = fetch_one(
                """SELECT t.subject, t.description, pc.name as category_name
                   FROM tickets t
                   LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
                   WHERE t.id = %s""",
                [ticket_id],
            )
            if not ticket:
                return

            # Build follow-up messages
            messages.append({"role": "user", "content": user_comment})

            system = (
                "You are Atlas, a support assistant continuing a conversation about a ticket. "
                f"Ticket subject: {ticket['subject']}. "
                f"Category: {ticket.get('category_name', 'unset')}. "
                "The end-user has replied. Respond helpfully and concisely (2-4 sentences). "
                "If you can help based on your knowledge, do so. "
                "If the issue needs human attention, acknowledge and reassure them. "
                "Do NOT mention you are AI. Sign off as 'Atlas, Support Assistant'.\n\n"
                "IMPORTANT: If the user's message confirms their issue is resolved "
                "(e.g., 'that worked', 'fixed', 'thanks, all good', 'problem solved', "
                "'it's working now'), you MUST start your response with exactly [RESOLVED] "
                "on the first line, then your response on the next line. "
                "Only use [RESOLVED] when the user clearly confirms resolution, not for "
                "general thanks or greetings."
            )

            result = complete(
                model=Config.AI_MODEL_ROUTER,
                max_tokens=800,
                system=system,
                messages=messages,
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                caller="atlas.follow_up",
            )

            reply = result.text.strip()
            if not reply:
                logger.warning("Atlas follow-up returned empty for ticket %s, posting fallback", ticket_id)
                _post_fallback_reply(ticket_id, tenant_id)
                return

            # Check for resolution signal
            is_resolved = reply.startswith("[RESOLVED]")
            if is_resolved:
                reply = reply.replace("[RESOLVED]", "", 1).strip()

            # Post as client-visible comment
            insert_returning(
                """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                   VALUES (%s, NULL, %s, false, true) RETURNING id""",
                [ticket_id, reply],
            )

            # Update conversation history
            messages.append({"role": "assistant", "content": reply})
            execute(
                """UPDATE ai_conversations
                   SET messages = %s::jsonb, turn_count = turn_count + 1, updated_at = now()
                   WHERE id = %s""",
                [json.dumps(messages), engagement["conversation_id"]],
            )

            execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

            # Auto-resolve if Atlas detected resolution confirmation
            if is_resolved:
                execute(
                    "UPDATE tickets SET status = 'resolved', updated_at = now() WHERE id = %s AND status IN ('open', 'pending')",
                    [ticket_id],
                )
                execute(
                    "UPDATE atlas_engagements SET resolved_by_ai = true, status = 'closed' WHERE id = %s",
                    [engagement["id"]],
                )
                insert_returning(
                    """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                       VALUES (%s, NULL, %s, true, true) RETURNING id""",
                    [ticket_id, "This case was resolved with Atlas assistance."],
                )
                logger.info("Atlas auto-resolved ticket %s", ticket_id)

            logger.info("Atlas follow-up posted for ticket %s", ticket_id)

        except Exception as e:
            logger.error("Atlas follow-up failed for ticket %s: %s", ticket_id, e)
            try:
                _post_fallback_reply(ticket_id, tenant_id)
            except Exception as fb_err:
                logger.error("Fallback reply also failed for ticket %s: %s", ticket_id, fb_err)

    thread = threading.Thread(target=_follow_up_worker, daemon=True)
    thread.start()


# ============================================================
# 3. Close-time audit pipeline
# ============================================================

def audit_ticket_close(ticket_id: int, tenant_id: int):
    """Fire-and-forget: Run close-time audit (tags, category, score, queue)."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        return
    if not is_ticket_review_enabled(tenant_id):
        return

    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background audit", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    thread = threading.Thread(
        target=_audit_close_worker, args=(ticket_id, tenant_id), daemon=True
    )
    thread.start()


def _audit_close_worker(ticket_id: int, tenant_id: int):
    """Background: Audit a closed ticket — tags, category, quality score, KBA candidate."""
    try:
        ticket = fetch_one(
            """SELECT t.*, pc.name as category_name, loc.name as location_name,
                      u.name as requester_name
               FROM tickets t
               LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
               LEFT JOIN locations loc ON loc.id = t.location_id
               LEFT JOIN users u ON u.id = t.requester_id
               WHERE t.id = %s""",
            [ticket_id],
        )
        if not ticket:
            return

        # Get all comments for full context
        comments = fetch_all(
            """SELECT c.content, c.is_internal, c.is_ai_generated, u.name as author_name
               FROM ticket_comments c
               LEFT JOIN users u ON u.id = c.author_id
               WHERE c.ticket_id = %s ORDER BY c.created_at""",
            [ticket_id],
        )

        # Get available categories for this tenant
        categories = fetch_all(
            "SELECT id, name FROM problem_categories WHERE tenant_id = %s AND is_active = true",
            [tenant_id],
        )
        category_list = ", ".join(f"{c['id']}:{c['name']}" for c in categories) if categories else "none"

        # Build audit prompt
        comment_text = "\n".join(
            f"[{'Internal' if c['is_internal'] else 'Reply'}] {c.get('author_name', 'System')}: {c['content'][:300]}"
            for c in comments[:20]
        )

        context = (
            f"Subject: {ticket['subject']}\n"
            f"Description: {(ticket.get('description') or '')[:500]}\n"
            f"Current Tags: {', '.join(ticket.get('tags') or [])}\n"
            f"Current Category: {ticket.get('category_name') or 'None'}\n"
            f"Category ID: {ticket.get('problem_category_id') or 'None'}\n"
            f"Status: {ticket['status']}\n"
            f"Priority: {ticket['priority']}\n\n"
            f"Conversation:\n{comment_text}\n\n"
            f"Available categories: {category_list}"
        )

        result = complete(
            model=Config.AI_MODEL_ROUTER,
            max_tokens=800,
            system=(
                "You are auditing a closed support ticket. Analyze the full conversation and provide:\n"
                "1. suggested_tags: 3-5 lowercase tags that best classify this ticket\n"
                "2. suggested_category_id: the ID of the best matching category from the available list (or null)\n"
                "3. category_confidence: 0.0-1.0 how confident you are in the category\n"
                "4. resolution_score: 0.0-1.0 quality of resolution (was root cause addressed?)\n"
                "5. resolution_notes: brief explanation of the score\n"
                "6. is_kba_candidate: true if this resolution is novel and would make a good KB article\n"
                "7. kba_draft: if is_kba_candidate, draft a brief KB article title and summary\n\n"
                "Return ONLY valid JSON, no markdown fences."
            ),
            messages=[{"role": "user", "content": context}],
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            caller="atlas.audit",
        )

        raw = result.text
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        audit = json.loads(raw)

        # ── Coerce all LLM output fields to expected types ────────
        # Claude sometimes returns dicts/lists where strings are expected.
        def _str(v, fallback=""):
            """Coerce value to string. Dicts/lists get JSON-serialized."""
            if v is None:
                return fallback
            if isinstance(v, str):
                return v
            if isinstance(v, (dict, list)):
                return json.dumps(v)
            return str(v)

        def _float(v, fallback=0.0):
            try:
                return float(v) if v is not None else fallback
            except (ValueError, TypeError):
                return fallback

        # Normalize fields before use
        audit["resolution_notes"] = _str(audit.get("resolution_notes"))
        audit["kba_draft"] = _str(audit.get("kba_draft"))
        audit["category_confidence"] = _float(audit.get("category_confidence"))
        audit["resolution_score"] = _float(audit.get("resolution_score"))

        # suggested_category_id: extract int from possible dict like {"id": 5, "name": "..."}
        raw_cat_id = audit.get("suggested_category_id")
        if isinstance(raw_cat_id, dict):
            raw_cat_id = raw_cat_id.get("id")
        try:
            audit["suggested_category_id"] = int(raw_cat_id) if raw_cat_id is not None else None
        except (ValueError, TypeError):
            audit["suggested_category_id"] = None

        # suggested_tags: flatten dicts like [{"tag": "x", "confidence": 0.9}] to ["x"]
        raw_tags = audit.get("suggested_tags", [])
        if isinstance(raw_tags, list):
            flat_tags = []
            for t in raw_tags:
                if isinstance(t, str):
                    flat_tags.append(t)
                elif isinstance(t, dict):
                    flat_tags.append(str(t.get("tag") or t.get("name") or t.get("value") or next(iter(t.values()), "")))
            audit["suggested_tags"] = flat_tags
        else:
            audit["suggested_tags"] = []

        # Determine queue type
        engagement = fetch_one(
            "SELECT resolved_by_ai FROM atlas_engagements WHERE ticket_id = %s ORDER BY created_at DESC LIMIT 1",
            [ticket_id],
        )
        was_auto_resolved = engagement and engagement.get("resolved_by_ai")

        category_confidence = audit["category_confidence"]

        if was_auto_resolved:
            queue_type = "auto_resolved"
        elif audit.get("is_kba_candidate"):
            queue_type = "kba_candidate"
        elif category_confidence < 0.6:
            queue_type = "low_confidence"
        else:
            queue_type = "human_resolved"

        # Get auto-close setting from tenant
        tenant = fetch_one("SELECT settings FROM tenants WHERE id = %s", [tenant_id])
        settings = (tenant or {}).get("settings") or {}
        auto_close_days = settings.get("ai_audit_auto_close_days", 7)

        # Apply tags if confident
        suggested_tags = audit["suggested_tags"]
        if suggested_tags:
            for tag in suggested_tags[:5]:
                if tag and tag.strip():
                    tag_clean = tag.strip().lower()[:50]
                    execute(
                        "UPDATE tickets SET tags = array_append(tags, %s), updated_at = now() WHERE id = %s AND NOT (%s = ANY(tags))",
                        [tag_clean, ticket_id, tag_clean],
                    )

        # Validate/fix category if high confidence and different
        suggested_cat_id = audit["suggested_category_id"]
        if suggested_cat_id and category_confidence >= 0.8:
            if str(suggested_cat_id) != str(ticket.get("problem_category_id")):
                try:
                    execute(
                        "UPDATE tickets SET problem_category_id = %s, updated_at = now() WHERE id = %s",
                        [suggested_cat_id, ticket_id],
                    )
                    # Leave agent note about the correction
                    cat_name = next((c["name"] for c in categories if c["id"] == suggested_cat_id), "Unknown")
                    insert_returning(
                        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                           VALUES (%s, NULL, %s, true, true) RETURNING id""",
                        [ticket_id, f"**Atlas Audit** — Category updated to **{cat_name}** (confidence: {category_confidence:.0%})"],
                    )
                except (ValueError, TypeError):
                    suggested_cat_id = None

        # Insert into audit queue
        clean_tags = [t.strip().lower()[:50] for t in suggested_tags if t and t.strip()]
        resolution_score = audit["resolution_score"]

        audit_item = insert_returning(
            """INSERT INTO ticket_audit_queue
               (ticket_id, tenant_id, queue_type, ai_suggested_tags, ai_suggested_category_id,
                ai_category_confidence, resolution_score, resolution_notes, kba_draft,
                auto_close_at)
               VALUES (%s, %s, %s, %s::text[], %s, %s, %s, %s, %s,
                       now() + interval '%s days')
               RETURNING id""",
            [
                ticket_id, tenant_id, queue_type,
                clean_tags,
                suggested_cat_id,
                category_confidence,
                resolution_score,
                audit["resolution_notes"],
                audit["kba_draft"],
                auto_close_days,
            ],
        )

        # Auto-approve/dismiss based on tenant thresholds
        auto_approve_thresh = settings.get("ai_audit_auto_approve_threshold", 80)
        auto_dismiss_thresh = settings.get("ai_audit_auto_dismiss_threshold", 0)
        score_pct = resolution_score * 100

        if auto_approve_thresh > 0 and score_pct >= auto_approve_thresh:
            execute(
                "UPDATE ticket_audit_queue SET status = 'auto_approved', reviewed_at = now() WHERE id = %s",
                [audit_item],
            )
            logger.info("Auto-approved audit item %s (score %.0f%% >= %s%%)",
                        audit_item, score_pct, auto_approve_thresh)
        elif auto_dismiss_thresh > 0 and score_pct <= auto_dismiss_thresh:
            execute(
                "UPDATE ticket_audit_queue SET status = 'auto_dismissed', reviewed_at = now() WHERE id = %s",
                [audit_item],
            )
            logger.info("Auto-dismissed audit item %s (score %.0f%% <= %s%%)",
                        audit_item, score_pct, auto_dismiss_thresh)

        # Close Atlas engagement
        execute(
            """UPDATE atlas_engagements SET status = 'closed', updated_at = now()
               WHERE ticket_id = %s AND status IN ('active', 'passive')""",
            [ticket_id],
        )

        # ── 8D: FCR/ROI tracking ──────────────────────────────
        _compute_resolution_type(ticket_id, tenant_id, comments, engagement)

        # Contact profile: reinforce location learning from final closed state
        if ticket.get("requester_id") and ticket.get("location_id"):
            try:
                from services.contact_profile_service import get_or_create_profile, record_location
                _cp_user = fetch_one(
                    "SELECT email, phone, name FROM users WHERE id = %s",
                    [ticket["requester_id"]],
                )
                if _cp_user:
                    _cp = get_or_create_profile(
                        tenant_id=tenant_id,
                        user_id=ticket["requester_id"],
                        email=_cp_user.get("email"),
                        phone=_cp_user.get("phone"),
                        name=_cp_user.get("name"),
                    )
                    if _cp:
                        record_location(_cp["id"], ticket["location_id"], tenant_id)
            except Exception as _cpe:
                logger.warning("Contact profile close-time update failed for ticket %s: %s", ticket_id, _cpe)

        logger.info("Atlas audited ticket %s → queue_type=%s, score=%.2f",
                     ticket_id, queue_type, resolution_score)

    except json.JSONDecodeError as e:
        logger.warning("Atlas audit JSON parse failed for ticket %s: %s", ticket_id, e)
    except Exception as e:
        logger.error("Atlas audit failed for ticket %s: %s", ticket_id, e)


def _compute_resolution_type(ticket_id: int, tenant_id: int, comments: list, engagement: dict | None):
    """Determine how this ticket was resolved and update ticket_metrics.

    resolution_type: ai_l1 | ai_l2 | human | hybrid
    ai_turns_before_resolve: count of AI conversation messages
    was_escalated_from_ai: true if L1→L2 or L1→human escalation occurred
    """
    try:
        has_ai_comments = any(c.get("is_ai_generated") for c in comments)
        has_human_replies = any(
            not c.get("is_internal") and not c.get("is_ai_generated")
            for c in comments
            if c.get("author_name") and c["author_name"] != "System"
        )

        # Check for L2 escalation
        l2_engagement = fetch_one(
            "SELECT id FROM atlas_engagements WHERE ticket_id = %s AND engagement_type = 'l2'",
            [ticket_id],
        )
        was_escalated = l2_engagement is not None

        # Count AI conversation turns
        ai_turns = 0
        ai_conv = fetch_one(
            "SELECT messages FROM ai_conversations WHERE ticket_id = %s ORDER BY updated_at DESC LIMIT 1",
            [ticket_id],
        )
        if ai_conv and ai_conv.get("messages"):
            msgs = ai_conv["messages"] if isinstance(ai_conv["messages"], list) else []
            ai_turns = len([m for m in msgs if isinstance(m, dict) and m.get("role") == "assistant"])

        resolved_by_ai = engagement and engagement.get("resolved_by_ai")

        if resolved_by_ai and not has_human_replies:
            resolution_type = "ai_l2" if was_escalated else "ai_l1"
        elif has_ai_comments and has_human_replies:
            resolution_type = "hybrid"
        else:
            resolution_type = "human"

        execute(
            """UPDATE ticket_metrics SET
                   resolution_type = %s,
                   ai_turns_before_resolve = %s,
                   was_escalated_from_ai = %s,
                   updated_at = now()
               WHERE ticket_id = %s""",
            [resolution_type, ai_turns, was_escalated, ticket_id],
        )

        logger.info("Resolution type for ticket %s: %s (ai_turns=%d, escalated=%s)",
                     ticket_id, resolution_type, ai_turns, was_escalated)

    except Exception as e:
        logger.error("Resolution type computation failed for ticket %s: %s", ticket_id, e)


# ============================================================
# 4. Calculate customer effort score
# ============================================================

def calculate_effort_score(ticket_id: int, tenant_id: int):
    """Fire-and-forget: Calculate effort score on ticket close."""
    thread = threading.Thread(
        target=_effort_worker, args=(ticket_id, tenant_id), daemon=True
    )
    thread.start()


def _effort_worker(ticket_id: int, tenant_id: int):
    """Background: Calculate customer effort score from ticket metrics.

    Factors (1=easy, 5=painful):
      - Requester reply count (more back-and-forth = higher effort)
      - Total reply volume
      - Escalation count (L2 escalations)
      - Resolution time vs SLA target (slower = higher effort)
      - Reassignment count (each handoff adds friction)
    """
    try:
        ticket = fetch_one(
            "SELECT created_at, resolved_at, closed_at, requester_id, priority, assignee_id FROM tickets WHERE id = %s",
            [ticket_id],
        )
        if not ticket:
            return

        comments = fetch_all(
            "SELECT author_id, is_internal FROM ticket_comments WHERE ticket_id = %s",
            [ticket_id],
        )

        requester_id = ticket.get("requester_id")
        total_replies = len([c for c in comments if not c["is_internal"]])
        requester_replies = len([c for c in comments if c.get("author_id") == requester_id and not c["is_internal"]])
        agent_replies = total_replies - requester_replies

        # Check for escalations
        escalation_count = 0
        engagement = fetch_one(
            "SELECT id FROM atlas_engagements WHERE ticket_id = %s AND engagement_type = 'l2'",
            [ticket_id],
        )
        if engagement:
            escalation_count += 1

        # Base effort from reply volume
        effort = 1.0
        if requester_replies >= 2:
            effort += 0.5
        if requester_replies >= 4:
            effort += 1.0
        if requester_replies >= 6:
            effort += 1.0
        if total_replies >= 8:
            effort += 0.5
        if escalation_count > 0:
            effort += 0.5

        # 8F: Resolution time factor — compare actual hours to SLA target
        end_time = ticket.get("resolved_at") or ticket.get("closed_at")
        if end_time and ticket.get("created_at"):
            actual_hours = (end_time - ticket["created_at"]).total_seconds() / 3600
            # SLA resolution targets by priority (hours)
            sla_targets = {"p1": 4, "p2": 8, "p3": 24, "p4": 72}
            target = sla_targets.get(ticket.get("priority", "p3"), 24)
            if target > 0:
                ratio = actual_hours / target
                if ratio > 2.0:
                    effort += 1.0  # Took more than 2x SLA target
                elif ratio > 1.0:
                    effort += 0.5  # Took longer than SLA target

        # 8F: Reassignment factor — count distinct assignee changes from comments
        reassignment_count = 0
        reassign_notes = fetch_all(
            """SELECT id FROM ticket_comments
               WHERE ticket_id = %s AND is_internal = true AND is_ai_generated = true
                     AND content LIKE '%%[Atlas Handoff Summary]%%'""",
            [ticket_id],
        )
        reassignment_count = len(reassign_notes)
        effort += min(reassignment_count * 0.5, 1.5)  # Cap at +1.5

        effort = min(5.0, effort)
        high_effort = effort >= 4.0

        resolved_first_contact = (agent_replies <= 1 and requester_replies <= 1)

        # Upsert metrics
        execute(
            """INSERT INTO ticket_metrics
                   (ticket_id, tenant_id, effort_score, reply_count, requester_replies,
                    agent_replies, resolved_first_contact, escalation_count, high_effort)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ticket_id) DO UPDATE SET
                   effort_score = EXCLUDED.effort_score,
                   reply_count = EXCLUDED.reply_count,
                   requester_replies = EXCLUDED.requester_replies,
                   agent_replies = EXCLUDED.agent_replies,
                   resolved_first_contact = EXCLUDED.resolved_first_contact,
                   escalation_count = EXCLUDED.escalation_count,
                   high_effort = EXCLUDED.high_effort,
                   updated_at = now()""",
            [ticket_id, tenant_id, effort, total_replies, requester_replies,
             agent_replies, resolved_first_contact, escalation_count, high_effort],
        )

        logger.info("Effort score for ticket %s: %.1f (FCR: %s, high_effort: %s)",
                     ticket_id, effort, resolved_first_contact, high_effort)

    except Exception as e:
        logger.error("Effort score failed for ticket %s: %s", ticket_id, e)


# ============================================================
# 5. Smart auto-routing suggestion
# ============================================================

def suggest_routing(ticket_id: int, tenant_id: int):
    """Fire-and-forget: Suggest which agent should handle this ticket."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        return

    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background routing", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    thread = threading.Thread(
        target=_routing_worker, args=(ticket_id, tenant_id), daemon=True
    )
    thread.start()


def _routing_worker(ticket_id: int, tenant_id: int):
    """Background: Analyze ticket and suggest best agent based on past performance.

    Enhanced scoring factors:
    - Category match count (weighted highest)
    - Recency (30-day matches weighted more than 90-day)
    - Current workload (fewer open tickets = preferred)
    - Resolution quality (avg effort score — lower is better)
    """
    try:
        ticket = fetch_one(
            "SELECT subject, description, priority, problem_category_id FROM tickets WHERE id = %s",
            [ticket_id],
        )
        if not ticket:
            return

        cat_id = ticket.get("problem_category_id")

        # Find agents with comprehensive stats
        agents = fetch_all(
            """SELECT u.id, u.name,
                      count(t.id) FILTER (WHERE t.status IN ('resolved', 'closed_not_resolved')
                          AND t.created_at > now() - interval '90 days') as total_resolved,
                      count(t.id) FILTER (WHERE t.problem_category_id = %s
                          AND t.status IN ('resolved', 'closed_not_resolved')
                          AND t.created_at > now() - interval '90 days') as category_matches,
                      count(t.id) FILTER (WHERE t.problem_category_id = %s
                          AND t.status IN ('resolved', 'closed_not_resolved')
                          AND t.created_at > now() - interval '30 days') as recent_category_matches,
                      count(t.id) FILTER (WHERE t.status IN ('open', 'pending')) as open_tickets,
                      coalesce(avg(tm.effort_score) FILTER (WHERE tm.effort_score IS NOT NULL), 3.0) as avg_effort
               FROM users u
               LEFT JOIN tickets t ON t.assignee_id = u.id AND t.tenant_id = %s
               LEFT JOIN ticket_metrics tm ON tm.ticket_id = t.id
               WHERE u.tenant_id = %s
                 AND u.role IN ('agent', 'tenant_admin')
                 AND u.is_active = true
               GROUP BY u.id, u.name
               ORDER BY category_matches DESC, total_resolved DESC
               LIMIT 10""",
            [cat_id, cat_id, tenant_id, tenant_id],
        )

        if not agents:
            return

        # Score each agent
        scored = []
        for agent in agents:
            base = 0.3
            category_factor = min(0.3, agent["category_matches"] * 0.06)
            recency_factor = min(0.15, agent["recent_category_matches"] * 0.05)
            # Quality factor: lower effort = better (effort 1-5, invert to 0-1 scale)
            quality_factor = min(0.15, (5 - float(agent["avg_effort"])) / 5 * 0.15)
            workload_penalty = min(0.1, agent["open_tickets"] * 0.02)

            score = base + category_factor + recency_factor + quality_factor - workload_penalty
            score = max(0.1, min(0.95, score))
            scored.append({**agent, "confidence": round(score, 3)})

        scored.sort(key=lambda a: a["confidence"], reverse=True)
        best = scored[0]

        # Build reason string
        if best["category_matches"] > 0:
            reason = f"{best['category_matches']} tickets resolved in this category (last 90d)"
            if best["recent_category_matches"] > 0:
                reason += f", {best['recent_category_matches']} in last 30d"
        elif best["total_resolved"] > 0:
            reason = f"Most active resolver ({best['total_resolved']} tickets in 90d)"
        else:
            reason = "Available agent"

        if best["open_tickets"] > 0:
            reason += f" — {best['open_tickets']} currently open"

        execute(
            """INSERT INTO ticket_metrics (ticket_id, tenant_id, suggested_assignee_id, routing_confidence, routing_reason)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (ticket_id) DO UPDATE SET
                   suggested_assignee_id = EXCLUDED.suggested_assignee_id,
                   routing_confidence = EXCLUDED.routing_confidence,
                   routing_reason = EXCLUDED.routing_reason,
                   updated_at = now()""",
            [ticket_id, tenant_id, best["id"], best["confidence"], reason],
        )

        logger.info("Routing suggestion for ticket %s: agent %s (%.0f%% confidence)",
                     ticket_id, best["name"], best["confidence"] * 100)

    except Exception as e:
        logger.error("Routing suggestion failed for ticket %s: %s", ticket_id, e)


# ============================================================
# 6. Agent handoff summarization
# ============================================================

def generate_handoff_summary(ticket_id: int, tenant_id: int):
    """Fire-and-forget: Generate a 3-line handoff brief when a ticket is reassigned.

    Posted as an internal note so the new assignee doesn't have to read
    15 comments to understand the context.
    """
    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background handoff", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    def _worker():
        try:
            _handoff_worker(ticket_id, tenant_id)
        except Exception:
            logger.exception("Handoff summary failed for ticket %s", ticket_id)

    threading.Thread(target=_worker, daemon=True).start()


def _handoff_worker(ticket_id: int, tenant_id: int):
    """Background: build handoff summary from ticket + comments, post as internal note."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        return

    ticket = fetch_one(
        """SELECT t.subject, t.description, t.status, t.priority,
                  pc.name as category_name, loc.name as location_name,
                  u.name as requester_name
           FROM tickets t
           LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
           LEFT JOIN locations loc ON loc.id = t.location_id
           LEFT JOIN users u ON u.id = t.requester_id
           WHERE t.id = %s""",
        [ticket_id],
    )
    if not ticket:
        return

    comments = fetch_all(
        """SELECT tc.content, tc.is_internal, tc.is_ai_generated,
                  u.name as author_name, tc.created_at
           FROM ticket_comments tc
           LEFT JOIN users u ON u.id = tc.author_id
           WHERE tc.ticket_id = %s
           ORDER BY tc.created_at""",
        [ticket_id],
    )

    comment_text = ""
    for c in comments[-20:]:  # last 20 comments max
        prefix = "[Internal]" if c["is_internal"] else "[Reply]"
        if c["is_ai_generated"]:
            prefix = "[Atlas]"
        comment_text += f"{prefix} {c.get('author_name', 'System')}: {c['content'][:200]}\n"

    prompt = f"""Summarize this ticket handoff in exactly 3 lines for the new assignee.

TICKET: {ticket['subject']}
Status: {ticket['status']} | Priority: {ticket['priority']}
Category: {ticket.get('category_name', 'N/A')} | Location: {ticket.get('location_name', 'N/A')}
Requester: {ticket.get('requester_name', 'Unknown')}

Description: {(ticket.get('description') or '')[:500]}

CONVERSATION HISTORY:
{comment_text[:2000]}

OUTPUT (exactly 3 lines, no labels):
Line 1: What has been tried so far
Line 2: Customer sentiment and urgency level
Line 3: Most likely root cause or recommended next step"""

    result = complete(
        messages=[{"role": "user", "content": prompt}],
        model=Config.AI_MODEL_ROUTER,
        max_tokens=200,
        temperature=0.3,
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        caller="atlas.handoff",
    )

    summary = result.text.strip()
    if not summary:
        return

    # Post as internal note
    insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, NULL, %s, true, true) RETURNING id""",
        [ticket_id, f"[Atlas Handoff Summary]\n{summary}"],
    )
    logger.info("Handoff summary posted for ticket %s", ticket_id)


# ============================================================
# 7. Knowledge gap detection
# ============================================================

def detect_knowledge_gaps(tenant_id: int):
    """Analyze recent tickets for topics not covered by KB articles.
    Should be called periodically (e.g., daily via cron scheduler).
    """
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        return

    # Billing cap gate — skip silently if tenant cannot use AI
    try:
        from services.billing_service import check_ai_gate, ApiCapError
        check_ai_gate(tenant_id)
    except ApiCapError:
        logger.info("AI cap reached for tenant %s — skipping background gap_detection", tenant_id)
        return
    except Exception:
        pass  # Don't let billing errors break workers

    try:
        # Get recent resolved tickets that had no matching articles
        tickets = fetch_all(
            """SELECT t.id, t.subject, t.description, t.tags
               FROM tickets t
               LEFT JOIN article_recommendations ar ON ar.conversation_id IN (
                   SELECT id FROM ai_conversations WHERE ticket_id = t.id
               )
               WHERE t.tenant_id = %s
                 AND t.status IN ('resolved', 'closed_not_resolved')
                 AND t.created_at > now() - interval '30 days'
                 AND ar.id IS NULL
               ORDER BY t.created_at DESC
               LIMIT 50""",
            [tenant_id],
        )

        if len(tickets) < 3:
            return  # Not enough data to detect gaps

        # Build ticket summary for LLM
        ticket_summaries = "\n".join(
            f"- [{t['id']}] {t['subject']}: {(t.get('description') or '')[:100]}"
            for t in tickets
        )

        result = complete(
            model=Config.AI_MODEL_ROUTER,
            max_tokens=600,
            system=(
                "Analyze these support tickets that had no matching KB article. "
                "Identify 1-5 recurring topics or themes that should have KB articles. "
                "Return ONLY a JSON array of objects: "
                "[{\"topic\": \"short topic\", \"suggested_title\": \"Article Title\", \"ticket_ids\": [1,2,3]}]"
            ),
            messages=[{"role": "user", "content": ticket_summaries}],
            tenant_id=tenant_id,
            caller="atlas.gaps",
        )

        raw = result.text
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]

        gaps = json.loads(raw.strip())

        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            topic = gap.get("topic", "").strip()
            if not topic:
                continue

            # Check if gap already exists
            existing = fetch_one(
                "SELECT id, ticket_count FROM knowledge_gaps WHERE tenant_id = %s AND topic = %s AND status = 'detected'",
                [tenant_id, topic],
            )

            ticket_ids = [int(tid) for tid in gap.get("ticket_ids", []) if isinstance(tid, (int, str))]

            if existing:
                execute(
                    """UPDATE knowledge_gaps
                       SET ticket_count = ticket_count + %s,
                           sample_tickets = (
                               SELECT array_agg(DISTINCT t) FROM unnest(sample_tickets || %s::int[]) t
                           ),
                           updated_at = now()
                       WHERE id = %s""",
                    [len(ticket_ids), ticket_ids, existing["id"]],
                )
            else:
                insert_returning(
                    """INSERT INTO knowledge_gaps (tenant_id, topic, ticket_count, sample_tickets, suggested_title)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    [tenant_id, topic, len(ticket_ids), ticket_ids, gap.get("suggested_title", "")],
                )

        logger.info("Knowledge gap detection for tenant %s found %d gaps", tenant_id, len(gaps))

    except json.JSONDecodeError as e:
        logger.warning("Knowledge gap detection JSON parse failed: %s", e)
    except Exception as e:
        logger.error("Knowledge gap detection failed for tenant %s: %s", tenant_id, e)
