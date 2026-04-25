"""KB Pipeline service: orchestrates clean → chunk → embed for knowledge modules."""

import json
import logging
import os
import threading
import time

from config import Config
from models.db import fetch_one, fetch_all, execute, insert_returning, cursor

logger = logging.getLogger(__name__)

# In-memory status tracking (for polling)
_pipeline_status: dict[str, dict] = {}
_status_lock = threading.Lock()

# Throttle between embed calls (seconds). Voyage free tier = 3 RPM → 21s between calls.
EMBED_THROTTLE = float(os.environ.get("EMBED_THROTTLE", "21"))
# Token budget per embedding call. Voyage free tier: 10K TPM / 3 RPM = 3333 max per call.
EMBED_TOKEN_BUDGET = int(os.environ.get("EMBED_TOKEN_BUDGET", "3000"))
_last_embed_time = 0.0


def start_pipeline(module_slug: str, force: bool = False):
    """Fire-and-forget: spawn daemon thread to run the KB pipeline."""
    provider = Config.EMBEDDING_PROVIDER
    if provider == "voyage" and not Config.VOYAGE_API_KEY:
        logger.warning("VOYAGE_API_KEY not set — cannot run pipeline")
        return
    if provider == "openai" and not Config.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — cannot run pipeline")
        return

    with _status_lock:
        current = _pipeline_status.get(module_slug)
        if current and current.get("status") == "running":
            logger.warning("Pipeline already running for %s", module_slug)
            return

    thread = threading.Thread(target=_pipeline_worker, args=(module_slug, force), daemon=True)
    thread.start()


def get_pipeline_status(module_slug: str) -> dict:
    """Get current pipeline status for a module."""
    with _status_lock:
        return _pipeline_status.get(module_slug, {"status": "idle"})


def run_pipeline(module_slug: str, force: bool = False, verbose: bool = False) -> dict:
    """Run the full pipeline synchronously. Returns stats dict."""
    return _pipeline_worker(module_slug, force, verbose)


def _pipeline_worker(module_slug: str, force: bool = False, verbose: bool = False) -> dict:
    """Orchestrate: for each document, clean → chunk → embed → upsert."""
    from services.text_cleaning_service import clean_document_text
    from services.chunking_service import chunk_document
    from services.embedding_service import embed_texts

    stats = {
        "status": "running",
        "documents_processed": 0,
        "chunks_created": 0,
        "chunks_skipped": 0,
        "chunks_deleted": 0,
        "errors": 0,
        "started_at": time.time(),
    }

    with _status_lock:
        _pipeline_status[module_slug] = stats

    # Look up module
    module = fetch_one("SELECT id FROM knowledge_modules WHERE slug = %s", [module_slug])
    if not module:
        stats["status"] = "failed"
        stats["error"] = f"Module '{module_slug}' not found"
        return stats

    module_id = module["id"]

    # Create pipeline run record
    run_id = insert_returning(
        """INSERT INTO pipeline_runs (module_id, status)
           VALUES (%s, 'running') RETURNING id""",
        [module_id],
    )

    try:
        # Fetch all documents for this module (including tags for propagation to chunks)
        documents = fetch_all(
            "SELECT id, title, content, source_url, tags FROM documents WHERE module_id = %s ORDER BY id",
            [module_id],
        )

        if verbose:
            print(f"Processing {len(documents)} documents for module '{module_slug}'...")

        for doc in documents:
            try:
                _process_document(doc, module_id, module_slug, force, stats, verbose)
                stats["documents_processed"] += 1
            except Exception as e:
                logger.error("Pipeline error for doc %s: %s", doc["id"], e)
                stats["errors"] += 1

        stats["status"] = "completed"
        if verbose:
            print(f"Pipeline complete: {stats['chunks_created']} created, "
                  f"{stats['chunks_skipped']} skipped, {stats['chunks_deleted']} deleted, "
                  f"{stats['errors']} errors")

    except Exception as e:
        logger.error("Pipeline failed for %s: %s", module_slug, e)
        stats["status"] = "failed"
        stats["error"] = str(e)

    # Update pipeline run record
    execute(
        """UPDATE pipeline_runs
           SET status = %s, documents_processed = %s, chunks_created = %s,
               chunks_skipped = %s, chunks_deleted = %s, errors = %s,
               error_message = %s, completed_at = now()
           WHERE id = %s""",
        [
            stats["status"], stats["documents_processed"], stats["chunks_created"],
            stats["chunks_skipped"], stats["chunks_deleted"], stats["errors"],
            stats.get("error"), run_id,
        ],
    )

    with _status_lock:
        _pipeline_status[module_slug] = stats

    return stats


