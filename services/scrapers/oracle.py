"""Oracle documentation scraper (Oracle Help Center static HTML).

Shared scraper for Oracle MICROS products. Crawls table-of-contents pages
to discover all chapter/section .htm URLs, then fetches content.

Products registered as separate modules:
  - oracle_simphony — MICROS Simphony POS (~174 pages)
  - oracle_xstore — MICROS Xstore POS (~500+ pages)
"""

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from services.scrapers import register, write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

REQUEST_DELAY = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _discover_pages_from_toc(session: requests.Session, toc_url: str) -> list[str]:
    """Fetch a TOC page and extract all linked .htm page URLs."""
    try:
        resp = session.get(toc_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch TOC %s: %s", toc_url, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Only .htm links, skip anchors and external
        if not href.endswith(".htm") and ".htm#" not in href:
            continue
        # Strip anchors
        href = href.split("#")[0]
        full_url = urljoin(toc_url, href)
        if full_url not in seen and full_url != toc_url:
            seen.add(full_url)
            urls.append(full_url)

    return urls


def _extract_content(session: requests.Session, url: str) -> tuple[str, str] | None:
    """Fetch a page and extract (title, content)."""
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

    # Remove noise
    for tag in soup.find_all([
        "script", "style", "nav", "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    # Oracle docs use <div class="ind"> for main content, or <body> directly
    main = (
        soup.find("div", class_="ind")
        or soup.find("div", id="CONTENT")
        or soup.find("article")
        or soup.find("main")
        or soup.find("body")
    )
    if not main:
        return None

    content = html_to_text(main)
    if not content or len(content) < 50:
        return None

    return title or "Untitled", content


def scrape_oracle(toc_url: str, output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape all pages from an Oracle docs TOC.

    Args:
        toc_url: URL of the table-of-contents page (toc.htm or index.html)
        output_dir: Directory to write .txt files into
        max_docs: Stop after this many saved documents (None = unlimited).

    Returns:
        Stats dict: {"saved", "skipped", "errors", "total"}
    """
    session = _get_session()
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    # Phase 1: Discover pages from TOC
    logger.info("Phase 1: Discovering pages from TOC %s", toc_url)
    page_urls = _discover_pages_from_toc(session, toc_url)
    stats["total"] = len(page_urls)
    logger.info("Found %d pages in TOC", len(page_urls))

    # Phase 2: Scrape each page
    logger.info("Phase 2: Scraping %d pages...", len(page_urls))
    for url in page_urls:
        if max_docs is not None and stats["saved"] >= max_docs:
            logger.info("oracle (%s): reached max_docs=%d, stopping", toc_url, max_docs)
            break
        try:
            slug = url.rstrip("/").split("/")[-1].replace(".htm", "")
            filename = sanitize_filename(slug) + ".txt"

            if (output_dir / filename).exists():
                stats["skipped"] += 1
                continue

            result = _extract_content(session, url)
            if result is None:
                stats["errors"] += 1
                continue

            title, content = result
            written = write_document(output_dir, filename, url, title, content)
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
            logger.warning("Error processing %s: %s", url, e)
            stats["errors"] += 1

    logger.info(
        "Oracle scrape complete (%s): saved=%d, skipped=%d, errors=%d, total=%d",
        toc_url, stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats


# -- Registered scrapers --

SIMPHONY_TOC = "https://docs.oracle.com/cd/F32325_01/doc.192/f32329/toc.htm"
XSTORE_DOCLIST = "https://docs.oracle.com/cd/E62106_01/xpos/doclist.html"
XSTORE_BASE = "https://docs.oracle.com/cd/E62106_01/xpos/"


@register("oracle_simphony")
def run_simphony(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Oracle MICROS Simphony POS documentation."""
    logger.info("Starting Oracle Simphony scraper from %s", SIMPHONY_TOC)
    return scrape_oracle(SIMPHONY_TOC, output_dir, max_docs=max_docs)


@register("oracle_xstore")
def run_xstore(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Oracle MICROS Xstore POS documentation.

    The index.html is a frameset — the actual doc listing is in doclist.html.
    We parse doclist.html for HTML TOC links (toc.htm), then scrape each guide.

    When max_docs is set, the cap is applied across all Xstore guides combined:
    each guide receives a remaining budget (max_docs - combined["saved"]) so the
    total across all guides does not exceed max_docs.
    """
    logger.info("Starting Oracle Xstore scraper from %s", XSTORE_DOCLIST)
    session = _get_session()
    try:
        resp = session.get(XSTORE_DOCLIST, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch Xstore doclist: %s", e)
        return {"saved": 0, "skipped": 0, "errors": 1, "total": 0}

    # Find all HTML TOC links from the doclist page
    soup = BeautifulSoup(resp.text, "html.parser")
    combined = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    toc_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Look for HTML guide TOC links (e.g., pdf/200/html/pos_user_guide/toc.htm)
        if "toc.htm" in href and "html/" in href:
            full_url = urljoin(XSTORE_DOCLIST, href)
            toc_urls.add(full_url)

    logger.info("Found %d HTML guide TOCs for Xstore", len(toc_urls))

    if not toc_urls:
        # Fallback: try to find any .htm links directly
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.endswith(".htm") and "html/" in href:
                full_url = urljoin(XSTORE_DOCLIST, href)
                toc_urls.add(full_url)
        logger.info("Fallback: found %d .htm links", len(toc_urls))

    for toc_url in sorted(toc_urls):
        # Compute remaining budget so the cap is global across all guides
        remaining = None
        if max_docs is not None:
            remaining = max_docs - combined["saved"]
            if remaining <= 0:
                logger.info("oracle_xstore: reached max_docs=%d, skipping remaining guides", max_docs)
                break
        logger.info("Scraping Xstore guide: %s", toc_url)
        stats = scrape_oracle(toc_url, output_dir, max_docs=remaining)
        for key in combined:
            combined[key] += stats.get(key, 0)

    return combined
