"""Shared Intercom help center scraper.

Reusable function for any Intercom-based knowledge module.
Uses __NEXT_DATA__ JSON extraction with DOM fallback.

Paytronix (help-paytronix.theaccessgroup.com) is a thin wrapper around scrape_intercom().
(Solink has its own private Intercom implementation — historical; not a wrapper.)
"""

import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from services.scrapers import write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _discover_collections(session: requests.Session, base_url: str, home_url: str) -> list[str]:
    """Fetch homepage and extract collection URLs."""
    resp = session.get(home_url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    collections = []

    # Try __NEXT_DATA__ first (Intercom embeds collections as JSON)
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.string)
            props = data.get("props", {}).get("pageProps", {})
            for coll in props.get("collections", []):
                slug = coll.get("slug", "")
                if slug:
                    url = f"{base_url}/en/collections/{slug}"
                    if url not in collections:
                        collections.append(url)
        except (json.JSONDecodeError, KeyError):
            pass

    # DOM fallback: Intercom collection links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/en/collections/" in href:
            url = href if href.startswith("http") else base_url + href
            url = url.split("?")[0]
            if url not in collections:
                collections.append(url)

    return collections


def _discover_articles(session: requests.Session, base_url: str, collection_url: str) -> list[str]:
    """Fetch a collection page and extract article URLs."""
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.get(collection_url, timeout=30)
        if resp.status_code != 200:
            return []
    except requests.RequestException as e:
        logger.warning("Failed to fetch collection %s: %s", collection_url, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []

    # Try __NEXT_DATA__ first
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.string)
            props = data.get("props", {}).get("pageProps", {})
            for section in props.get("sections", []):
                for article in section.get("articles", []):
                    slug = article.get("slug", "")
                    aid = article.get("id", "")
                    if slug and aid:
                        url = f"{base_url}/en/articles/{aid}-{slug}"
                        if url not in articles:
                            articles.append(url)
                    elif slug:
                        url = f"{base_url}/en/articles/{slug}"
                        if url not in articles:
                            articles.append(url)
            for article in props.get("articles", []):
                slug = article.get("slug", "")
                aid = article.get("id", "")
                if slug and aid:
                    url = f"{base_url}/en/articles/{aid}-{slug}"
                    if url not in articles:
                        articles.append(url)
        except (json.JSONDecodeError, KeyError):
            pass

    # DOM fallback
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/en/articles/" in href:
            url = href if href.startswith("http") else base_url + href
            url = url.split("?")[0]
            if url not in articles:
                articles.append(url)

    return articles


def _extract_article(
    session: requests.Session, base_url: str, article_url: str,
) -> tuple[str, str] | None:
    """Fetch an article and extract (title, content). Returns None on failure."""
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.get(article_url, timeout=30)
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try __NEXT_DATA__ first (Intercom embeds article body as HTML in JSON)
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.string)
            props = data.get("props", {}).get("pageProps", {})
            article = props.get("article", {})
            title = article.get("title", "")
            body_html = article.get("body", "")
            if body_html:
                body_soup = BeautifulSoup(body_html, "html.parser")
                content = html_to_text(body_soup)
                if content and len(content) >= 50:
                    return title, content
        except (json.JSONDecodeError, KeyError):
            pass

    # DOM fallback
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" | ")[0].strip()

    for tag in soup.find_all([
        "script", "style", "nav", "footer", "header",
        "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"article|content|body", re.I))
    )
    if not main:
        return None

    content = html_to_text(main)
    if not content or len(content) < 50:
        return None

    return title or "Untitled", content


def scrape_intercom(base_url: str, output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape all articles from an Intercom help center.

    Args:
        base_url: Intercom instance URL (e.g. "https://help.solink.com")
        output_dir: Directory to write .txt files into
        max_docs: Stop after this many saved documents (None = unlimited).

    Returns:
        Stats dict: {"saved", "skipped", "errors", "total"}
    """
    session = _get_session()
    home_url = f"{base_url}/en/"
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    # Phase 1: Discover collections
    logger.info("Phase 1: Discovering collections from %s", home_url)
    collections = _discover_collections(session, base_url, home_url)
    logger.info("Found %d collections", len(collections))

    # Phase 2: Discover articles from all collections
    logger.info("Phase 2: Discovering articles...")
    article_urls = []
    seen = set()
    for coll_url in collections:
        articles = _discover_articles(session, base_url, coll_url)
        for url in articles:
            if url not in seen:
                seen.add(url)
                article_urls.append(url)
    logger.info("Found %d unique articles", len(article_urls))
    stats["total"] = len(article_urls)

    # Phase 3: Scrape each article
    logger.info("Phase 3: Scraping %d articles...", len(article_urls))
    for i, url in enumerate(article_urls, 1):
        if max_docs is not None and stats["saved"] >= max_docs:
            logger.info("intercom (%s): reached max_docs=%d, stopping", base_url, max_docs)
            break
        slug = url.rstrip("/").split("/")[-1]
        filename = sanitize_filename(slug) + ".txt"

        if (output_dir / filename).exists():
            stats["skipped"] += 1
            continue

        result = _extract_article(session, base_url, url)
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

    logger.info(
        "Intercom scrape complete (%s): saved=%d, skipped=%d, errors=%d, total=%d",
        base_url, stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats
