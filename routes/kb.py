"""Knowledge base blueprint: browse/search articles by module + tenant articles."""

import logging
import threading

from flask import Blueprint, jsonify, request

from routes.auth import login_required, require_role, require_permission, get_current_user, get_tenant_id
from models.db import fetch_all, fetch_one, insert_returning, execute

logger = logging.getLogger(__name__)
kb_bp = Blueprint("kb", __name__)


@kb_bp.route("/modules", methods=["GET"])
@login_required
def list_enabled_modules():
    """List knowledge modules enabled for the current tenant."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify([])

    modules = fetch_all(
        """SELECT km.*, tm.enabled_at
           FROM knowledge_modules km
           JOIN tenant_modules tm ON tm.module_id = km.id
           WHERE tm.tenant_id = %s AND km.is_active = true
           ORDER BY km.name""",
        [tenant_id],
    )
    return jsonify(modules)


@kb_bp.route("/documents", methods=["GET"])
@login_required
def list_documents():
    """List documents: tenant's enabled modules + tenant's own articles."""
    tenant_id = get_tenant_id()
    module_slug = request.args.get("module")
    search = request.args.get("q", "").strip()
    tag_filter = request.args.get("tag", "").strip()
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    conditions = ["d.is_published = true"]
    params = []

    # Scope: tenant's enabled modules OR tenant's own articles
    if tenant_id:
        conditions.append(
            "(d.module_id IN (SELECT module_id FROM tenant_modules WHERE tenant_id = %s) "
            "OR d.tenant_id = %s)"
        )
        params.extend([tenant_id, tenant_id])

    if module_slug:
        conditions.append("km.slug = %s")
        params.append(module_slug)

    if tag_filter:
        conditions.append("%s = ANY(d.tags)")
        params.append(tag_filter)

    if search:
        conditions.append(
            "(d.title ILIKE %s OR d.content ILIKE %s OR EXISTS (SELECT 1 FROM unnest(d.tags) t WHERE t ILIKE %s))"
        )
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Get total count
    count_row = fetch_one(
        f"""SELECT COUNT(*) as total
            FROM documents d
            LEFT JOIN knowledge_modules km ON km.id = d.module_id
            {where}""",
        params,
    )
    total = count_row["total"] if count_row else 0

    params.extend([limit, offset])
    docs = fetch_all(
        f"""SELECT d.id, d.title, d.source_url, d.tags,
                   COALESCE(km.name, tc.name, 'My Articles') as module_name,
                   COALESCE(km.slug, 'tenant') as module_slug,
                   d.created_at
            FROM documents d
            LEFT JOIN knowledge_modules km ON km.id = d.module_id
            LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
            {where}
            ORDER BY d.title
            LIMIT %s OFFSET %s""",
        params,
    )
    return jsonify({"documents": docs, "total": total})


@kb_bp.route("/tags", methods=["GET"])
@login_required
def list_tags():
    """Return distinct tags with counts for the tenant's visible documents."""
    tenant_id = get_tenant_id()

    conditions = ["d.is_published = true"]
    params = []

    if tenant_id:
        conditions.append(
            "(d.module_id IN (SELECT module_id FROM tenant_modules WHERE tenant_id = %s) "
            "OR d.tenant_id = %s)"
        )
        params.extend([tenant_id, tenant_id])

    where = " AND ".join(conditions)

    tags = fetch_all(
        f"""SELECT tag, COUNT(*) as count
            FROM documents d, unnest(d.tags) AS tag
            WHERE {where}
            GROUP BY tag
            ORDER BY count DESC, tag
            LIMIT 100""",
        params,
    )
    return jsonify(tags)


