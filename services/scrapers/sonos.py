"""Sonos support center scraper.

Fetches article URLs from sitemap, then scrapes content concurrently.
~639 articles at https://support.sonos.com/en-us/article/*

Strategy:
  1. Fetch sitemap XML → filter to /article/ URLs
  2. Async concurrent fetch with semaphore (10 parallel, 0.5s delay)
  3. Extract content: try __NEXT_DATA__ JSON, fallback to DOM
  4. Save as .txt files in standard format
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from xml.etree import ElementTree

import aiohttp
from bs4 import BeautifulSoup

from services.scrapers import register, write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://support.sonos.com/en-us/sitemap.xml"
BASE_URL = "https://support.sonos.com"
CONCURRENT = 10
DELAY = 0.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


async def _fetch_sitemap(session: aiohttp.ClientSession) -> list[str]:
    """Fetch sitemap XML and extract /article/ URLs."""
    async with session.get(
        SITEMAP_URL, timeout=aiohttp.ClientTimeout(total=30),
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
            if "/article/" in url:
                urls.append(url)
    except ElementTree.ParseError:
        # Regex fallback
        for m in re.finditer(
            r"<loc>(https://support\.sonos\.com/en-us/article/[^<]+)</loc>",
            xml_text,
        ):
            urls.append(m.group(1))

    return urls


def _portable_text_to_str(blocks: list) -> str:
    """Convert Sanity portable text blocks to plain text."""
    lines = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        style = block.get("style", "normal")
        children = block.get("children", [])
        text = " ".join(
            c.get("text", "") for c in children if isinstance(c, dict)
        ).strip()
        if not text:
            continue
        if style.startswith("h"):
            try:
                level = int(style[1])
                lines.append(f"\n{'#' * level} {text}\n")
            except (ValueError, IndexError):
                lines.append(text + "\n")
        elif block.get("listItem"):
            lines.append(f"  - {text}")
        else:
            lines.append(text + "\n")
    return "\n".join(lines).strip()


def _extract_content(html: str) -> tuple[str, str] | None:
    """Extract (title, content) from a Sonos article page."""
    soup = BeautifulSoup(html, "html.parser")

    # Try __NEXT_DATA__ first (Sanity CMS content)
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.string)
            props = data.get("props", {}).get("pageProps", {})
            article = props.get("article") or props.get("page") or {}
            title = article.get("title", "")

            # Try body as HTML string
            body = article.get("body", "") or article.get("content", "")
            if isinstance(body, str) and body:
                body_soup = BeautifulSoup(body, "html.parser")
                content = html_to_text(body_soup)
                if content and len(content) >= 50:
                    return title or "Untitled", content

            # Try body as Sanity portable text (list of blocks)
            if isinstance(body, list):
                content = _portable_text_to_str(body)
                if content and len(content) >= 50:
                    return title or "Untitled", content
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # DOM fallback
    for tag in soup.find_all([
        "script", "style", "nav", "footer", "header",
        "noscript", "svg", "iframe",
    ]):
        tag.decompose()

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" | ")[0].strip()

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"article|content|body", re.I))
        or soup.find("div", attrs={"role": "main"})
    )
    if not main:
        main = soup.find("body") or soup

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
        # Phase 1: Sitemap discovery
        logger.info("Sonos Phase 1: Fetching sitemap from %s", SITEMAP_URL)
        article_urls = await _fetch_sitemap(session)
        stats["total"] = len(article_urls)
        logger.info("Found %d article URLs in sitemap", len(article_urls))

        # Phase 2: Fetch and save articles
        logger.info(
            "Sonos Phase 2: Scraping %d articles (concurrency=%d)...",
            len(article_urls), CONCURRENT,
        )

        tasks = [
            _fetch_article(session, url, semaphore) for url in article_urls
        ]

        for coro in asyncio.as_completed(tasks):
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("sonos: reached max_docs=%d, stopping", max_docs)
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
                if stats["saved"] % 50 == 0 or stats["saved"] <= 3:
                    logger.info(
                        "  [%d/%d] Saved: %s (%d chars)",
                        stats["saved"], stats["total"], filename, len(content),
                    )
            else:
                stats["skipped"] += 1

    logger.info(
        "Sonos scrape complete: saved=%d, skipped=%d, errors=%d, total=%d",
        stats["saved"], stats["skipped"], stats["errors"], stats["total"],
    )
    return stats


@register("sonos")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Sonos support articles into output_dir."""
    # Run async code from Flask's sync context
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an existing event loop — run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run_async(output_dir, max_docs)).result()
        else:
            return asyncio.run(_run_async(output_dir, max_docs))
    except RuntimeError:
        return asyncio.run(_run_async(output_dir, max_docs))
