"""Batch auto-tagging service for KB documents using Claude Haiku."""

import json
import logging
import time

from config import Config
from models.db import fetch_all, execute
from services.llm_provider import complete

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


def auto_tag_documents(module_slug: str | None = None, tenant_id: int | None = None) -> dict:
    """Auto-tag all documents with empty tags. Returns {tagged, skipped, errors}."""
    if not Config.ANTHROPIC_API_KEY and not Config.OPENAI_API_KEY:
        return {"tagged": 0, "skipped": 0, "errors": 0, "error": "No API key"}

    conditions = ["(tags IS NULL OR tags = '{}')"]
    params = []

    if module_slug:
        conditions.append(
            "module_id = (SELECT id FROM knowledge_modules WHERE slug = %s)"
        )
        params.append(module_slug)

    where = " AND ".join(conditions)
    docs = fetch_all(
        f"SELECT id, title, LEFT(content, 300) as excerpt FROM documents WHERE {where} ORDER BY id",
        params,
    )

    if not docs:
        return {"tagged": 0, "skipped": 0, "errors": 0}

    tagged = 0
    errors = 0

    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        batch_text = "\n\n".join(
            f"[ID:{d['id']}] {d['title']}\n{d.get('excerpt', '')}" for d in batch
        )

        try:
            result = complete(
                model=Config.AI_MODEL_ROUTER,
                max_tokens=1000,
                system=(
                    "You are a knowledge base article tagger. For each article (identified by [ID:N]), "
                    "return a JSON object mapping the ID to an array of 1-5 short lowercase tags. "
                    "Tags should describe the topic (e.g., 'pos', 'kitchen-printer', 'menu-builder', 'payments'). "
                    "Return ONLY valid JSON like: {\"1\": [\"tag1\", \"tag2\"], \"2\": [\"tag3\"]}"
                ),
                messages=[{"role": "user", "content": batch_text}],
                tenant_id=tenant_id,
                caller="doc_tagging",
            )

            raw = result.text
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]  # skip first line
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            tag_map = json.loads(raw)

            for doc in batch:
                doc_id = str(doc["id"])
                if doc_id in tag_map and isinstance(tag_map[doc_id], list):
                    tags = [t.strip().lower()[:50] for t in tag_map[doc_id] if isinstance(t, str)][:5]
                    if tags:
                        execute(
                            "UPDATE documents SET tags = %s WHERE id = %s",
                            [tags, doc["id"]],
                        )
                        tagged += 1

            logger.info("Tagged batch %d-%d (%d docs)", i, i + len(batch), len(batch))
            time.sleep(0.5)  # Rate limit

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response for batch %d: %s", i, e)
            errors += len(batch)
        except Exception as e:
            logger.error("Auto-tag batch %d failed: %s", i, e)
            errors += len(batch)

    return {"tagged": tagged, "skipped": len(docs) - tagged - errors, "errors": errors}