def _process_document(doc: dict, module_id: int, module_slug: str, force: bool, stats: dict, verbose: bool):
    """Process a single document: clean, chunk, diff, embed, upsert."""
    from services.text_cleaning_service import clean_document_text
    from services.chunking_service import chunk_document
    from services.embedding_service import embed_texts

    doc_id = doc["id"]
    content = doc.get("content") or ""
    title = doc.get("title") or ""
    source_url = doc.get("source_url")

    if not content.strip():
        return

    # Clean
    cleaned = clean_document_text(content)

    # Chunk (propagate document-level tags to each chunk)
    doc_tags = doc.get("tags") or []
    chunks = chunk_document(doc_id, title, cleaned, source_url, module_slug, tags=doc_tags)

    if not chunks:
        return

    # Get existing chunks for this document
    existing = fetch_all(
        "SELECT id, content_hash FROM document_chunks WHERE document_id = %s",
        [doc_id],
    )
    existing_hashes = {row["content_hash"] for row in existing if row.get("content_hash")}
    existing_ids = {row["id"] for row in existing}

    # Determine new/changed chunks
    new_chunks = []
    kept_hashes = set()
    for chunk in chunks:
        h = chunk["metadata"]["content_hash"]
        kept_hashes.add(h)
        if not force and h in existing_hashes:
            stats["chunks_skipped"] += 1
        else:
            new_chunks.append(chunk)

    # Delete stale chunks (hashes no longer present)
    if existing:
        stale_hashes = existing_hashes - kept_hashes
        if stale_hashes or force:
            if force:
                # Delete all existing chunks when forcing
                execute("DELETE FROM document_chunks WHERE document_id = %s", [doc_id])
                stats["chunks_deleted"] += len(existing)
            else:
                for row in existing:
                    if row.get("content_hash") in stale_hashes:
                        execute("DELETE FROM document_chunks WHERE id = %s", [row["id"]])
                        stats["chunks_deleted"] += 1

    if not new_chunks:
        return

    if verbose:
        print(f"  {title[:60]}... — {len(new_chunks)} new chunks")

    # Split chunks into token-budget sub-batches (respects Voyage TPM limit)
    sub_batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for chunk in new_chunks:
        t = chunk.get("token_count", 0)
        if current and current_tokens + t > EMBED_TOKEN_BUDGET:
            sub_batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += t
    if current:
        sub_batches.append(current)

    # Embed each sub-batch with throttle between calls
    chunk_to_embedding: dict[int, list[float]] = {}
    global _last_embed_time
    for sub_batch in sub_batches:
        if EMBED_THROTTLE > 0 and _last_embed_time > 0:
            elapsed = time.time() - _last_embed_time
            if elapsed < EMBED_THROTTLE:
                wait = EMBED_THROTTLE - elapsed
                if verbose:
                    print(f"    Throttling {wait:.0f}s ({sum(c['token_count'] for c in sub_batch)} tokens)...")
                time.sleep(wait)

        texts = [c["content"] for c in sub_batch]
        embeddings = embed_texts(texts)
        _last_embed_time = time.time()
        for chunk, emb in zip(sub_batch, embeddings):
            chunk_to_embedding[id(chunk)] = emb

    # Insert chunks (with tags propagated from parent document)
    for chunk, embedding in [(c, chunk_to_embedding[id(c)]) for c in new_chunks]:
        with cursor() as cur:
            cur.execute(
                """INSERT INTO document_chunks
                   (document_id, module_id, chunk_index, content, token_count, embedding, metadata, content_hash, tags)
                   VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, %s)""",
                [
                    doc_id, module_id, chunk["chunk_index"],
                    chunk["content"], chunk["token_count"],
                    str(embedding), json.dumps(chunk["metadata"]),
                    chunk["metadata"]["content_hash"],
                    chunk.get("tags", []),
                ],
            )
        stats["chunks_created"] += 1


def run_full_pipeline(module_slug: str, force: bool = False, verbose: bool = False) -> dict:
    """Run the complete KB pipeline: ingest new files, auto-tag, then embed.

    This is the unified entry point that the pipeline queue and admin UI call.
    See docs/knowledge-pipeline-architecture.md for the full process.

    Steps:
      1. Ingest new .txt files from documents/{module_slug}/
      2. Auto-tag any untagged documents via Claude Haiku
      3. Run the embed pipeline (clean -> chunk with tags -> embed)

    Returns combined stats from all three steps.
    """
    from services.ingestion_service import ingest_directory
    from services.doc_tagging_service import auto_tag_documents

    combined = {
        "module_slug": module_slug,
        "ingest": {},
        "tagging": {},
        "pipeline": {},
        "status": "running",
    }

    # Step 1: Ingest new files from disk
    doc_dir = os.path.join(Config.DOCUMENTS_DIR, module_slug)
    if os.path.isdir(doc_dir):
        if verbose:
            print(f"Step 1/3: Ingesting from {doc_dir}...")
        combined["ingest"] = ingest_directory(doc_dir, module_slug)
        if verbose:
            print(f"  Ingest: {combined['ingest']}")
    else:
        combined["ingest"] = {"skipped": True, "reason": f"No directory: {doc_dir}"}

    # Step 2: Auto-tag untagged documents
    if verbose:
        print(f"Step 2/3: Auto-tagging untagged documents...")
    try:
        combined["tagging"] = auto_tag_documents(module_slug)
        if verbose:
            print(f"  Tagging: {combined['tagging']}")
    except Exception as e:
        logger.error("Auto-tagging failed for %s: %s", module_slug, e)
        combined["tagging"] = {"tagged": 0, "errors": 1, "error": str(e)}

    # Step 3: Run the embed pipeline (clean -> chunk with tags -> embed)
    if verbose:
        print(f"Step 3/3: Running embed pipeline...")
    combined["pipeline"] = run_pipeline(module_slug, force=force, verbose=verbose)

    combined["status"] = combined["pipeline"].get("status", "unknown")
    return combined


def embed_tenant_article(document_id: int, tenant_id: int, force: bool = False) -> dict:
    """Chunk and embed a single tenant article (module_id IS NULL).

    Unlike the module pipeline, this processes a single document and tracks
    token usage under the tenant's billing (caller='kb_embed').

    Safe to call from a background thread.
    """
    from services.text_cleaning_service import clean_document_text
    from services.chunking_service import chunk_document
    from services.embedding_service import embed_texts
    from models.db import fetch_one, fetch_all, execute, cursor

    stats = {"chunks_created": 0, "chunks_skipped": 0, "chunks_deleted": 0, "total_tokens_embedded": 0}

    doc = fetch_one(
        "SELECT id, title, content, source_url, tags FROM documents WHERE id = %s AND tenant_id = %s",
        [document_id, tenant_id],
    )
    if not doc:
        return {"error": "Document not found", **stats}

    content = (doc.get("content") or "").strip()
    title = doc.get("title") or ""
    if not content:
        return {"error": "Document has no content", **stats}

    # Clean and chunk
    cleaned = clean_document_text(content)
    doc_tags = doc.get("tags") or []
    chunks = chunk_document(document_id, title, cleaned, doc.get("source_url"), "tenant", tags=doc_tags)
    if not chunks:
        return stats

    # Diff against existing chunks
    existing = fetch_all(
        "SELECT id, content_hash FROM document_chunks WHERE document_id = %s",
        [document_id],
    )
    existing_hashes = {row["content_hash"] for row in existing if row.get("content_hash")}

    new_chunks = []
    kept_hashes = set()
    for chunk in chunks:
        h = chunk["metadata"]["content_hash"]
        kept_hashes.add(h)
        if not force and h in existing_hashes:
            stats["chunks_skipped"] += 1
        else:
            new_chunks.append(chunk)

    # Delete stale chunks
    if existing:
        stale_hashes = existing_hashes - kept_hashes
        if stale_hashes or force:
            if force:
                execute("DELETE FROM document_chunks WHERE document_id = %s", [document_id])
                stats["chunks_deleted"] += len(existing)
            else:
                for row in existing:
                    if row.get("content_hash") in stale_hashes:
                        execute("DELETE FROM document_chunks WHERE id = %s", [row["id"]])
                        stats["chunks_deleted"] += 1

    if not new_chunks:
        return stats

    # Embed all new chunks (tenant has paid Voyage — no throttle needed)
    # Use BYOK key if tenant is enterprise
    total_tokens = sum(c["token_count"] for c in new_chunks)
    texts = [c["content"] for c in new_chunks]
    embeddings = embed_texts(texts, tenant_id=tenant_id)
    stats["total_tokens_embedded"] = total_tokens

    # Insert chunks with module_id = NULL for tenant articles
    for chunk, embedding in zip(new_chunks, embeddings):
        with cursor() as cur:
            cur.execute(
                """INSERT INTO document_chunks
                   (document_id, module_id, chunk_index, content, token_count, embedding, metadata, content_hash, tags)
                   VALUES (%s, NULL, %s, %s, %s, %s::vector, %s::jsonb, %s, %s)""",
                [
                    document_id, chunk["chunk_index"],
                    chunk["content"], chunk["token_count"],
                    str(embedding), json.dumps(chunk["metadata"]),
                    chunk["metadata"]["content_hash"],
                    chunk.get("tags", []),
                ],
            )
        stats["chunks_created"] += 1

    # Track token usage for billing — embedding costs under 'kb_embed' caller
    _record_embed_usage(tenant_id, total_tokens)

    logger.info(
        "Tenant article %d embedded: %d chunks created, %d skipped, %d deleted, %d tokens",
        document_id, stats["chunks_created"], stats["chunks_skipped"],
        stats["chunks_deleted"], total_tokens,
    )
    return stats


def start_tenant_article_embed(document_id: int, tenant_id: int):
    """Fire-and-forget: spawn daemon thread to embed a tenant article."""
    thread = threading.Thread(
        target=embed_tenant_article,
        args=(document_id, tenant_id),
        daemon=True,
    )
    thread.start()


def _record_embed_usage(tenant_id: int, input_tokens: int) -> None:
    """Record embedding token usage in both tenant_token_usage and api_usage_monthly.

    Uses the configured embedding model (voyage-3 by default).
    """
    from config import Config as _Cfg
    model = _Cfg.EMBEDDING_MODEL  # e.g. 'voyage-3'
    provider = _Cfg.EMBEDDING_PROVIDER  # e.g. 'voyage'
    caller = "kb_embed"

    def _insert():
        try:
            from models.db import execute as db_execute
            from services import billing_service

            # Per-call detail log (tenant_token_usage)
            rates = billing_service.COSTS.get(model, {"input": 0, "output": 0})
            cost = input_tokens * rates["input"]
            db_execute(
                """INSERT INTO tenant_token_usage
                   (tenant_id, ticket_id, provider, model, caller,
                    input_tokens, output_tokens, cost_usd,
                    rate_input, rate_output)
                   VALUES (%s, NULL, %s, %s, %s, %s, 0, %s, %s, 0)""",
                [tenant_id, provider, model, caller,
                 input_tokens, cost,
                 rates["input"] * 1_000_000],  # rate stored as per-1M
            )

            # Monthly rollup (api_usage_monthly)
            billing_service.record_usage(tenant_id, model, caller, input_tokens, 0)
        except Exception as exc:
            logger.debug("kb_embed usage recording failed: %s", exc)

    threading.Thread(target=_insert, daemon=True).start()


def start_full_pipeline(module_slug: str, force: bool = False):
    """Fire-and-forget: spawn daemon thread to run the full KB pipeline."""
    with _status_lock:
        current = _pipeline_status.get(module_slug)
        if current and current.get("status") == "running":
            logger.warning("Pipeline already running for %s", module_slug)
            return

    thread = threading.Thread(
        target=run_full_pipeline,
        args=(module_slug, force, True),
        daemon=True,
    )
    thread.start()
