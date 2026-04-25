"""CLI management tool for helpdesk operations.

Usage:
    python manage.py ingest <module_slug>
    python manage.py pipeline <module_slug> [--force]
    python manage.py search <query>
"""

import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from config import Config


def cmd_ingest(module_slug: str):
    """Ingest documents from documents/<module_slug>/ into the DB."""
    from models.db import init_pool
    init_pool()

    from services.ingestion_service import ingest_directory

    directory = os.path.join(Config.DOCUMENTS_DIR, module_slug)
    if not os.path.isdir(directory):
        print(f"Error: directory not found: {directory}")
        sys.exit(1)

    txt_count = len([f for f in os.listdir(directory) if f.endswith(".txt")])
    print(f"Ingesting {txt_count} .txt files from {directory} into module '{module_slug}'...")

    result = ingest_directory(directory, module_slug)

    if result.get("error"):
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"Done: {result['ingested']} ingested, {result['skipped']} skipped, {result['errors']} errors")


def cmd_pipeline(module_slug: str, force: bool = False):
    """Run the KB pipeline: clean → chunk → embed."""
    from models.db import init_pool
    init_pool()

    from services.pipeline_service import run_pipeline

    print(f"Running pipeline for '{module_slug}'{' (force rebuild)' if force else ''}...")
    stats = run_pipeline(module_slug, force=force, verbose=True)

    if stats.get("error"):
        print(f"Error: {stats['error']}")
        sys.exit(1)

    print(f"\nPipeline complete:")
    print(f"  Documents processed: {stats['documents_processed']}")
    print(f"  Chunks created:      {stats['chunks_created']}")
    print(f"  Chunks skipped:      {stats['chunks_skipped']}")
    print(f"  Chunks deleted:      {stats['chunks_deleted']}")
    print(f"  Errors:              {stats['errors']}")


def cmd_search(query: str):
    """Debug search: embed query and show top-5 similar chunks."""
    from models.db import init_pool, fetch_all
    init_pool()

    from services.embedding_service import embed_single

    print(f"Searching for: {query}\n")

    query_embedding = embed_single(query)

    results = fetch_all(
        """SELECT dc.id, dc.document_id, dc.content, dc.token_count, dc.metadata,
                  d.title, d.source_url,
                  1 - (dc.embedding <=> %s::vector) as similarity
           FROM document_chunks dc
           JOIN documents d ON d.id = dc.document_id
           ORDER BY dc.embedding <=> %s::vector
           LIMIT 5""",
        [str(query_embedding), str(query_embedding)],
    )

    if not results:
        print("No results found. Have you run the pipeline?")
        return

    for i, r in enumerate(results, 1):
        sim = r.get("similarity", 0)
        title = r.get("title", "Unknown")
        content_preview = (r.get("content") or "")[:200].replace("\n", " ")
        print(f"  {i}. [{sim:.4f}] {title}")
        print(f"     {content_preview}...")
        print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "ingest":
        if len(sys.argv) < 3:
            print("Usage: python manage.py ingest <module_slug>")
            sys.exit(1)
        cmd_ingest(sys.argv[2])

    elif command == "pipeline":
        if len(sys.argv) < 3:
            print("Usage: python manage.py pipeline <module_slug> [--force]")
            sys.exit(1)
        force = "--force" in sys.argv
        cmd_pipeline(sys.argv[2], force)

    elif command == "search":
        if len(sys.argv) < 3:
            print("Usage: python manage.py search <query>")
            sys.exit(1)
        query = " ".join(sys.argv[2:])
        cmd_search(query)

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