@kb_bp.route("/documents/<int:doc_id>", methods=["GET"])
@login_required
def get_document(doc_id: int):
    """Get a single document with full content."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    doc = fetch_one(
        """SELECT d.*, km.name as module_name, km.slug as module_slug
           FROM documents d
           LEFT JOIN knowledge_modules km ON km.id = d.module_id
           WHERE d.id = %s""",
        [doc_id],
    )
    if not doc:
        return jsonify({"error": "Not found"}), 404

    # Access control: super_admin sees all, others must have module enabled or own tenant article
    if user["role"] != "super_admin" and tenant_id:
        is_tenant_article = doc.get("tenant_id") == tenant_id
        is_enabled_module = doc.get("module_id") and fetch_one(
            "SELECT id FROM tenant_modules WHERE tenant_id = %s AND module_id = %s",
            [tenant_id, doc["module_id"]],
        )
        if not is_tenant_article and not is_enabled_module:
            return jsonify({"error": "Not found"}), 404

    return jsonify(doc)


@kb_bp.route("/suggest/<int:ticket_id>", methods=["GET"])
@login_required
def suggest_articles(ticket_id: int):
    """Suggest KB articles using vector similarity search on ticket context."""
    tenant_id = get_tenant_id()
    user = get_current_user()
    is_super = user.get("role") == "super_admin"

    if is_super:
        ticket = fetch_one(
            "SELECT t.subject, t.description FROM tickets t WHERE t.id = %s",
            [ticket_id],
        )
    else:
        ticket = fetch_one(
            "SELECT t.subject, t.description FROM tickets t WHERE t.id = %s AND t.tenant_id = %s",
            [ticket_id, tenant_id],
        )
    if not ticket:
        return jsonify([])

    subject = ticket.get("subject", "") or ""
    description = (ticket.get("description") or "")[:500]
    search_query = f"{subject} {description}".strip()
    if not search_query:
        return jsonify([])

    # Get tenant's enabled module IDs for scoping
    module_ids = []
    if tenant_id:
        module_ids = [
            r["module_id"] for r in
            fetch_all("SELECT module_id FROM tenant_modules WHERE tenant_id = %s", [tenant_id])
        ]

    # Vector similarity search on document chunks
    try:
        from services.embedding_service import embed_single
        query_embedding = embed_single(search_query)
    except Exception as e:
        logger.warning("Embedding failed for article suggestion: %s", e)
        return jsonify([])

    # Find top chunks by cosine similarity, deduplicate by document, return top 8
    # Scope: tenant's enabled modules OR tenant's own articles (module_id IS NULL)
    docs = fetch_all(
        """SELECT DISTINCT ON (d.id)
                  d.id, d.title, d.tags, d.source_url,
                  COALESCE(km.name, tc.name, 'My Articles') as module_name,
                  COALESCE(km.slug, 'tenant') as module_slug,
                  1 - (dc.embedding <=> %s::vector) as similarity
           FROM document_chunks dc
           JOIN documents d ON d.id = dc.document_id AND d.is_published = true
           LEFT JOIN knowledge_modules km ON km.id = d.module_id
           LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
           WHERE (dc.module_id = ANY(%s)
                  OR (dc.module_id IS NULL AND d.tenant_id = %s))
           ORDER BY d.id, dc.embedding <=> %s::vector
           LIMIT 40""",
        [str(query_embedding), module_ids, tenant_id, str(query_embedding)],
    )

    # Re-sort by similarity descending and take top 8 with minimum threshold
    docs = sorted(docs, key=lambda d: d.get("similarity", 0), reverse=True)
    docs = [d for d in docs if d.get("similarity", 0) > 0.25][:8]

    # Remove similarity from response (internal metric)
    for d in docs:
        d.pop("similarity", None)

    return jsonify(docs)


@kb_bp.route("/send-to-ticket", methods=["POST"])
@login_required
@require_permission("tickets.create")
def send_article_to_ticket():
    """Send a KB article's content as a reply comment on a ticket."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}

    document_id = data.get("document_id")
    ticket_id = data.get("ticket_id")
    if not document_id or not ticket_id:
        return jsonify({"error": "document_id and ticket_id required"}), 400

    # Fetch document with access-control fields
    doc = fetch_one(
        "SELECT id, title, content, tenant_id AS doc_tenant_id, module_id FROM documents WHERE id = %s",
        [document_id],
    )
    if not doc:
        return jsonify({"error": "Not found"}), 404

    # Access control: super_admin sees all, others must own the article or have module enabled
    if user["role"] != "super_admin" and tenant_id:
        is_tenant_article = doc.get("doc_tenant_id") == tenant_id
        is_enabled_module = doc.get("module_id") and fetch_one(
            "SELECT id FROM tenant_modules WHERE tenant_id = %s AND module_id = %s",
            [tenant_id, doc["module_id"]],
        )
        if not is_tenant_article and not is_enabled_module:
            return jsonify({"error": "Not found"}), 404

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

    # Format article as reply content (truncate to 3000 chars)
    content = doc["content"] or ""
    if len(content) > 3000:
        content = content[:3000] + "\n\n[Article truncated — see full article in Knowledge Base]"

    reply_content = f"**KB Article: {doc['title']}**\n\n{content}"

    comment_id = insert_returning(
        """INSERT INTO ticket_comments (ticket_id, author_id, content, is_internal, is_ai_generated)
           VALUES (%s, %s, %s, false, false) RETURNING id""",
        [ticket_id, user["id"], reply_content],
    )

    execute("UPDATE tickets SET updated_at = now() WHERE id = %s", [ticket_id])

    # Track first response time for agent replies
    if user["role"] in ("super_admin", "tenant_admin", "agent"):
        execute(
            "UPDATE tickets SET first_response_at = now() WHERE id = %s AND first_response_at IS NULL",
            [ticket_id],
        )

    # Dispatch notifications (email, Teams, Slack, in-app) — mirrors tickets.py comment flow
    try:
        from services.queue_service import enqueue_notify
        author_name = user.get("name", "")
        comment_dict = {"content": reply_content, "author_name": author_name}
        enqueue_notify(tenant_id, ticket_id, "agent_reply", comment=comment_dict)
    except Exception as e:
        logger.warning("kb send-to-ticket notification dispatch failed: %s", e)

    # Fire matching automations
    try:
        from services.automation_engine import fire_automations
        fire_automations("comment_added", ticket_id, tenant_id, {"comment_type": "public"})
    except Exception as e:
        logger.warning("kb send-to-ticket automation dispatch failed: %s", e)

    return jsonify({"id": comment_id})


