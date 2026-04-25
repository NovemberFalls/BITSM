"""Microsoft Support documentation scraper.

Fetches articles from https://support.microsoft.com/en-us/ via sitemap shards.
Server-side rendered HTML (jQuery, not SPA) — no JS needed.

The en-us sitemap is split into 16 hex shards (index-0 through index-f),
each containing ~1,000 URLs across all Microsoft products. We filter by
product keywords to extract only relevant articles.

Products registered as separate modules:
  - ms_outlook (~500-700 articles)
  - ms_teams (~200-300 articles)
  - ms_excel (~150-250 articles)
  - ms_sharepoint (~80-120 articles)

Strategy:
  1. Fetch all 16 sitemap shards → filter URLs by product keywords
  2. Async concurrent fetch with semaphore (10 parallel, 1.0s delay)
  3. Extract content from server-rendered HTML article body
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

BASE_URL = "https://support.microsoft.com"
SITEMAP_SHARDS = [f"{BASE_URL}/en-us/sitemap/index-{h}.xml" for h in "0123456789abcdef"]
CONCURRENT = 10
DELAY = 1.0  # respectful delay for Microsoft

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
}

# Product keyword filters — URL slug or path must contain one of these
PRODUCT_FILTERS = {
    "ms_outlook": ["outlook"],
    "ms_teams": ["teams"],
    "ms_excel": ["excel"],
    "ms_sharepoint": ["sharepoint"],
}


async def _fetch_sitemap_shard(
    session: aiohttp.ClientSession,
    shard_url: str,
    product_keywords: list[str],
) -> list[str]:
    """Fetch one sitemap shard and extract matching article URLs."""
    try:
        async with session.get(
            shard_url, timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return []
            xml_text = await resp.text()
    except Exception as e:
        logger.warning("Failed to fetch sitemap shard %s: %s", shard_url, e)
        return []

    urls = []
    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            url = (loc.text or "").strip()
            url_lower = url.lower()
            # Must be an article URL (contains a UUID pattern) and match product
            if "/en-us/" in url_lower and any(kw in url_lower for kw in product_keywords):
                # Skip hub pages — articles have UUIDs in the URL
                if re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-', url_lower):
                    urls.append(url)
    except ElementTree.ParseError:
        pass

    return urls


async def _discover_articles(
    session: aiohttp.ClientSession,
    product_keywords: list[str],
) -> list[str]:
    """Fetch all 16 sitemap shards and collect matching article URLs."""
    tasks = [
        _fetch_sitemap_shard(session, shard, product_keywords)
        for shard in SITEMAP_SHARDS
    ]
    all_urls = []
    seen = set()
    for result in await asyncio.gather(*tasks):
        for url in result:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)
    return all_urls


def _extract_content(html: str) -> tuple[str, str] | None:
    """Extract (title, content) from a Microsoft Support article page."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" - Microsoft")[0].strip()

    # Remove noise elements
    for tag in soup.find_all([
        "script", "style", "nav", "footer", "header",
        "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    # Remove navigation/sidebar/related content
    for sel in soup.select(
        ".ocArticleFooter, .ocFeedback, .ocBreadcrumb, "
        "#sidePanel, .side-panel, .related-articles, "
        ".ocShareWidget, #ocAdditionalResources"
    ):
        sel.decompose()

    # Find main content area
    main = (
        soup.find("div", id="main-content")
        or soup.find("div", class_="ocArticleContent")
        or soup.find("article")
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


async def _run_async(
    product_slug: str,
    product_keywords: list[str],
    output_dir: Path,
    max_docs: int | None = None,
) -> dict:
    """Async scraping logic for a specific Microsoft product."""
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}
    semaphore = asyncio.Semaphore(CONCURRENT)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Phase 1: Discover articles from all sitemap shards
        logger.info(
            "%s Phase 1: Scanning 16 sitemap shards for '%s' articles...",
            product_slug, "', '".join(product_keywords),
        )
        article_urls = await _discover_articles(session, product_keywords)
        stats["total"] = len(article_urls)
        logger.info("Found %d %s article URLs", len(article_urls), product_slug)

        if not article_urls:
            return stats

        # Phase 2: Fetch and save articles
        logger.info(
            "%s Phase 2: Scraping %d articles (concurrency=%d, delay=%.1fs)...",
            product_slug, len(article_urls), CONCURRENT, DELAY,
        )

        tasks = [
            _fetch_article(session, url, semaphore) for url in article_urls
        ]

        for coro in asyncio.as_completed(tasks):
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("%s: reached max_docs=%d, stopping", product_slug, max_docs)
                break
            url, html = await coro

            if html is None:
                stats["errors"] += 1
                continue

            # Build filename from URL slug (strip UUID suffix for readability)
            slug = url.rstrip("/").split("/")[-1]
            # Remove UUID at the end if present
            slug_clean = re.sub(r'-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', '', slug)
            filename = sanitize_filename(slug_clean or slug) + ".txt"

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
        "%s scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        product_slug, stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats


def _run_sync(
    product_slug: str,
    product_keywords: list[str],
    output_dir: Path,
    max_docs: int | None = None,
) -> dict:
    """Run async scraper from Flask sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    asyncio.run,
                    _run_async(product_slug, product_keywords, output_dir, max_docs),
                ).result()
        else:
            return asyncio.run(_run_async(product_slug, product_keywords, output_dir, max_docs))
    except RuntimeError:
        return asyncio.run(_run_async(product_slug, product_keywords, output_dir, max_docs))


# -- Registered scrapers for each Microsoft product --

@register("ms_outlook")
def run_outlook(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Microsoft Outlook support articles."""
    logger.info("Starting MS Outlook scraper from support.microsoft.com")
    return _run_sync("ms_outlook", PRODUCT_FILTERS["ms_outlook"], output_dir, max_docs)


@register("ms_teams")
def run_teams(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Microsoft Teams support articles."""
    logger.info("Starting MS Teams scraper from support.microsoft.com")
    return _run_sync("ms_teams", PRODUCT_FILTERS["ms_teams"], output_dir, max_docs)


@register("ms_excel")
def run_excel(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Microsoft Excel support articles."""
    logger.info("Starting MS Excel scraper from support.microsoft.com")
    return _run_sync("ms_excel", PRODUCT_FILTERS["ms_excel"], output_dir, max_docs)


@register("ms_sharepoint")
def run_sharepoint(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Microsoft SharePoint support articles."""
    logger.info("Starting MS SharePoint scraper from support.microsoft.com")
    return _run_sync("ms_sharepoint", PRODUCT_FILTERS["ms_sharepoint"], output_dir, max_docs)
