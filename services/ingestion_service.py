"""Ingestion service: parse scraped .txt docs and insert into documents table."""

import logging
import os

from models.db import fetch_one, insert_returning

logger = logging.getLogger(__name__)


def parse_document_file(filepath: str) -> dict | None:
    """Parse a scraped .txt file into {source_url, title, content, source_file}.

    Expected format:
        Source: <url>
        Title: <title>
        ============================================================
        <content>
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        logger.error("Failed to read %s: %s", filepath, e)
        return None

    lines = text.split("\n")
    source_url = ""
    title = ""
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Source:"):
            source_url = stripped[len("Source:"):].strip()
        elif stripped.startswith("Title:"):
            title = stripped[len("Title:"):].strip()
        elif stripped.startswith("=" * 10):
            content_start = i + 1
            break

    if not title and not source_url:
        # Fallback: treat entire file as content, filename as title
        title = os.path.splitext(os.path.basename(filepath))[0]
        content_start = 0

    content = "\n".join(lines[content_start:]).strip()
    source_file = os.path.basename(filepath)

    return {
        "source_url": source_url or None,
        "title": title or source_file,
        "content": content,
        "source_file": source_file,
    }


def ingest_directory(directory: str, module_slug: str) -> dict:
    """Batch insert .txt files from directory into documents table.

    Idempotent: skips files already ingested for this module.
    Returns {ingested, skipped, errors} counts.
    """
    # Look up module
    module = fetch_one(
        "SELECT id FROM knowledge_modules WHERE slug = %s", [module_slug]
    )
    if not module:
        return {"error": f"Module '{module_slug}' not found", "ingested": 0, "skipped": 0, "errors": 0}

    module_id = module["id"]
    stats = {"ingested": 0, "skipped": 0, "errors": 0}

    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory}", **stats}

    txt_files = sorted(f for f in os.listdir(directory) if f.endswith(".txt"))

    for filename in txt_files:
        filepath = os.path.join(directory, filename)

        # Idempotent check: skip if source_file already exists for this module
        existing = fetch_one(
            "SELECT id FROM documents WHERE module_id = %s AND source_file = %s",
            [module_id, filename],
        )
        if existing:
            stats["skipped"] += 1
            continue

        doc = parse_document_file(filepath)
        if not doc:
            stats["errors"] += 1
            continue

        try:
            insert_returning(
                """INSERT INTO documents (module_id, title, content, source_url, source_file)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                [module_id, doc["title"], doc["content"], doc["source_url"], doc["source_file"]],
            )
            stats["ingested"] += 1
        except Exception as e:
            logger.error("Failed to ingest %s: %s", filename, e)
            stats["errors"] += 1

    return stats
