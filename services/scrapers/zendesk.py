"""Shared Zendesk Help Center API scraper.

Reusable function for any Zendesk-based knowledge module.
Uses the public /api/v2/help_center/ REST API (no auth required).

Both Olo (olosupport.zendesk.com) and Rockbot (support.rockbot.com)
are thin wrappers around scrape_zendesk().
"""

import logging
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from services.scrapers import write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

REQUEST_DELAY = 0.5  # seconds between paginated API requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _fetch_paginated(session: requests.Session, url: str, key: str) -> list[dict]:
    """Fetch all pages from a Zendesk paginated API endpoint.

    Args:
        session: requests session with headers set
        url: initial API URL (e.g. .../articles.json?per_page=100)
        key: JSON key containing the results (e.g. "articles", "sections")

    Returns:
        Combined list of all items across all pages.
    """
    items = []
    next_url = url

    while next_url:
        try:
            resp = session.get(next_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("Zendesk API error fetching %s: %s", next_url, e)
            break

        page_items = data.get(key, [])
        items.extend(page_items)
        next_url = data.get("next_page")

        if next_url:
            time.sleep(REQUEST_DELAY)

    return items


def _build_taxonomy(session: requests.Session, base_url: str) -> dict[int, str]:
    """Fetch categories and sections, return section_id -> "Category > Section" map."""
    # Fetch categories
    cat_url = f"{base_url}/api/v2/help_center/en-us/categories.json"
    categories = _fetch_paginated(session, cat_url, "categories")
    cat_map = {c["id"]: c["name"] for c in categories}
    logger.info("  Fetched %d categories", len(cat_map))

    # Fetch sections
    sec_url = f"{base_url}/api/v2/help_center/en-us/sections.json"
    sections = _fetch_paginated(session, sec_url, "sections")

    # Build section_id -> "Category > Section" label
    taxonomy = {}
    for s in sections:
        cat_name = cat_map.get(s.get("category_id"), "")
        sec_name = s.get("name", "")
        if cat_name and sec_name:
            taxonomy[s["id"]] = f"{cat_name} > {sec_name}"
        elif sec_name:
            taxonomy[s["id"]] = sec_name

    logger.info("  Fetched %d sections", len(taxonomy))
    return taxonomy


def scrape_zendesk(base_url: str, output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape all articles from a Zendesk Help Center via public API.

    Args:
        base_url: Zendesk instance URL (e.g. "https://olosupport.zendesk.com")
        output_dir: Directory to write .txt files into
        max_docs: Stop after this many saved documents (None = unlimited).

    Returns:
        Stats dict: {"saved", "skipped", "errors", "total"}
    """
    session = _get_session()
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    # Phase 1: Build taxonomy for category/section context
    logger.info("Phase 1: Fetching taxonomy from %s", base_url)
    try:
        taxonomy = _build_taxonomy(session, base_url)
    except Exception as e:
        logger.warning("Failed to build taxonomy: %s (continuing without)", e)
        taxonomy = {}

    # Phase 2: Fetch all articles
    logger.info("Phase 2: Fetching articles...")
    articles_url = f"{base_url}/api/v2/help_center/en-us/articles.json?per_page=100"
    articles = _fetch_paginated(session, articles_url, "articles")
    stats["total"] = len(articles)
    logger.info("  Found %d articles", len(articles))

    # Phase 3: Process each article
    logger.info("Phase 3: Processing %d articles...", len(articles))
    for article in articles:
        if max_docs is not None and stats["saved"] >= max_docs:
            logger.info("zendesk (%s): reached max_docs=%d, stopping", base_url, max_docs)
            break
        try:
            # Skip drafts
            if article.get("draft", False):
                stats["skipped"] += 1
                continue

            title = article.get("title", "").strip()
            body_html = article.get("body") or ""
            source_url = article.get("html_url", "")
            section_id = article.get("section_id")

            if not title or not body_html.strip():
                stats["skipped"] += 1
                continue

            # Convert HTML body to text
            body_soup = BeautifulSoup(body_html, "html.parser")

            # Remove noise elements
            for tag in body_soup.find_all([
                "script", "style", "nav", "footer", "noscript", "svg", "iframe",
            ]):
                tag.decompose()

            content = html_to_text(body_soup)
            if not content or len(content) < 50:
                stats["skipped"] += 1
                continue

            # Prepend category/section context for RAG quality
            section_label = taxonomy.get(section_id, "")
            if section_label:
                content = f"Category: {section_label}\n\n{content}"

            # Write file
            filename = sanitize_filename(title) + ".txt"
            written = write_document(output_dir, filename, source_url, title, content)

            if written:
                stats["saved"] += 1
                if stats["saved"] % 20 == 0 or stats["saved"] <= 3:
                    logger.info(
                        "  [%d/%d] Saved: %s (%d chars)",
                        stats["saved"], stats["total"], filename, len(content),
                    )
            else:
                stats["skipped"] += 1

        except Exception as e:
            logger.warning("Error processing article '%s': %s", article.get("title", "?"), e)
            stats["errors"] += 1

    logger.info(
        "Zendesk scrape complete (%s): saved=%d, skipped=%d, errors=%d, total=%d",
        base_url, stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats
