"""Shared Salesforce Experience Cloud knowledge base scraper.

Salesforce Experience Cloud sites are SPAs (Lightning Web Components / Aura)
that require JavaScript rendering to access article content. Uses Playwright
for headless Chromium rendering with sitemap-based article discovery.

Strategy:
  1. Fetch sitemap XML → extract article URLs (filter by path pattern)
  2. Launch headless Chromium via Playwright
  3. Render each article page, wait for content selector
  4. Extract rendered HTML → convert to structured text
  5. Save as .txt files in standard format

Used by: billcom.py

Requires: pip install playwright && playwright install chromium
"""

import asyncio
import logging
import re
from pathlib import Path
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from services.scrapers import write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.5  # seconds between page renders (respectful)
CONCURRENT = 5       # parallel browser pages
PAGE_TIMEOUT = 20_000  # 20 seconds per page load

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Salesforce Knowledge article content selectors (ordered by specificity)
DEFAULT_CONTENT_SELECTORS = [
    ".slds-rich-text-editor__output",    # Standard Knowledge rich text body
    ".uiOutputRichText",                 # Aura rich text component
    ".forceCommunityArticleBody",        # Community article body
    ".cKnowledgeArticleView",            # Knowledge article container
    "article",                           # Generic article element
    ".content-body",                     # Some communities use this
    "main",                              # Last resort
]


def _fetch_sitemap_urls(
    sitemap_url: str,
    path_filter: str | None = None,
) -> list[str]:
    """Fetch sitemap XML and extract article URLs.

    Args:
        sitemap_url: URL of the sitemap XML file
        path_filter: Only include URLs containing this string (e.g. "/s/article/")

    Returns:
        List of article URLs found in the sitemap.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(sitemap_url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch sitemap %s: %s", sitemap_url, e)
        return []

    xml_text = resp.text
    urls = []

    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            url = (loc.text or "").strip()
            if path_filter and path_filter not in url:
                continue
            urls.append(url)
    except ElementTree.ParseError:
        # Regex fallback for malformed XML
        for m in re.finditer(r"<loc>([^<]+)</loc>", xml_text):
            url = m.group(1).strip()
            if path_filter and path_filter not in url:
                continue
            urls.append(url)

    return urls


async def _scrape_articles_async(
    article_urls: list[str],
    output_dir: Path,
    content_selectors: list[str] | None = None,
    max_docs: int | None = None,
) -> dict:
    """Render Salesforce pages with Playwright and extract content.

    Args:
        article_urls: List of article URLs to scrape
        output_dir: Directory to write .txt files into
        content_selectors: CSS selectors to find article content
        max_docs: Stop after this many saved documents (None = unlimited).

    Returns:
        Stats dict.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is required for Salesforce Experience Cloud scrapers. "
            "Install: pip install playwright && playwright install chromium"
        )

    selectors = content_selectors or DEFAULT_CONTENT_SELECTORS
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": len(article_urls)}
    semaphore = asyncio.Semaphore(CONCURRENT)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )

        async def process_url(url: str) -> tuple[str, str | None, str | None]:
            """Render a single page and extract title + content HTML."""
            async with semaphore:
                await asyncio.sleep(REQUEST_DELAY)
                page = await context.new_page()
                try:
                    await page.goto(
                        url, wait_until="networkidle", timeout=PAGE_TIMEOUT,
                    )

                    # Wait for any content selector to appear
                    content_el = None
                    for sel in selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=8_000)
                            content_el = await page.query_selector(sel)
                            if content_el:
                                break
                        except Exception:
                            continue

                    if not content_el:
                        logger.warning("No content found for %s", url)
                        return url, None, None

                    # Extract title
                    title = ""
                    title_el = await page.query_selector("h1")
                    if title_el:
                        title = (await title_el.inner_text()).strip()
                    if not title:
                        title = await page.title()
                        # Clean up common Salesforce title patterns
                        title = title.split(" - ")[0].strip()

                    # Extract rendered HTML of content area
                    content_html = await content_el.inner_html()
                    return url, title, content_html

                except Exception as e:
                    logger.warning("Failed to render %s: %s", url, e)
                    return url, None, None
                finally:
                    await page.close()

        # Process all URLs concurrently
        tasks = [process_url(url) for url in article_urls]
        for coro in asyncio.as_completed(tasks):
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("salesforce: reached max_docs=%d, stopping", max_docs)
                break
            url, title, content_html = await coro

            if content_html is None:
                stats["errors"] += 1
                continue

            # Convert rendered HTML to structured text
            soup = BeautifulSoup(content_html, "html.parser")
            for tag in soup.find_all([
                "script", "style", "nav", "footer",
                "noscript", "svg", "iframe",
            ]):
                tag.decompose()
            content = html_to_text(soup)

            if not content or len(content) < 50:
                stats["errors"] += 1
                continue

            # Generate filename from URL slug
            slug = url.rstrip("/").split("/")[-1]
            filename = sanitize_filename(slug) + ".txt"

            if (output_dir / filename).exists():
                stats["skipped"] += 1
                continue

            written = write_document(
                output_dir, filename, url, title or "Untitled", content,
            )
            if written:
                stats["saved"] += 1
                if stats["saved"] % 20 == 0 or stats["saved"] <= 3:
                    logger.info(
                        "  [%d/%d] Saved: %s (%d chars)",
                        stats["saved"], stats["total"], filename, len(content),
                    )
            else:
                stats["skipped"] += 1

        await browser.close()

    return stats


def scrape_salesforce(
    sitemap_url: str,
    output_dir: Path,
    path_filter: str | None = None,
    content_selectors: list[str] | None = None,
    max_docs: int | None = None,
) -> dict:
    """Scrape all articles from a Salesforce Experience Cloud knowledge base.

    Args:
        sitemap_url: URL of the sitemap XML (or sitemap index)
        output_dir: Directory to write .txt files into
        path_filter: Only include URLs containing this string
        content_selectors: CSS selectors to find article content (ordered)
        max_docs: Stop after this many saved documents (None = unlimited).

    Returns:
        Stats dict: {"saved", "skipped", "errors", "total"}
    """
    # Phase 1: Discover articles from sitemap
    logger.info("Phase 1: Fetching sitemap from %s", sitemap_url)
    article_urls = _fetch_sitemap_urls(sitemap_url, path_filter)
    logger.info("Found %d article URLs in sitemap", len(article_urls))

    if not article_urls:
        return {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    # Phase 2: Render and extract with Playwright
    logger.info(
        "Phase 2: Rendering %d articles with Playwright (concurrency=%d)...",
        len(article_urls), CONCURRENT,
    )

    # Handle event loop (same pattern as square.py / microsoft.py)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                stats = pool.submit(
                    asyncio.run,
                    _scrape_articles_async(
                        article_urls, output_dir, content_selectors, max_docs,
                    ),
                ).result()
        else:
            stats = asyncio.run(
                _scrape_articles_async(
                    article_urls, output_dir, content_selectors, max_docs,
                ),
            )
    except RuntimeError:
        stats = asyncio.run(
            _scrape_articles_async(
                article_urls, output_dir, content_selectors, max_docs,
            ),
        )

    logger.info(
        "Salesforce scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats
