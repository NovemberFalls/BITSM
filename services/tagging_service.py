"""LLM auto-tagging service: suggests tags for tickets using Claude Haiku."""

import json
import logging
import threading

from config import Config
from models.db import fetch_one, insert_returning
from services.llm_provider import complete

logger = logging.getLogger(__name__)


def auto_tag_ticket(ticket_id: int):
    """Fire-and-forget: spawn background thread to suggest tags for a ticket."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        logger.debug("No LLM API key set — skipping auto-tag for ticket %s", ticket_id)
        return

    thread = threading.Thread(target=_tag_worker, args=(ticket_id,), daemon=True)
    thread.start()


def _tag_worker(ticket_id: int):
    """Background worker: call LLM to suggest tags."""
    try:
        ticket = fetch_one(
            """SELECT t.subject, t.description, t.tenant_id, t.ticket_type,
                      pc.name as category_name
               FROM tickets t
               LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
               WHERE t.id = %s""",
            [ticket_id],
        )
        if not ticket:
            return

        tenant_id   = ticket.get("tenant_id")

        # Billing cap gate — skip silently if tenant cannot use AI
        try:
            from services.billing_service import check_ai_gate, ApiCapError
            check_ai_gate(tenant_id)
        except ApiCapError:
            logger.info("AI cap reached for tenant %s — skipping background tagging", tenant_id)
            return
        except Exception:
            pass  # Don't let billing errors break workers

        subject     = ticket.get("subject", "")
        description = ticket.get("description", "") or ""
        category    = ticket.get("category_name", "") or ""
        ticket_type = ticket.get("ticket_type", "support")
        is_dev_item = ticket_type in ("task", "bug", "feature")

        prompt_parts = [f"Subject: {subject}"]
        if description:
            prompt_parts.append(f"Description: {description[:500]}")
        if category:
            prompt_parts.append(f"Category: {category}")

        ticket_text = "\n".join(prompt_parts)

        if is_dev_item:
            tag_system = (
                "You are a dev work item tagger. Given a task/bug/feature title and description, "
                "suggest 2-5 short, lowercase tags about the technology, domain, or type of work. "
                'Return ONLY a JSON array of strings, nothing else. '
                'Example: ["python", "api", "refactor", "backend"]'
            )
        else:
            tag_system = (
                "You are a support ticket tagger. Given a ticket's subject, description, and category, "
                "suggest 2-5 short, lowercase tags that classify the issue. "
                'Return ONLY a JSON array of strings, nothing else. '
                'Example: ["hardware", "kiosk", "image-update"]'
            )

        tags = None
        for attempt in range(2):
            result = complete(
                model=Config.AI_MODEL_ROUTER,
                max_tokens=200,
                system=tag_system,
                messages=[{"role": "user", "content": ticket_text}],
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                caller="tagging",
            )

            raw = result.text
            if not raw or not raw.strip():
                logger.warning("LLM returned empty response for ticket %s (attempt %d, stop=%s)",
                               ticket_id, attempt + 1, result.stop_reason)
                continue

            # Strip markdown fences if the LLM wrapped the JSON
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()

            try:
                tags = json.loads(cleaned)
                break
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse LLM tag response for ticket %s (attempt %d): %s — raw: %s",
                               ticket_id, attempt + 1, e, raw[:200])

        if tags is None:
            return

        if not isinstance(tags, list):
            logger.warning("LLM returned non-list for ticket %s: %s", ticket_id, raw)
            return

        clean_tags = []
        for tag in tags[:5]:
            if isinstance(tag, str) and tag.strip():
                insert_returning(
                    """INSERT INTO tag_suggestions (ticket_id, tag, confidence)
                       VALUES (%s, %s, %s) RETURNING id""",
                    [ticket_id, tag.strip().lower()[:50], None],
                )
                clean_tags.append(tag.strip().lower())

        logger.info("Auto-tagged ticket %s with %d suggestions", ticket_id, len(clean_tags))
        return clean_tags

    except Exception as e:
        logger.error("Auto-tag failed for ticket %s: %s", ticket_id, e)
