"""Restaurant365 documentation scraper (Document360 platform).

Fetches articles from https://docs.restaurant365.com via sitemap discovery.
~2,308 articles across 22 categories. Server-side rendered HTML — no JS needed.

Strategy:
  1. Fetch sitemap-en.xml → extract all /docs/ URLs
  2. Async concurrent fetch with semaphore (10 parallel, 1.5s delay)
  3. Extract content from <article id="articleContent"> element
  4. Save as .txt files in standard format
"""

import asyncio
import logging
import re
from pathlib import Path
from xml.etree import ElementTree

import aiohttp
from bs4 import BeautifulSoup

from services.scrapers import register, write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://docs.restaurant365.com/sitemap-en.xml"
BASE_URL = "https://docs.restaurant365.com"
CONCURRENT = 10
DELAY = 1.5  # respectful delay — large site

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
}


async def _fetch_sitemap(session: aiohttp.ClientSession) -> list[str]:
    """Fetch sitemap XML and extract /docs/ article URLs."""
    async with session.get(
        SITEMAP_URL,
        timeout=aiohttp.ClientTimeout(total=60),
        allow_redirects=True,
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Sitemap returned HTTP {resp.status}")
        xml_text = await resp.text()

    urls = []
    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            url = (loc.text or "").strip()
            # Skip the root docs page, only grab article URLs
            if url.startswith(f"{BASE_URL}/docs/") and url != f"{BASE_URL}/docs/":
                urls.append(url)
    except ElementTree.ParseError:
        # Regex fallback
        for m in re.finditer(
            r"<loc>(https://docs\.restaurant365\.com/docs/[^<]+)</loc>",
            xml_text,
        ):
            url = m.group(1)
            if url != f"{BASE_URL}/docs/":
                urls.append(url)

    return urls


def _extract_content(html: str) -> tuple[str, str] | None:
    """Extract (title, content) from a Document360 article page."""
    soup = BeautifulSoup(html, "html.parser")

    # Title: <h1 class="article-title">
    title = ""
    h1 = soup.find("h1", class_="article-title")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" |")[0].strip()

    # Content: <article id="articleContent">
    article = soup.find("article", id="articleContent")
    if not article:
        article = soup.find("article")
    if not article:
        article = soup.find("div", class_=re.compile(r"article|content", re.I))
    if not article:
        return None

    # Remove noise inside the article
    for tag in article.find_all([
        "script", "style", "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    content = html_to_text(article)
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
        # Phase 1: Sitemap discovery
        logger.info("R365 Phase 1: Fetching sitemap from %s", SITEMAP_URL)
        article_urls = await _fetch_sitemap(session)
        stats["total"] = len(article_urls)
        logger.info("Found %d article URLs in sitemap", len(article_urls))

        # Phase 2: Fetch and save articles
        logger.info(
            "R365 Phase 2: Scraping %d articles (concurrency=%d, delay=%.1fs)...",
            len(article_urls), CONCURRENT, DELAY,
        )

        tasks = [
            _fetch_article(session, url, semaphore) for url in article_urls
        ]

        for coro in asyncio.as_completed(tasks):
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("r365: reached max_docs=%d, stopping", max_docs)
                break
            url, html = await coro

            if html is None:
                stats["errors"] += 1
                continue

            # Build filename from URL slug
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
                if stats["saved"] % 100 == 0 or stats["saved"] <= 3:
                    logger.info(
                        "  [%d/%d] Saved: %s (%d chars)",
                        stats["saved"], stats["total"], filename, len(content),
                    )
            else:
                stats["skipped"] += 1

    logger.info(
        "R365 scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats


@register("r365")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Restaurant365 documentation into output_dir."""
    logger.info("Starting R365 scraper (Document360) from %s", BASE_URL)
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
