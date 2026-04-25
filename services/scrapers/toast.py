"""Toast scraper — support center + platform guide in one @register("toast").

_scrape_support_async:    BFS of support.toasttab.com via aiohttp + Semaphore.
                          Filenames prefixed "support_" to avoid collisions.
_scrape_platformguide_sync: BFS of doc.toasttab.com/doc/platformguide/ via requests.

Budget is shared: max_docs across both sources combined. Support runs first;
platformguide receives the remainder (may be zero — logged and skipped).

toast_support_urls.json: NOT used. The file is a previous-run manifest with
several corrupted HTML-entity-encoded slugs. Hardcoded seeds + BFS discovery
are more reliable. File is left for Tier 4 to retire with the legacy scripts.

Seeds: central.toasttab.com sitemaps are fetched first (best-effort); hardcoded
seeds ensure the scraper works when those endpoints are down.
"""

import asyncio
import logging
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import aiohttp
import requests
from bs4 import BeautifulSoup

from services.scrapers import register, write_document, sanitize_filename, html_to_text

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# -- Support center ----------------------------------------------------------
SUPPORT_BASE = "https://support.toasttab.com"
SUPPORT_ARTICLE_URL = SUPPORT_BASE + "/en/article/{slug}?lang=en_US"
CENTRAL_SITEMAPS = [
    "https://central.toasttab.com/s/sitemap-topicarticle-1.xml",
    "https://central.toasttab.com/s/sitemap-topicarticle-weekly.xml",
]
SUPPORT_SEED_SLUGS = [
    "My-Printer-Isnt-Working",
    "Contact-Customer-Support",
    "Getting-Started-Online-Ordering",
    "Toast-Hardware-Hub",
    "How-can-I-contact-support-in-the-Toast-app-1492809778919",
    "Building-your-Menu-Template",
    "Printer-Paper-Rolls-and-Ink-Ribbons-1492745816150",
    "Toast-POS-App-Overview",
    "Toast-Go-2-Overview",
    "Kitchen-Display-System-Overview",
]
SUPPORT_CONCURRENT = 10
SUPPORT_DELAY = 0.5

# -- Platform guide ----------------------------------------------------------
PLATFORMGUIDE_INDEX = "https://doc.toasttab.com/doc/platformguide/index.html"
PLATFORMGUIDE_NETLOC = "doc.toasttab.com"
PLATFORMGUIDE_PREFIX = "/doc/platformguide/"
PLATFORMGUIDE_DELAY = 0.5


# ===========================================================================
# Support-center sub-scraper (async BFS)
# ===========================================================================

def _extract_slugs_from_sitemap(xml_text: str) -> set[str]:
    slugs: set[str] = set()
    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            m = re.search(r"/s/article/([^?#]+)", loc.text or "")
            if m:
                slugs.add(m.group(1))
    except ElementTree.ParseError:
        for m in re.finditer(r"/s/article/([^?&#<>\s]+)", xml_text):
            slugs.add(m.group(1))
    return slugs


async def _discover_support_seeds(session: aiohttp.ClientSession) -> set[str]:
    slugs: set[str] = set()
    for url in CENTRAL_SITEMAPS:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    found = _extract_slugs_from_sitemap(await r.text())
                    slugs.update(found)
                    logger.info("toast/support: sitemap %s → %d slugs", url, len(found))
        except Exception as exc:
            logger.warning("toast/support: sitemap %s unavailable: %s", url, exc)
    slugs.update(SUPPORT_SEED_SLUGS)
    logger.info("toast/support: %d seed slugs total", len(slugs))
    return slugs


def _extract_support_links(html: str) -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r'href="[^"]*?/(?:en/)?article/([^"?#]+)', html)
        if not m.group(1).startswith(("es-", "zh-", "fr-"))
    }


async def _fetch_support_article(
    session: aiohttp.ClientSession,
    slug: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str | None, set[str]]:
    url = SUPPORT_ARTICLE_URL.format(slug=slug)
    async with semaphore:
        await asyncio.sleep(SUPPORT_DELAY)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return slug, None, set()
                html = await r.text()
                return slug, html, _extract_support_links(html)
        except Exception:
            return slug, None, set()


def _parse_support_article(html: str) -> tuple[str, str] | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"]):
        tag.decompose()
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        t = soup.find("title")
        if t:
            title = t.get_text(strip=True).split(" - ")[0].strip()
    main = (
        soup.find("main") or soup.find("article")
        or soup.find("div", class_=re.compile(r"article|content|body", re.I))
        or soup.find("div", attrs={"role": "main"})
        or soup.find("body") or soup
    )
    content = html_to_text(main)
    if not content or len(content) < 50:
        return None
    return title or "Untitled", content


async def _run_support_async(output_dir: Path, max_docs: int | None) -> dict:
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}
    semaphore = asyncio.Semaphore(SUPPORT_CONCURRENT)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        queue = list(await _discover_support_seeds(session))
        visited: set[str] = set()
        wave = 0

        while queue:
            if max_docs is not None and stats["saved"] >= max_docs:
                logger.info("toast/support: budget exhausted (max_docs=%d), stopping", max_docs)
                break
            wave += 1
            batch = [s for s in queue if s not in visited]
            for s in batch:
                visited.add(s)
            queue = []
            if not batch:
                break
            stats["total"] += len(batch)
            logger.info("toast/support: wave %d — %d articles", wave, len(batch))

            new_slugs: set[str] = set()
            for coro in asyncio.as_completed(
                [_fetch_support_article(session, s, semaphore) for s in batch]
            ):
                if max_docs is not None and stats["saved"] >= max_docs:
                    break
                slug, html, discovered = await coro
                new_slugs.update(discovered)
                if html is None:
                    stats["errors"] += 1
                    continue
                result = _parse_support_article(html)
                if result is None:
                    stats["skipped"] += 1
                    continue
                title, content = result
                filename = f"support_{sanitize_filename(slug)}.txt"
                if write_document(output_dir, filename, SUPPORT_ARTICLE_URL.format(slug=slug), title, content):
                    stats["saved"] += 1
                    if stats["saved"] <= 5 or stats["saved"] % 50 == 0:
                        logger.info("toast/support [%d] saved: %s", stats["saved"], filename)
                else:
                    stats["skipped"] += 1

            for s in new_slugs:
                if s not in visited:
                    queue.append(s)

    logger.info("toast/support done: %s", stats)
    return stats


def _scrape_support_async(output_dir: Path, max_docs: int | None) -> dict:
    # Bridge async → sync for Flask/Gunicorn workers (mirrors sonos.py pattern).
    # If a loop is already running (async worker class), use a thread to avoid
    # blocking it with asyncio.run().
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run_support_async(output_dir, max_docs)).result()
        return asyncio.run(_run_support_async(output_dir, max_docs))
    except RuntimeError:
        return asyncio.run(_run_support_async(output_dir, max_docs))


# ===========================================================================
# Platform-guide sub-scraper (sync BFS)
# ===========================================================================

def _is_platformguide_url(url: str) -> bool:
    p = urlparse(url)
    return (
        p.netloc == PLATFORMGUIDE_NETLOC
        and p.path.startswith(PLATFORMGUIDE_PREFIX)
        and p.path.endswith(".html")
        and "#" not in url
    )


def _scrape_platformguide_sync(output_dir: Path, max_docs: int | None) -> dict:
    stats = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}
    session = requests.Session()
    session.headers.update(HEADERS)
    visited: set[str] = set()
    queue: deque[str] = deque([PLATFORMGUIDE_INDEX])

    while queue:
        if max_docs is not None and stats["saved"] >= max_docs:
            logger.info("toast/platformguide: budget exhausted (max_docs=%d), stopping", max_docs)
            break
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        stats["total"] += 1

        time.sleep(PLATFORMGUIDE_DELAY)
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("toast/platformguide: error %s: %s", url, exc)
            stats["errors"] += 1
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # Discover links before decomposing noise tags
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("#", "javascript:", "mailto:")):
                continue
            full = urljoin(url, href).split("#")[0]
            if _is_platformguide_url(full) and full not in visited:
                queue.append(full)

        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        title = ""
        t = soup.find("title")
        if t:
            title = t.get_text(strip=True)
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        main = (
            soup.find("main") or soup.find("article")
            or soup.find("div", class_=re.compile(r"content|main|body|topic", re.I))
            or soup.find("div", id=re.compile(r"content|main|body|topic", re.I))
            or soup.find("body") or soup
        )
        content = html_to_text(main)
        if not content or len(content) < 50:
            stats["skipped"] += 1
            continue

        page_name = urlparse(url).path.split("/")[-1].replace(".html", "")
        filename = sanitize_filename(page_name) + ".txt"
        if write_document(output_dir, filename, url, title or "Untitled", content):
            stats["saved"] += 1
            if stats["saved"] <= 5 or stats["saved"] % 50 == 0:
                logger.info("toast/platformguide [%d] saved: %s", stats["saved"], filename)
        else:
            stats["skipped"] += 1

    logger.info("toast/platformguide done: %s", stats)
    return stats


# ===========================================================================
# Public entry point
# ===========================================================================

@register("toast")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape both Toast platform guide + Toast support center into output_dir.

    Runs support-center first (richer, user-facing content) and platform guide
    second. Budget is shared: max_docs=3 across both sources, not 3 each.
    """
    logger.info("toast: starting combined scrape (max_docs=%s)", max_docs)
    support_stats = _scrape_support_async(output_dir, max_docs)

    if max_docs is not None:
        remaining = max_docs - support_stats["saved"]
        if remaining <= 0:
            logger.info(
                "toast: budget exhausted after support scrape (saved=%d, max_docs=%d)"
                " — skipping platformguide",
                support_stats["saved"], max_docs,
            )
            guide_stats: dict = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}
        else:
            guide_stats = _scrape_platformguide_sync(output_dir, remaining)
    else:
        guide_stats = _scrape_platformguide_sync(output_dir, None)

    combined = {k: support_stats.get(k, 0) + guide_stats.get(k, 0) for k in ("saved", "skipped", "errors", "total")}
    logger.info("toast: combined stats: %s", combined)
    return combined
