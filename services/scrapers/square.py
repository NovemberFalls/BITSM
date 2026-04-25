"""Square support center scraper.

Fetches help articles from https://squareup.com/help/us/en via hub-page crawl.
~200-400 articles, server-side rendered HTML. No JS required.

Strategy (no sitemap for /help/ URLs):
  1. Fetch hub page → discover /topic/ URLs
  2. Fetch each topic page → discover /article/ URLs
  3. Async concurrent fetch articles with semaphore (10 parallel, 1.0s delay)
  4. Extract content from server-rendered article body
  5. Save as .txt files in standard format
"""

import asyncio
import logging
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

from services.scrapers import register, write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

HUB_URL = "https://squareup.com/help/us/en"
BASE_URL = "https://squareup.com"
CONCURRENT = 10
DELAY = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch a page, return HTML or None."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return None
            return await resp.text()
    except Exception:
        return None


async def _discover_articles(session: aiohttp.ClientSession) -> list[str]:
    """Crawl hub → topics → collect all article URLs."""
    # Phase 1: Get topic URLs from hub page
    logger.info("Square discovery: fetching hub page %s", HUB_URL)
    hub_html = await _fetch_page(session, HUB_URL)
    if not hub_html:
        logger.warning("Failed to fetch Square hub page")
        return []

    soup = BeautifulSoup(hub_html, "html.parser")
    topic_urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/help/us/en/topic/" in href:
            url = href if href.startswith("http") else BASE_URL + href
            url = url.split("?")[0].split("#")[0]
            if url not in topic_urls:
                topic_urls.append(url)

    logger.info("Found %d topic pages", len(topic_urls))

    # Phase 2: Get article URLs from each topic page
    article_urls = []
    seen = set()

    for topic_url in topic_urls:
        await asyncio.sleep(DELAY)
        topic_html = await _fetch_page(session, topic_url)
        if not topic_html:
            continue

        topic_soup = BeautifulSoup(topic_html, "html.parser")
        for a in topic_soup.find_all("a", href=True):
            href = a["href"]
            if "/help/us/en/article/" in href:
                url = href if href.startswith("http") else BASE_URL + href
                url = url.split("?")[0].split("#")[0]
                if url not in seen:
                    seen.add(url)
                    article_urls.append(url)

    return article_urls


def _extract_content(html: str) -> tuple[str, str] | None:
    """Extract (title, content) from a Square help article page."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" | ")[0].strip()

    # Remove noise
    for tag in soup.find_all([
        "script", "style", "nav", "footer", "header",
        "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    # Find main content area
    main = (
        soup.find("article")
        or soup.find("div", class_=re.compile(r"article-body|article-content", re.I))
        or soup.find("main")
        or soup.find("div", attrs={"role": "main"})
    )
    if not main:
        return None

    content = html_to_text(main)
    if not content or len(content) < 50:
        return None

    return title or "Untitled", content


async def _fetch_article(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str | None]:
    """Fetch a single article. Returns (url, html_or_none)."""
    async with semaphore:
        await asyncio.sleep(DELAY)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return url, None
                return url, await resp.text()
        except Exception:
            return url, None


async def _run_async(output_dir: Path, max_docs: int | None = None) -> dict:
    """Async scraping logic."""
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}
    semaphore = asyncio.Semaphore(CONCURRENT)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Phase 1-2: Discover articles via hub → topic crawl
        logger.info("Square Phase 1-2: Discovering articles via hub crawl...")
        article_urls = await _discover_articles(session)
        stats["total"] = len(article_urls)
        logger.info("Found %d unique article URLs", len(article_urls))

        if not article_urls:
            return stats

        # Phase 3: Fetch and save articles
        logger.info(
            "Square Phase 3: Scraping %d articles (concurrency=%d)...",
            len(article_urls), CONCURRENT,
        )

        tasks = [
            _fetch_article(session, url, semaphore) for url in article_urls
        ]

        for coro in asyncio.as_completed(tasks):
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("square: reached max_docs=%d, stopping", max_docs)
                break
            url, html = await coro
            if html is None:
                stats["errors"] += 1
                continue

            slug = url.rstrip("/").split("/")[-1]
            filename = sanitize_filename(slug) + ".txt"

            if (output_dir / filename).exists():
                stats["skipped"] += 1
                continue

            result = _extract_content(html)
            if result is None:
                stats["errors"] += 1
                continue

            title, content = result
            written = write_document(output_dir, filename, url, title, content)
            if written:
                stats["saved"] += 1
                if stats["saved"] % 50 == 0 or stats["saved"] <= 3:
                    logger.info(
                        "  [%d/%d] Saved: %s (%d chars)",
                        stats["saved"], stats["total"], filename, len(content),
                    )
            else:
                stats["skipped"] += 1

    logger.info(
        "Square scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats


@register("square")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Square help articles into output_dir."""
    logger.info("Starting Square scraper from %s", HUB_URL)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run_async(output_dir, max_docs)).result()
        else:
            return asyncio.run(_run_async(output_dir, max_docs))
    except RuntimeError:
        return asyncio.run(_run_async(output_dir, max_docs))
