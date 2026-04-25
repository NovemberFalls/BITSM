# Knowledge Base Pipeline Architecture

**Document Type:** Engineering Reference
**Last Updated:** 2026-03-17
**Owner:** BITSM Engineering

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Decisions & Rationale](#2-architecture-decisions--rationale)
3. [Pipeline Orchestration](#3-pipeline-orchestration)
4. [Complete Pipeline Process](#4-complete-pipeline-process)
5. [Tag Strategy](#5-tag-strategy)
6. [Data Flow Diagram](#6-data-flow-diagram)
7. [Database Schema](#7-database-schema)
8. [Rate Limits & Throttling](#8-rate-limits--throttling)
9. [Idempotency & Deduplication](#9-idempotency--deduplication)
10. [Pros & Cons of This Approach](#10-pros--cons-of-this-approach)
11. [Future Module Onboarding Guide](#11-future-module-onboarding-guide)
12. [Change Control Policy](#12-change-control-policy)
13. [Troubleshooting & Operations](#13-troubleshooting--operations)
14. [Appendix: File Reference](#14-appendix-file-reference)

---

## 1. Executive Summary

The Knowledge Base (KB) Pipeline is the system that turns raw documentation (scraped web pages, API docs, internal guides) into searchable, AI-retrievable knowledge for our helpdesk's RAG-powered support assistant.

**Pipeline in one sentence:** Scrape source docs to `.txt` files, ingest into PostgreSQL, auto-tag with Claude Haiku, split into heading-aware chunks with tags propagated, embed via Voyage AI, store as pgvector embeddings — all orchestrated by Flask's built-in queue service (`services/queue_service.py`) on a cron schedule.

**Why this matters:** The quality of our AI support assistant is directly proportional to how well this pipeline processes, tags, and indexes documentation. Poor chunking = irrelevant RAG results. Missing tags = larger search space and slower retrieval. Gaps in coverage = the AI says "I don't know" when it shouldn't.

**Scale context (as of 2026-03-17):**
- 16 active modules (Toast, Solink, Sonos, Power BI, Olo, Rockbot, R365, MS Outlook/Teams/Excel/SharePoint, Shift4, Lightspeed, Square, Oracle Simphony/Xstore), 17 defined (+ VSN)
- 10,000+ documents, 20,000+ chunks with 1536-dim vector embeddings
- Built-in pipeline scheduler (queue_service.py) handles full lifecycle: scrape → ingest → tag → embed on weekly cadence (Sunday 2am)
- Designed to scale to ~50,000 chunks across all modules before needing index tuning

---

## 2. Architecture Decisions & Rationale

Each major decision is documented with the alternatives considered and why we chose what we did.

### 2.1 pgvector over Dedicated Vector Database

**Decision:** Use PostgreSQL + pgvector extension on our existing database server.

**Alternatives considered:**
- Pinecone (managed, fast, expensive at scale)
- Weaviate (self-hosted, complex ops, separate infrastructure)
- ChromaDB (lightweight, but limited production features)

**Why pgvector:**
- **Co-location with relational data.** Documents, tenants, modules, and chunks all live in the same database. Joins between document metadata and vector embeddings are native SQL — no cross-service calls.
- **Operational simplicity.** One database to back up, monitor, and maintain. No additional infrastructure.
- **Scale adequacy.** At <50K chunks, pgvector with IVFFlat indexing provides sub-100ms search. We are nowhere near the threshold where a dedicated vector DB becomes necessary.
- **Cost.** Zero additional cost — pgvector is a free PostgreSQL extension.

**When to reconsider:** If chunk count exceeds 500K, or if search latency under load exceeds 200ms, evaluate HNSW indexing or a dedicated vector service.

### 2.2 Voyage AI for Embeddings (Primary Provider)

**Decision:** Use Voyage AI (`voyage-3` model) as primary embedding provider.

**Alternatives considered:**
- OpenAI `text-embedding-3-small` (supported as fallback, configured via env var)
- Cohere Embed v3

**Why Voyage:**
- Superior multilingual performance (we serve EN and ES users)
- Competitive quality at lower per-token cost than OpenAI
- 1536-dimension vectors (matching our pgvector column)

**Tradeoff:** Free tier is limited to 3 RPM / 10K TPM, requiring aggressive throttling (21s between calls). This makes initial bulk ingestion slow (~2+ hours for 2,000+ docs) but is acceptable for incremental updates.

**Fallback:** Set `EMBEDDING_PROVIDER=openai` in `.env` to switch. No code changes required.

### 2.3 Heading-Aware Chunking over Fixed-Size

**Decision:** Split documents at `###`/`####` Markdown heading boundaries, not fixed token windows.

**Alternatives considered:**
- Fixed-size sliding window (512 or 1024 tokens with overlap)
- Sentence-level splitting with semantic clustering
- Full document as single chunk

**Why heading-aware:**
- Toast documentation has clear hierarchical headings. Splitting at headings preserves semantic boundaries — a chunk about "KDS Setup" stays self-contained.
- Each chunk is prepended with `Article: {title}\nSection: {heading}`, giving the embedding model explicit context about what the chunk covers.
- Reduces "noise retrieval" where a fixed-size window spans two unrelated topics.

**Parameters:**
| Constant | Value | Purpose |
|----------|-------|---------|
| `SMALL_DOC_THRESHOLD` | 4,000 tokens | Below this, entire doc = 1 chunk |
| `MAX_SECTION_TOKENS` | 6,000 tokens | Above this, split section at paragraph boundaries |
| `TARGET_CHUNK_TOKENS` | 4,000 tokens | Target size for paragraph-level fallback splits |

**Tokenizer:** `cl100k_base` (tiktoken) — matches both Voyage and OpenAI token counting.

### 2.4 LLM Auto-Tagging over Manual or Rule-Based

**Decision:** Use Claude Haiku to auto-generate 1-5 tags per document.

**Alternatives considered:**
- Manual human tagging (doesn't scale at 2,300+ docs)
- Keyword extraction (TF-IDF, RAKE) — misses semantic topics
- Pre-defined taxonomy with classifier — requires training data we don't have

**Why LLM tagging:**
- Zero training data needed. Works out of the box with a simple system prompt.
- Semantic understanding — tags like "kitchen-display" for an article that never uses that exact phrase but describes KDS setup.
- Cost-effective: Haiku processes ~191 batches of 10 docs in ~95 seconds total, costing pennies.
- Tags are lowercase, hyphenated, max 50 chars, max 5 per doc.

**Accuracy:** Observed ~90%+ accuracy on spot checks. Occasional misses on highly niche articles, acceptable for our use case since tags supplement (not replace) vector search.

### 2.5 Tags on Both Documents AND Chunks

**Decision:** Store canonical tags on `documents.tags` and propagate to `document_chunks.tags` during chunking.

**Why dual storage:**
- **Documents own tags.** The tagging LLM sees the full document. Tags are generated and updated at document level.
- **Chunks need tags for retrieval.** RAG search operates on chunks, not documents. Without chunk-level tags, the retrieval query can't filter by topic.
- **GIN index on chunks.** A native `TEXT[]` column with GIN index enables sub-millisecond array containment queries (`dc.tags && ARRAY['kds']`), far faster than extracting from JSONB metadata.
- **Content hash independence.** Tags are NOT included in the content hash. If only tags change, existing chunks get a lightweight SQL UPDATE — no re-embedding needed.

---

## 3. Pipeline Orchestration

Pipeline scheduling and orchestration is handled entirely within Flask by `services/queue_service.py` — a PostgreSQL-backed job queue with sequential lanes, retry logic, and cron scheduling.

### Flask Owns ALL Logic

Flask services are the single source of truth for every operation:

| Operation | Flask Service | Function |
|-----------|---------------|----------|
| Parse .txt files into documents | `ingestion_service.py` | `ingest_directory()` |
| Clean document text | `text_cleaning_service.py` | `clean_document_text()` |
| Auto-tag documents | `doc_tagging_service.py` | `auto_tag_documents()` |
| Chunk documents | `chunking_service.py` | `chunk_document()` |
| Generate embeddings | `embedding_service.py` | `embed_texts()` |
| Orchestrate pipeline | `pipeline_service.py` | `run_full_pipeline()` |
| RAG retrieval | `rag_service.py` | `_tool_kb_search()` |
| Schedule + dispatch jobs | `queue_service.py` | `QueueProcessor`, cron loop |

### Why Everything Lives in Flask

- **Single codebase.** All pipeline logic is version-controlled in the Flask repo, testable locally, and debuggable with standard Python tools.
- **No external dependencies.** Scheduling uses PostgreSQL `pipeline_schedules` table with cron expressions — no separate orchestrator service to maintain.
- **No split-brain.** All state lives in one database, processed by one application.

See `services/queue_service.py` for the full implementation.

---

## 4. Complete Pipeline Process

### 4.1 Overview

The full pipeline runs as a single atomic operation via `run_full_pipeline(module_slug)`:

```
Step 1: INGEST — Pick up new .txt files from documents/{slug}/
Step 2: TAG    — Auto-tag any untagged documents via Claude Haiku
Step 3: EMBED  — Clean → Chunk (with tags) → Embed → Upsert chunks
```

### 4.2 Step 1: Ingest

**Service:** `ingestion_service.ingest_directory(directory, module_slug)`

**What it does:**
1. Lists all `.txt` files in `documents/{module_slug}/`
2. For each file, checks if `source_file` already exists in `documents` table for this module
3. If new: parses the file (extracts Source URL, Title, Content from header format), inserts into `documents`
4. If existing: skips (idempotent)

**Expected file format:**
```
Source: https://doc.toasttab.com/doc/platformguide/example
Title: How to Configure KDS
============================================================
Article content in Markdown format...
```

**Fallback:** If no `Source:`/`Title:` header, uses filename as title and entire file as content.

**Returns:** `{ingested: N, skipped: N, errors: N}`

### 4.3 Step 2: Tag

**Service:** `doc_tagging_service.auto_tag_documents(module_slug)`

**What it does:**
1. Fetches all documents where `tags IS NULL OR tags = '{}'` for the given module
2. Batches documents in groups of 10
3. For each batch, sends doc titles + 300-char excerpts to Claude Haiku
4. Haiku returns JSON mapping doc IDs to tag arrays
5. Tags are sanitized (lowercase, max 50 chars, max 5 per doc) and written to `documents.tags`
6. Sleeps 0.5s between batches for rate limiting

**System prompt:**
```
You are a knowledge base article tagger. For each article (identified by [ID:N]),
return a JSON object mapping the ID to an array of 1-5 short lowercase tags.
Tags should describe the topic (e.g., 'pos', 'kitchen-printer', 'menu-builder', 'payments').
Return ONLY valid JSON like: {"1": ["tag1", "tag2"], "2": ["tag3"]}
```

**Returns:** `{tagged: N, skipped: N, errors: N}`

### 4.4 Step 3: Embed Pipeline

**Service:** `pipeline_service.run_pipeline(module_slug, force=False)`

For each document in the module:

**4.4a. Clean**
- `text_cleaning_service.clean_document_text(content)`
- Removes scraping artifacts: duplicate callout boxes, bullet duplication, excessive indentation
- Normalizes whitespace

**4.4b. Chunk**
- `chunking_service.chunk_document(doc_id, title, cleaned, source_url, module_slug, tags=doc_tags)`
- Splits at heading boundaries (see Section 2.3)
- Each chunk includes: `chunk_index`, `content`, `token_count`, `tags`, `metadata` (module, title, section, source_url, content_hash, tags)

**4.4c. Content-Hash Dedup**
- Each chunk has a SHA-256 content hash (first 16 chars)
- Compared against existing chunk hashes for this document
- Unchanged chunks are skipped (no re-embedding)
- Stale chunks (hashes no longer present) are deleted
- `force=True` deletes ALL existing chunks and re-processes everything

**4.4d. Embed**
- Groups chunks into sub-batches respecting `EMBED_TOKEN_BUDGET` (3,000 tokens per API call)
- Calls `embedding_service.embed_texts()` for each sub-batch
- Throttles between calls: waits `EMBED_THROTTLE` seconds (21s for Voyage free tier)
- Retry: 4 attempts with exponential backoff on rate limits

**4.4e. Upsert**
- Inserts into `document_chunks` with: `document_id`, `module_id`, `chunk_index`, `content`, `token_count`, `embedding` (vector), `metadata` (JSONB), `content_hash`, `tags` (TEXT[])

**Audit trail:** Every run creates a `pipeline_runs` record tracking: documents_processed, chunks_created, chunks_skipped, chunks_deleted, errors, timestamps.

---

## 5. Tag Strategy

### 5.1 What Tags Are

Tags are lowercase, hyphenated strings that categorize a document's topic area. Examples:
- `pos`, `kitchen-printer`, `menu-builder`, `online-ordering`, `gift-cards`
- `payments`, `employees`, `kds`, `troubleshooting`, `integration`

Tags are NOT:
- Full sentences or descriptions
- Status indicators (no "needs-review" or "outdated")
- Version numbers or dates

### 5.2 How Tags Help RAG

**Without tags:** A query "How do I set up KDS?" searches all ~2,000+ Toast chunks by vector distance. Results may include chunks about "kitchen printers" (semantically similar but different topic).

**With tags (current release):** Search results include chunk-level tags in the response metadata. The LLM sees `"tags": ["kds", "kitchen-display", "setup"]` alongside each chunk, giving it stronger signal about what the chunk covers. This improves answer quality without changing the retrieval query.

**With tag filtering (future Phase 2):** Add `WHERE dc.tags && ARRAY['kds']` to the vector search query. This pre-filters to only KDS-related chunks BEFORE doing cosine similarity, reducing search space from 2,000 to ~50 chunks. Faster AND more accurate.

### 5.3 Tag Lifecycle

```
Document ingested (no tags)
    ↓
auto_tag_documents() runs → Claude Haiku assigns 1-5 tags
    ↓
Tags stored in documents.tags TEXT[]
    ↓
Pipeline runs → chunk_document() receives doc tags
    ↓
_make_chunk() copies tags into chunk dict
    ↓
Pipeline INSERT writes tags to document_chunks.tags TEXT[]
    ↓
RAG search returns chunks with tags in results
```

### 5.4 Tag Backfill

For documents that already had tags before this pipeline update, migration 010 backfills tags from `documents.tags` to existing `document_chunks.tags` via a single SQL UPDATE. No re-embedding required.

---

## 6. Data Flow Diagram

```
                                 ┌──────────────────────────────────┐
                                 │      queue_service.py Cron        │
                                 │  (Schedule: Weekly Sun 2am)       │
                                 │  PostgreSQL-backed job queue      │
                                 └──────────────┬───────────────────┘
                                                │
                                     _cron_kb_pipeline()
                                                │
                    ┌───────────────────────────▼───────────────────────────┐
                    │                   Flask: run_full_pipeline()           │
                    │                                                       │
                    │  ┌─────────────────────────────────────────────────┐  │
                    │  │ Step 1: INGEST                                  │  │
                    │  │  ingestion_service.ingest_directory()            │  │
                    │  │  documents/toast/*.txt → documents table         │  │
                    │  │  (idempotent: skip existing source_file)        │  │
                    │  └──────────────────────┬──────────────────────────┘  │
                    │                         │                             │
                    │  ┌──────────────────────▼──────────────────────────┐  │
                    │  │ Step 2: TAG                                     │  │
                    │  │  doc_tagging_service.auto_tag_documents()        │  │
                    │  │  Untagged docs → Claude Haiku → documents.tags   │  │
                    │  │  (batch 10, 0.5s sleep, lowercase 1-5 tags)     │  │
                    │  └──────────────────────┬──────────────────────────┘  │
                    │                         │                             │
                    │  ┌──────────────────────▼──────────────────────────┐  │
                    │  │ Step 3: EMBED PIPELINE                          │  │
                    │  │  For each document:                             │  │
                    │  │                                                 │  │
                    │  │  ┌─────────────────────────────────────────┐    │  │
                    │  │  │ Clean (text_cleaning_service)           │    │  │
                    │  │  │ Remove scraping artifacts, normalize    │    │  │
                    │  │  └──────────────┬──────────────────────────┘    │  │
                    │  │                 │                               │  │
                    │  │  ┌──────────────▼──────────────────────────┐    │  │
                    │  │  │ Chunk (chunking_service)                │    │  │
                    │  │  │ Split at headings, propagate doc tags   │    │  │
                    │  │  │ Compute content_hash per chunk          │    │  │
                    │  │  └──────────────┬──────────────────────────┘    │  │
                    │  │                 │                               │  │
                    │  │  ┌──────────────▼──────────────────────────┐    │  │
                    │  │  │ Dedup (content_hash comparison)         │    │  │
                    │  │  │ Skip unchanged, delete stale chunks     │    │  │
                    │  │  └──────────────┬──────────────────────────┘    │  │
                    │  │                 │                               │  │
                    │  │  ┌──────────────▼──────────────────────────┐    │  │
                    │  │  │ Embed (embedding_service via Voyage AI) │    │  │
                    │  │  │ Batch by token budget, throttle 21s     │    │  │
                    │  │  └──────────────┬──────────────────────────┘    │  │
                    │  │                 │                               │  │
                    │  │  ┌──────────────▼──────────────────────────┐    │  │
                    │  │  │ Upsert to document_chunks               │    │  │
                    │  │  │ content + embedding + tags + metadata    │    │  │
                    │  │  └─────────────────────────────────────────┘    │  │
                    │  └────────────────────────────────────────────────┘   │
                    │                                                       │
                    │  Returns: {ingest: {...}, tagging: {...},              │
                    │           pipeline: {chunks_created, skipped, ...}}   │
                    └───────────────────────────────────────────────────────┘
                                                │
                                     ┌──────────▼──────────┐
                                     │    pgvector index    │
                                     │  document_chunks     │
                                     │  (IVFFlat cosine)    │
                                     └──────────┬──────────┘
                                                │
                                    RAG query at chat time:
                                    embed query → cosine search
                                    → return chunks with tags
```

---

## 7. Database Schema

### 7.1 Core Tables

```sql
-- Source documents (one per scraped article)
documents (
    id              SERIAL PRIMARY KEY,
    module_id       INT REFERENCES knowledge_modules(id),
    tenant_id       INT REFERENCES tenants(id),    -- NULL for global KB, set for tenant articles
    source_file     TEXT,                           -- filename for dedup
    source_url      TEXT,
    title           TEXT,
    content         TEXT,
    is_published    BOOLEAN DEFAULT true,
    tags            TEXT[] DEFAULT '{}',            -- GIN indexed
    created_at      TIMESTAMPTZ DEFAULT now(),
    created_by      INT REFERENCES users(id)
);

-- Embedded chunks (one or more per document)
document_chunks (
    id              SERIAL PRIMARY KEY,
    document_id     INT REFERENCES documents(id) ON DELETE CASCADE,
    module_id       INT REFERENCES knowledge_modules(id),
    chunk_index     INT,
    content         TEXT NOT NULL,
    token_count     INT,
    embedding       vector(1536),                  -- pgvector
    metadata        JSONB DEFAULT '{}',            -- {module, title, section, source_url, content_hash, tags}
    content_hash    TEXT,                           -- SHA256[:16] for dedup
    tags            TEXT[] DEFAULT '{}',            -- GIN indexed, propagated from parent document
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Pipeline audit trail
pipeline_runs (
    id                  SERIAL PRIMARY KEY,
    module_id           INTEGER REFERENCES knowledge_modules(id),
    status              TEXT,          -- running, completed, failed
    documents_processed INTEGER,
    chunks_created      INTEGER,
    chunks_skipped      INTEGER,
    chunks_deleted      INTEGER,
    errors              INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMPTZ DEFAULT now(),
    completed_at        TIMESTAMPTZ
);
```

### 7.2 Indexes

```sql
CREATE INDEX idx_chunks_module ON document_chunks(module_id);
CREATE INDEX idx_chunks_document ON document_chunks(document_id);
CREATE INDEX idx_documents_tags ON documents USING GIN (tags);
CREATE INDEX idx_document_chunks_tags ON document_chunks USING GIN (tags);
-- IVFFlat vector index (created after initial bulk load):
-- CREATE INDEX idx_chunks_embedding_ivfflat
--   ON document_chunks USING ivfflat (embedding vector_cosine_ops)
--   WITH (lists = 100);
```

---

## 8. Rate Limits & Throttling

| Provider | Limit | Our Setting | Impact |
|----------|-------|-------------|--------|
| Voyage AI (free tier) | 3 RPM, 10K TPM | `EMBED_THROTTLE=21s`, `EMBED_TOKEN_BUDGET=3000` | Bulk ingestion of 2,000 docs takes ~2 hours |
| Voyage AI (paid tier) | Higher RPM/TPM | Reduce throttle to 1-2s | Same ingestion in ~15 minutes |
| Claude Haiku (tagging) | ~1000 RPM | 0.5s sleep between batches | 2,000 docs tagged in ~100 seconds |

**Important:** The throttle is global per process. If two pipeline runs execute simultaneously, they'll interfere with each other's rate limits. The pipeline checks for `status == "running"` and refuses to start a second run for the same module.

---

## 9. Idempotency & Deduplication

Every step in the pipeline is safe to re-run:

| Step | Dedup Mechanism | Re-run Behavior |
|------|-----------------|-----------------|
| Ingest | `source_file` uniqueness per module | Skips files already in DB |
| Tag | `WHERE tags IS NULL OR tags = '{}'` | Only processes untagged docs |
| Chunk | `content_hash` comparison | Skips chunks with unchanged content |
| Embed | Only called for new/changed chunks | No wasted API calls on unchanged content |

**Force mode:** `force=True` bypasses content_hash dedup, deletes all existing chunks, and re-processes everything. Use only when the embedding model changes or chunking logic is updated.

---

## 10. Pros & Cons of This Approach

### Pros

| Advantage | Detail |
|-----------|--------|
| **Single source of truth** | All logic in Flask Python services. One repo, one deployment, one set of logs. |
| **Idempotent by design** | Safe to re-run at any time. Content hashing prevents wasted embedding API calls. |
| **Incremental processing** | Only new/changed documents get processed. A weekly run on 2,300 docs typically processes <50 changes. |
| **LLM-quality tagging at scale** | Claude Haiku tags thousands of docs for pennies with ~90% accuracy. |
| **Audit trail** | Every pipeline run is logged with document counts, chunk counts, errors, and timestamps. |
| **Provider flexibility** | Embedding provider swappable via env var (Voyage ↔ OpenAI). No code changes. |
| **Self-contained scheduling** | Cron scheduling is built into the Flask queue service — no external orchestrator to maintain. |
| **Multi-tenant safe** | All data scoped by module_id. Tenant access controlled via tenant_modules join table. |

### Cons

| Limitation | Mitigation |
|------------|------------|
| **Slow bulk ingestion** | Voyage free tier throttle (21s/call) makes initial load take hours. Paid tier or OpenAI fallback reduces this to minutes. |
| **No real-time updates** | Pipeline runs on schedule (weekly) or manual trigger. Documentation changes aren't reflected until next run. |
| **Tag quality depends on LLM** | ~10% of tags may be suboptimal. Acceptable because tags supplement vector search, not replace it. |
| **Single-threaded pipeline** | One document at a time within a module. Parallelization would help but adds complexity and rate limit contention. |
| **No tag-based filtering yet** | Tags are in results but not used for pre-filtering in queries. Phase 2 work. |

---

## 11. Future Module Onboarding Guide

To add a new knowledge module (e.g., Solink):

### Step 1: Register the Module

```sql
INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('solink', 'Solink', 'Video surveillance and loss prevention', 'video', true);
```

### Step 2: Create the Scraper

Add a scraper module at `services/scrapers/{slug}.py` using the `@register` decorator:

```python
from services.scrapers import register, write_document, sanitize_filename

@register("solink")
def run(output_dir: Path) -> dict:
    # Scrape articles, save via write_document()
    # Return {"saved": N, "skipped": N, "errors": N, "total": N}
```

Output `.txt` files in the standard format (handled by `write_document()`):
```
Source: https://solink.com/docs/example
Title: Article Title Here
============================================================
Content here...
```

This is the ONLY module-specific code. Everything downstream is shared.

### Step 3: Run via API

The queue service cron handles scrape + pipeline automatically on schedule. For manual runs:

```bash
# Scrape only
curl -X POST https://bitsm.io/api/webhooks/scrape/run \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"module_slug": "solink", "sync": true}'

# Full pipeline (ingest + tag + embed)
curl -X POST https://bitsm.io/api/webhooks/pipeline/full \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"module_slug": "solink", "sync": true}'
```

### Step 4: Enable for Tenants

```sql
INSERT INTO tenant_modules (tenant_id, module_id)
SELECT t.id, km.id
FROM tenants t, knowledge_modules km
WHERE km.slug = 'solink' AND t.name = 'Target Tenant';
```

### Step 5: Verify Cron Pickup

The `kb_pipeline` cron job in `queue_service.py` automatically discovers all active modules with registered scrapers via `services/scrapers.available()`. No additional configuration needed — just register the scraper (Step 2) and the cron will pick it up on the next scheduled run.

---

## 12. Change Control Policy

### When to Deviate from This Process

This pipeline architecture should be treated as stable infrastructure. Deviations require documented justification in one of these categories:

**Critical — deviate immediately:**
- Data corruption or loss (e.g., chunks being deleted when they shouldn't be)
- Security vulnerability (e.g., SQL injection in a query)
- Production outage affecting RAG retrieval

**Justified — discuss before changing:**
- Embedding model upgrade (requires `force=True` re-run of all modules)
- Chunking strategy change (new heading patterns, different token targets)
- Tag generation prompt update (may change tag taxonomy)
- New pipeline step (e.g., adding a classification or summarization step)
- Rate limit changes (provider plan upgrade)

**Not justified — do not change:**
- "I think a different vector DB would be faster" — benchmark first, document results
- "Let's move logic into an external orchestrator" — violates Flask-owns-logic boundary
- "Let's add parallel embedding" — rate limit contention is worse than serial slowness
- "Let's tag at chunk level instead of doc level" — chunks don't have enough context for accurate tagging

### How to Document Changes

1. Update this document with the change, rationale, and date
2. Add a migration file if schema changes are needed
3. Update project documentation (README.md, etc.) if the change affects setup or development workflow
4. Test in dev mode before deploying to production

---

## 13. Troubleshooting & Operations

### Pipeline Stuck in "Running"

**Symptom:** `GET /api/webhooks/pipeline/status/toast` returns `{"status": "running"}` indefinitely.

**Cause:** Pipeline thread crashed without updating status. The in-memory `_pipeline_status` dict retains the stale state.

**Fix:**
```sql
-- Clean stale runs in DB
UPDATE pipeline_runs SET status = 'failed', error_message = 'Manual cleanup', completed_at = now()
WHERE status = 'running' AND started_at < now() - INTERVAL '1 hour';
```
Then restart the Flask process to clear in-memory state.

### Documents Missing Chunks

**Check:**
```sql
SELECT count(*) FROM documents d
WHERE d.module_id = (SELECT id FROM knowledge_modules WHERE slug = 'toast')
  AND NOT EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.document_id = d.id);
```

**Cause:** Documents with empty content, or pipeline errors during processing.

**Fix:** Run pipeline with `force=False` — it will process any documents that don't have chunks.

### Tags Not Propagating to Chunks

**Check:**
```sql
SELECT count(*) FROM document_chunks WHERE tags IS NULL OR tags = '{}';
SELECT count(*) FROM documents WHERE tags IS NOT NULL AND array_length(tags, 1) > 0;
```

**Fix:** If documents have tags but chunks don't, re-run the pipeline with `force=True` to re-chunk with tags. Or run the backfill query from migration 010.

### Embedding Rate Limit Errors

**Symptom:** Logs show "429 Too Many Requests" or "rate limit exceeded".

**Fix:** Increase `EMBED_THROTTLE` env var (e.g., from 21 to 30). Or upgrade Voyage plan. The embedding service has automatic retry with exponential backoff (4 attempts, 65s base delay).

---

## 14. Appendix: File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `services/pipeline_service.py` | ~280 | Pipeline orchestration: clean → chunk → embed |
| `services/chunking_service.py` | ~152 | Heading-aware document splitting |
| `services/embedding_service.py` | ~86 | Voyage/OpenAI embedding with batching + retry |
| `services/doc_tagging_service.py` | ~93 | Claude Haiku auto-tagging in batches of 10 |
| `services/text_cleaning_service.py` | ~150 | Scraping artifact removal |
| `services/ingestion_service.py` | ~80 | .txt file parsing + DB insertion |
| `services/rag_service.py` | ~600+ | RAG retrieval, tool-use loop, streaming |
| `services/scrapers/__init__.py` | ~130 | Scraper registry, shared utilities (write_document, html_to_text) |
| `services/scrapers/solink.py` | ~180 | Solink help center crawler (Intercom, sync requests) |
| `services/scrapers/sonos.py` | ~200 | Sonos support scraper (sitemap + async aiohttp) |
| `services/scrapers/powerbi.py` | ~140 | Power BI docs (GitHub tarball + markdown processing) |
| `services/scrapers/zendesk.py` | ~160 | Shared Zendesk Help Center API scraper |
| `services/scrapers/olo.py` | ~20 | Olo scraper wrapper (Zendesk) |
| `services/scrapers/rockbot.py` | ~20 | Rockbot scraper wrapper (Zendesk) |
| `services/scrapers/r365.py` | ~180 | Restaurant365 scraper (Document360 sitemap + async) |
| `services/scrapers/microsoft.py` | ~240 | Microsoft Support scraper (4 products, sitemap shards + async) |
| `services/scrapers/shift4.py` | ~20 | Shift4/SkyTab scraper wrapper (Zendesk) |
| `services/scrapers/lightspeed.py` | ~35 | Lightspeed scraper (multi-instance Zendesk) |
| `services/scrapers/square.py` | ~170 | Square help scraper (sitemap + SSR HTML + async) |
| `services/scrapers/oracle.py` | ~200 | Oracle MICROS scraper (Simphony + Xstore, static HTML TOC) |
| `routes/webhooks.py` | ~230 | Webhook endpoints (pipeline, scraper, KB tools) |
| `routes/kb.py` | ~410 | KB browsing, articles, tags |
| `routes/ai.py` | ~330 | Chat endpoints, SSE streaming |
| `config.py` | ~100 | 40+ env vars (DB, auth, AI) |
| `services/scrapers/toast.py` | ~344 | Toast scraper (BFS platformguide + async support, registered as `toast`) |

| `migrations/010_chunk_tags.sql` | ~20 | Chunk tags column + backfill |
| `migrations/011_sonos_module.sql` | ~8 | Insert Sonos knowledge module |

---

*This document is the authoritative reference for the KB pipeline. All team members working on knowledge base features should read and follow this guide. Updates to this document should be committed to git with a clear commit message.*
