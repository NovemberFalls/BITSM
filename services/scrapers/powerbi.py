"""Power BI documentation scraper.

Downloads the MicrosoftDocs/powerbi-docs GitHub repo as a tarball
and processes all .md files into the standard document format.

No git dependency — uses GitHub's tarball API (works in slim Docker images).
~700-1,000 markdown files.
"""

import io
import logging
import re
import tarfile
from pathlib import Path

import requests

from services.scrapers import register, write_document, sanitize_filename

logger = logging.getLogger(__name__)

REPO_TARBALL_URL = (
    "https://github.com/MicrosoftDocs/powerbi-docs/archive/refs/heads/main.tar.gz"
)
DOCS_BASE_URL = "https://learn.microsoft.com/en-us/power-bi"

# Directories to skip inside the docs folder
SKIP_DIRS = frozenset({
    "media", "includes", "breadcrumb", ".github", "contributor-guide",
})

HEADERS = {"User-Agent": "helpdesk-scraper/1.0"}


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown file.

    Uses simple regex — no pyyaml dependency needed.
    Returns (metadata_dict, body_text).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]

    meta = {}
    for line in fm_text.split("\n"):
        m = re.match(r"^(\w[\w.-]*)\s*:\s*(.+)", line)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip().strip('"').strip("'")
            meta[key] = value

    return meta, body


def _build_source_url(relative_path: str) -> str:
    """Build learn.microsoft.com URL from relative file path.

    e.g. "fundamentals/desktop-getting-started.md"
      -> "https://learn.microsoft.com/en-us/power-bi/fundamentals/desktop-getting-started"
    """
    path = relative_path.removesuffix(".md")
    if path.endswith("/index"):
        path = path.removesuffix("/index")
    return f"{DOCS_BASE_URL}/{path}"


@register("powerbi")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Download and process Power BI docs from GitHub tarball.

    max_docs limits the number of markdown files written to disk.
    Note: because powerbi has no separate discovery phase (the tarball
    is enumerated and written in one pass), stats["total"] will reflect
    only the number of eligible .md members enumerated before the cap
    trips, not the full tarball count.
    """
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    # Download tarball
    logger.info("Downloading Power BI docs tarball from GitHub...")
    resp = requests.get(REPO_TARBALL_URL, headers=HEADERS, timeout=120, stream=True)
    resp.raise_for_status()
    logger.info("Downloaded %.1f MB", len(resp.content) / 1_048_576)

    tarball_bytes = io.BytesIO(resp.content)

    with tarfile.open(fileobj=tarball_bytes, mode="r:gz") as tar:
        members = tar.getmembers()

        # Find the docs root prefix (e.g., "powerbi-docs-main/powerbi-docs/")
        docs_prefix = None
        for member in members:
            if (
                member.isfile()
                and member.name.endswith(".md")
                and "/powerbi-docs/" in member.name
            ):
                parts = member.name.split("/powerbi-docs/", 1)
                docs_prefix = parts[0] + "/powerbi-docs/"
                break

        if not docs_prefix:
            logger.warning(
                "Could not find powerbi-docs/ in tarball, "
                "processing all .md files"
            )
            docs_prefix = ""

        logger.info("Docs prefix: %s", docs_prefix)

        for member in members:
            if not member.isfile() or not member.name.endswith(".md"):
                continue

            if docs_prefix and not member.name.startswith(docs_prefix):
                continue

            # Relative path within docs dir
            relative_path = member.name[len(docs_prefix):]

            # Skip excluded directories
            first_dir = relative_path.split("/")[0] if "/" in relative_path else ""
            if first_dir in SKIP_DIRS:
                continue

            # Skip TOC and config files
            basename = relative_path.split("/")[-1]
            if basename.lower().startswith("toc.") or basename == "index.yml":
                continue

            stats["total"] += 1

            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("powerbi: reached max_docs=%d, stopping", max_docs)
                break

            # Build output filename
            safe_name = sanitize_filename(
                relative_path.replace("/", "_").removesuffix(".md"),
            )
            filename = f"{safe_name}.txt"

            if (output_dir / filename).exists():
                stats["skipped"] += 1
                continue

            # Extract and parse the markdown file
            try:
                f = tar.extractfile(member)
                if f is None:
                    stats["errors"] += 1
                    continue
                raw = f.read()
                content = raw.decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning("Error reading %s: %s", member.name, e)
                stats["errors"] += 1
                continue

            meta, body = _parse_frontmatter(content)
            title = (
                meta.get("title", "")
                or meta.get("Title", "")
                or basename.removesuffix(".md")
            )

            body = body.strip()
            if not body or len(body) < 50:
                stats["skipped"] += 1
                continue

            source_url = _build_source_url(relative_path)

            written = write_document(output_dir, filename, source_url, title, body)
            if written:
                stats["saved"] += 1
                if stats["saved"] % 100 == 0 or stats["saved"] <= 3:
                    logger.info("  [%d] Saved: %s", stats["saved"], filename)
            else:
                stats["skipped"] += 1

    logger.info(
        "Power BI scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats
