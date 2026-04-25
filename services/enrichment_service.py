"""Ticket enrichment service: AI-powered KB matching and classification."""

import json
import logging
import threading

from config import Config
from models.db import fetch_one, fetch_all, insert_returning, execute
from services.llm_provider import complete

logger = logging.getLogger(__name__)


def enrich_ticket(ticket_id: int):
    """Fire-and-forget: spawn background thread to enrich a ticket with KB context."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        logger.debug("No LLM API key set — skipping enrichment for ticket %s", ticket_id)
        return
    if not Config.OPENAI_API_KEY and not Config.VOYAGE_API_KEY:
        logger.debug("No embedding API key set — skipping enrichment for ticket %s", ticket_id)
        return

    thread = threading.Thread(target=_enrichment_worker, args=(ticket_id,), daemon=True)
    thread.start()


def _enrichment_worker(ticket_id: int):
    """Background worker: find matching KBAs and classify the ticket."""
    try:
        # Fetch ticket with context
        ticket = fetch_one(
            """SELECT t.id, t.subject, t.description, t.tenant_id, t.tags,
                      t.problem_category_id, pc.name as category_name
               FROM tickets t
               LEFT JOIN problem_categories pc ON pc.id = t.problem_category_id
               WHERE t.id = %s""",
            [ticket_id],
        )
        if not ticket:
            return []

        tenant_id = ticket["tenant_id"]
        if not tenant_id:
            return []

        # Billing cap gate — skip silently if tenant cannot use AI
        try:
            from services.billing_service import check_ai_gate, ApiCapError
            check_ai_gate(tenant_id)
        except ApiCapError:
            logger.info("AI cap reached for tenant %s — skipping background enrichment", tenant_id)
            return []
        except Exception:
            pass  # Don't let billing errors break workers

        # Check tenant has enabled modules
        module_ids = [
            r["module_id"] for r in
            fetch_all("SELECT module_id FROM tenant_modules WHERE tenant_id = %s", [tenant_id])
        ]
        if not module_ids:
            logger.debug("No modules enabled for tenant %s — skipping enrichment", tenant_id)
            return []

        # Check if we have chunks to search
        chunk_count = fetch_one(
            "SELECT count(*) as cnt FROM document_chunks WHERE module_id = ANY(%s)",
            [module_ids],
        )
        if not chunk_count or chunk_count["cnt"] == 0:
            return []

        # Build ticket context for the search
        subject = ticket.get("subject", "")
        description = (ticket.get("description") or "")[:1000]
        category = ticket.get("category_name") or ""
        search_query = f"{subject} {description[:200]}"

        # Vector search for relevant KB articles
        from services.embedding_service import embed_single_with_usage
        query_embedding, embed_tokens = embed_single_with_usage(search_query, tenant_id=tenant_id)

        # Record embedding token usage for billing
        if embed_tokens and tenant_id:
            try:
                from config import Config as _Cfg
                from services.llm_provider import _record_usage
                _record_usage(tenant_id, ticket_id, _Cfg.EMBEDDING_PROVIDER, _Cfg.EMBEDDING_MODEL,
                              "enrichment.embed", embed_tokens, 0)
                from services import billing_service
                billing_service.record_usage(tenant_id, _Cfg.EMBEDDING_MODEL, "enrichment.embed", embed_tokens, 0)
            except Exception:
                pass

        kb_results = fetch_all(
            """SELECT dc.document_id, dc.content, dc.metadata,
                      d.title, d.source_url,
                      1 - (dc.embedding <=> %s::vector) as similarity
               FROM document_chunks dc
               JOIN documents d ON d.id = dc.document_id
               WHERE dc.module_id = ANY(%s)
               ORDER BY dc.embedding <=> %s::vector
               LIMIT 5""",
            [str(query_embedding), module_ids, str(query_embedding)],
        )

        if not kb_results:
            return []

        # Filter to only reasonably relevant results
        relevant = [r for r in kb_results if r.get("similarity", 0) > 0.3]
        if not relevant:
            return []

        # Use LLM to analyze and summarize
        kb_context = "\n\n---\n\n".join([
            f"**{r['title']}** (similarity: {r['similarity']:.3f})\n{r['content'][:500]}"
            for r in relevant[:3]
        ])

        analysis_prompt = f"""Analyze this support ticket and the relevant knowledge base articles found.

TICKET:
Subject: {subject}
Description: {description}
Category: {category}
Current tags: {', '.join(ticket.get('tags') or [])}

RELEVANT KNOWLEDGE BASE ARTICLES:
{kb_context}

Respond with a JSON object containing:
1. "summary" (string): A 1-2 sentence analysis of the issue and which KB articles are most relevant
2. "suggested_tags" (array of strings): 0-3 additional tags not already on the ticket
3. "kb_articles" (array of objects): The most relevant articles with "title", "source_url", and "relevance" (high/medium/low)
4. "confidence" (number 0-1): Your confidence that the KB articles address the ticket's issue

Return ONLY the JSON object, nothing else."""

        result = complete(
            model=Config.AI_MODEL_ROUTER,
            max_tokens=500,
            messages=[{"role": "user", "content": analysis_prompt}],
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            caller="enrichment",
        )

        raw = result.text
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        analysis = json.loads(raw)

        # Post internal comment with analysis
        summary = analysis.get("summary", "")
        articles = analysis.get("kb_articles", [])
        confidence = analysis.get("confidence", 0)

        if summary:
            comment_parts = [f"**AI Analysis** (confidence: {confidence:.0%}):", summary]

            if articles:
                comment_parts.append("\n**Related Knowledge Base Articles:**")
                for article in articles[:3]:
                    title = article.get("title", "")
                    url = article.get("source_url", "")
                    relevance = article.get("relevance", "")
                    if url:
                        comment_parts.append(f"- [{title}]({url}) ({relevance})")
                    else:
                        comment_parts.append(f"- {title} ({relevance})")

            comment_content = "\n".join(comment_parts)

            # Insert as internal AI-generated comment
            insert_returning(
                """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
                   VALUES (%s, NULL, %s, true, true) RETURNING id""",
                [ticket_id, comment_content],
            )

        # Add suggested tags
        suggested_tags = analysis.get("suggested_tags", [])
        existing_tags = ticket.get("tags") or []
        for tag in suggested_tags[:3]:
            if isinstance(tag, str) and tag.strip() and tag.strip().lower() not in [t.lower() for t in existing_tags]:
                insert_returning(
                    """INSERT INTO tag_suggestions (ticket_id, tag, confidence)
                       VALUES (%s, %s, %s) RETURNING id""",
                    [ticket_id, tag.strip().lower()[:50], confidence],
                )

        logger.info("Enriched ticket %s: confidence=%.2f, %d articles, %d tags suggested",
                     ticket_id, confidence, len(articles), len(suggested_tags))

        # Return KB results for downstream steps (engage uses these)
        return [
            {
                "document_id": r.get("document_id"),
                "title": r.get("title", ""),
                "content": r.get("content", "")[:500],
                "source_url": r.get("source_url", ""),
                "similarity": float(r.get("similarity", 0)),
                "module": r.get("metadata", {}).get("module", "") if isinstance(r.get("metadata"), dict) else "",
            }
            for r in relevant[:5]
        ]

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse enrichment response for ticket %s: %s", ticket_id, e)
        raise  # Let queue_service record this as a failed step
    except Exception as e:
        logger.error("Enrichment failed for ticket %s: %s", ticket_id, e)
        raise  # Let queue_service record this as a failed step