@kb_bp.route("/auto-tag", methods=["POST"])
@login_required
@require_role("super_admin")
def trigger_auto_tag():
    """Trigger auto-tagging of untagged documents (runs in background)."""
    from services.doc_tagging_service import auto_tag_documents

    module_slug = (request.json or {}).get("module_slug")
    tenant_id = get_tenant_id()

    def _run():
        result = auto_tag_documents(module_slug, tenant_id=tenant_id)
        logger.info("Auto-tag complete: %s", result)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"status": "started"})


# ============================================================
# Tenant Collections
# ============================================================

@kb_bp.route("/collections", methods=["GET"])
@login_required
def list_collections():
    """List tenant's article collections with doc counts."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify([])

    collections = fetch_all(
        """SELECT tc.id, tc.name, tc.slug, tc.description, tc.doc_count, tc.created_at,
                  u.name as created_by_name
           FROM tenant_collections tc
           LEFT JOIN users u ON u.id = tc.created_by
           WHERE tc.tenant_id = %s
           ORDER BY tc.name""",
        [tenant_id],
    )
    return jsonify(collections)


@kb_bp.route("/collections", methods=["POST"])
@login_required
@require_permission("kb.manage")
def create_collection():
    """Create a new tenant article collection."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Collection name is required"}), 400

    import re
    slug = re.sub(r'[^a-z0-9\s-]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug).strip('-')
    if not slug:
        slug = "collection"

    # Check uniqueness
    existing = fetch_one(
        "SELECT id FROM tenant_collections WHERE tenant_id = %s AND slug = %s",
        [tenant_id, slug],
    )
    if existing:
        return jsonify({"error": f"Collection '{name}' already exists"}), 409

    coll_id = insert_returning(
        """INSERT INTO tenant_collections (tenant_id, name, slug, description, created_by)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, name, slug, data.get("description", ""), user["id"]],
    )
    return jsonify({"id": coll_id, "name": name, "slug": slug}), 201


@kb_bp.route("/collections/<int:collection_id>", methods=["DELETE"])
@login_required
@require_permission("kb.manage")
def delete_collection(collection_id: int):
    """Delete a collection. Articles inside are soft-deleted (unpublished)."""
    tenant_id = get_tenant_id()
    coll = fetch_one(
        "SELECT id FROM tenant_collections WHERE id = %s AND tenant_id = %s",
        [collection_id, tenant_id],
    )
    if not coll:
        return jsonify({"error": "Not found"}), 404

    # Soft-delete articles inside
    execute(
        "UPDATE documents SET is_published = false, updated_at = now() WHERE tenant_collection_id = %s",
        [collection_id],
    )
    execute("DELETE FROM tenant_collections WHERE id = %s", [collection_id])
    return jsonify({"ok": True})


# ============================================================
# Tenant Articles CRUD
# ============================================================

@kb_bp.route("/articles", methods=["GET"])
@login_required
def list_articles():
    """List tenant's own articles, optionally filtered by collection."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify([])

    collection_slug = request.args.get("collection")
    conditions = ["d.tenant_id = %s", "d.module_id IS NULL", "d.is_published = true"]
    params: list = [tenant_id]

    if collection_slug:
        conditions.append("d.tenant_collection_id = (SELECT id FROM tenant_collections WHERE tenant_id = %s AND slug = %s)")
        params.extend([tenant_id, collection_slug])

    where = " AND ".join(conditions)
    articles = fetch_all(
        f"""SELECT d.id, d.title, d.is_published, d.created_at, d.updated_at,
                  u.name as author_name, length(d.content) as content_length,
                  d.source_file_name, d.source_file_type, d.file_size,
                  d.tenant_collection_id,
                  tc.name as collection_name
           FROM documents d
           LEFT JOIN users u ON u.id = d.created_by
           LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
           WHERE {where}
           ORDER BY d.updated_at DESC""",
        params,
    )
    return jsonify(articles)


@kb_bp.route("/articles", methods=["POST"])
@login_required
@require_permission("kb.manage")
def create_article():
    """Create a tenant-owned KB article and embed it for RAG."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400

    content = data.get("content", "")
    collection_id = data.get("collection_id")
    article_id = insert_returning(
        """INSERT INTO documents (tenant_id, title, content, is_published, created_by, tenant_collection_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        [tenant_id, title, content, data.get("is_published", True), user["id"], collection_id],
    )

    # Update collection doc count if assigned
    if collection_id:
        execute(
            """UPDATE tenant_collections
               SET doc_count = (SELECT COUNT(*) FROM documents WHERE tenant_collection_id = %s AND is_published = true)
               WHERE id = %s""",
            [collection_id, collection_id],
        )

    # Embed for RAG in background (if there's content worth chunking)
    if content.strip():
        from services.pipeline_service import start_tenant_article_embed
        start_tenant_article_embed(article_id, tenant_id)

    return jsonify({"id": article_id}), 201


@kb_bp.route("/articles/upload", methods=["POST"])
@login_required
@require_permission("kb.manage")
def upload_articles():
    """Bulk upload document files (PDF, DOCX, TXT) into a tenant collection.

    Accepts multipart form with:
      - files: one or more document files (required, up to 30)
      - collection_id: target collection ID (required)

    Each file's name (sans extension) becomes the article title.
    All articles are embedded for RAG in the background.
    """
    import os as _os
    user = get_current_user()
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 400

    collection_slug = request.form.get("collection")
    if not collection_slug:
        return jsonify({"error": "collection slug is required"}), 400

    # Verify collection belongs to tenant
    coll = fetch_one(
        "SELECT id FROM tenant_collections WHERE slug = %s AND tenant_id = %s",
        [collection_slug, tenant_id],
    )
    if not coll:
        return jsonify({"error": "Collection not found"}), 404
    collection_id = coll["id"]

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400
    if len(files) > 30:
        return jsonify({"error": "Maximum 30 files per upload"}), 400

    from services.file_parser_service import validate_file, extract_text, ParseError
    from services.pipeline_service import start_tenant_article_embed

    mime_map = {
        ".txt": "text/plain",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    results = []
    succeeded = 0

    for file in files:
        filename = file.filename or ""
        if not filename:
            results.append({"file": "(empty)", "error": "No filename"})
            continue

        file_bytes = file.read()
        file_size = len(file_bytes)

        # Validate
        try:
            ext = validate_file(filename, file_size)
        except ParseError as e:
            results.append({"file": filename, "error": str(e)})
            continue

        # Extract text
        try:
            content = extract_text(file_bytes, filename)
        except ParseError as e:
            results.append({"file": filename, "error": str(e)})
            continue

        title = _os.path.splitext(filename)[0]
        file_type = mime_map.get(ext, "application/octet-stream")

        # Insert document into collection
        article_id = insert_returning(
            """INSERT INTO documents
               (tenant_id, title, content, is_published, created_by,
                source_file_name, source_file_type, file_size, tenant_collection_id)
               VALUES (%s, %s, %s, true, %s, %s, %s, %s, %s) RETURNING id""",
            [tenant_id, title, content, user["id"], filename, file_type, file_size, int(collection_id)],
        )

        # Embed in background
        start_tenant_article_embed(article_id, tenant_id)

        results.append({
            "file": filename,
            "id": article_id,
            "title": title,
            "content_length": len(content),
        })
        succeeded += 1

    # Update doc count on collection
    execute(
        """UPDATE tenant_collections
           SET doc_count = (SELECT COUNT(*) FROM documents WHERE tenant_collection_id = %s AND is_published = true)
           WHERE id = %s""",
        [int(collection_id), int(collection_id)],
    )

    return jsonify({
        "collection_id": int(collection_id),
        "uploaded": succeeded,
        "errors": len(files) - succeeded,
        "results": results,
    }), 201


@kb_bp.route("/articles/<int:article_id>", methods=["GET"])
@login_required
def get_article(article_id: int):
    """Get a single tenant article."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    if user["role"] == "super_admin":
        article = fetch_one(
            "SELECT * FROM documents WHERE id = %s AND module_id IS NULL", [article_id]
        )
    else:
        article = fetch_one(
            "SELECT * FROM documents WHERE id = %s AND tenant_id = %s AND module_id IS NULL",
            [article_id, tenant_id],
        )

    if not article:
        return jsonify({"error": "Not found"}), 404
    return jsonify(article)


@kb_bp.route("/articles/<int:article_id>", methods=["PUT"])
@login_required
@require_permission("kb.manage")
def update_article(article_id: int):
    """Update a tenant-owned KB article."""
    user = get_current_user()
    tenant_id = get_tenant_id()
    data = request.json or {}

    # Verify ownership
    if user["role"] == "super_admin":
        existing = fetch_one("SELECT id FROM documents WHERE id = %s AND module_id IS NULL", [article_id])
    else:
        existing = fetch_one(
            "SELECT id FROM documents WHERE id = %s AND tenant_id = %s AND module_id IS NULL",
            [article_id, tenant_id],
        )
    if not existing:
        return jsonify({"error": "Not found"}), 404

    fields, params = [], []
    for col in ("title", "content", "is_published"):
        if col in data:
            fields.append(f"{col} = %s")
            params.append(data[col])

    # Handle collection reassignment
    old_collection_id = None
    new_collection_id = None
    if "tenant_collection_id" in data:
        old_doc = fetch_one("SELECT tenant_collection_id FROM documents WHERE id = %s", [article_id])
        old_collection_id = old_doc["tenant_collection_id"] if old_doc else None
        new_collection_id = data["tenant_collection_id"]  # can be None to un-assign
        fields.append("tenant_collection_id = %s")
        params.append(new_collection_id)

    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    fields.append("updated_at = now()")
    params.append(article_id)
    execute(f"UPDATE documents SET {', '.join(fields)} WHERE id = %s", params)

    # Update doc_count on old and new collections
    if "tenant_collection_id" in data and old_collection_id != new_collection_id:
        if old_collection_id:
            execute(
                "UPDATE tenant_collections SET doc_count = GREATEST(0, (SELECT COUNT(*) FROM documents WHERE tenant_collection_id = %s)) WHERE id = %s",
                [old_collection_id, old_collection_id],
            )
        if new_collection_id:
            execute(
                "UPDATE tenant_collections SET doc_count = (SELECT COUNT(*) FROM documents WHERE tenant_collection_id = %s) WHERE id = %s",
                [new_collection_id, new_collection_id],
            )

    # Re-embed if content changed
    if "content" in data and data["content"].strip() and tenant_id:
        from services.pipeline_service import start_tenant_article_embed
        start_tenant_article_embed(article_id, tenant_id)

    return jsonify({"ok": True})


@kb_bp.route("/articles/<int:article_id>", methods=["DELETE"])
@login_required
@require_permission("kb.manage")
def delete_article(article_id: int):
    """Soft-delete: set is_published = false and update collection doc_count."""
    user = get_current_user()
    tenant_id = get_tenant_id()

    # Look up the article first to get its collection_id for doc_count update
    if user["role"] == "super_admin":
        article = fetch_one(
            "SELECT id, tenant_collection_id FROM documents WHERE id = %s AND module_id IS NULL",
            [article_id],
        )
    else:
        article = fetch_one(
            "SELECT id, tenant_collection_id FROM documents WHERE id = %s AND tenant_id = %s AND module_id IS NULL",
            [article_id, tenant_id],
        )

    if not article:
        return jsonify({"error": "Not found"}), 404

    # Soft-delete
    if user["role"] == "super_admin":
        execute("UPDATE documents SET is_published = false, updated_at = now() WHERE id = %s AND module_id IS NULL", [article_id])
    else:
        execute(
            "UPDATE documents SET is_published = false, updated_at = now() WHERE id = %s AND tenant_id = %s AND module_id IS NULL",
            [article_id, tenant_id],
        )

    # Update collection doc_count if article belonged to a collection
    collection_id = article.get("tenant_collection_id")
    if collection_id:
        execute(
            """UPDATE tenant_collections
               SET doc_count = (SELECT COUNT(*) FROM documents WHERE tenant_collection_id = %s AND is_published = true)
               WHERE id = %s""",
            [collection_id, collection_id],
        )

    return jsonify({"ok": True})


# ============================================================
# Upload History
# ============================================================

@kb_bp.route("/upload-history", methods=["GET"])
@login_required
@require_permission("kb.manage")
def get_upload_history():
    """Return upload audit trail: documents with source_file_name, uploader, chunk counts."""
    tenant_id = get_tenant_id()
    if not tenant_id:
        return jsonify([])

    collection_id = request.args.get("collection_id")

    conditions = ["d.tenant_id = %s", "d.source_file_name IS NOT NULL"]
    params: list = [tenant_id]

    if collection_id:
        conditions.append("d.tenant_collection_id = %s")
        params.append(int(collection_id))

    where = " AND ".join(conditions)

    rows = fetch_all(
        f"""SELECT
               d.id,
               d.title,
               d.source_file_name,
               d.source_file_type,
               d.file_size,
               d.created_at,
               u.name AS uploader_name,
               tc.name AS collection_name,
               (SELECT COUNT(*) FROM document_chunks dc WHERE dc.document_id = d.id) AS chunk_count
           FROM documents d
           LEFT JOIN users u ON u.id = d.created_by
           LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
           WHERE {where}
           ORDER BY d.created_at DESC""",
        params,
    )

    # Add has_embeddings boolean
    for row in rows:
        row["has_embeddings"] = (row.get("chunk_count") or 0) > 0

    return jsonify(rows)
