"""KB scraper registry — maps module slug to scraper function.

Each scraper module registers itself via the @register decorator.
Scrapers are invoked by the /api/webhooks/scrape/run endpoint.

All scrapers accept (output_dir: Path, max_docs: int | None = None) and return a stats dict:
    {"saved": int, "skipped": int, "errors": int, "total": int}

max_docs=None  — unlimited (production behaviour, unchanged).
max_docs=N     — stop after N saves; useful for smoke tests.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Base output directory — project_root/documents/
DOCUMENTS_BASE = Path(__file__).resolve().parent.parent.parent / "documents"

_REGISTRY: dict[str, callable] = {}


def register(slug: str):
    """Decorator to register a scraper for a module slug."""
    def wrapper(fn):
        _REGISTRY[slug] = fn
        return fn
    return wrapper


def get_scraper(slug: str):
    """Get scraper function by module slug, or None."""
    return _REGISTRY.get(slug)


def available() -> list[str]:
    """List module slugs with registered scrapers."""
    return sorted(_REGISTRY.keys())


def run_scraper(slug: str, max_docs: int | None = None) -> dict:
    """Run a scraper by module slug. Returns stats dict.

    Args:
        slug: Module slug identifying the registered scraper.
        max_docs: Optional cap on saved documents (None = unlimited).
    """
    scraper_fn = get_scraper(slug)
    if not scraper_fn:
        raise ValueError(
            f"No scraper registered for module '{slug}'. "
            f"Available: {available()}"
        )

    output_dir = DOCUMENTS_BASE / slug
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting scraper for %s -> %s (max_docs=%s)", slug, output_dir, max_docs)
    stats = scraper_fn(output_dir, max_docs=max_docs)
    logger.info("Scraper %s complete: %s", slug, stats)
    return stats


# -- Shared utilities for all scrapers --

def write_document(
    output_dir: Path,
    filename: str,
    source_url: str,
    title: str,
    content: str,
) -> bool:
    """Write a scraped document in the standard format.

    Format:
        Source: <url>
        Title: <title>
        ============================================================
        <content>

    Returns True if written, False if file already existed.
    """
    filepath = output_dir / filename
    if filepath.exists():
        return False

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Source: {source_url}\n")
        f.write(f"Title: {title}\n")
        f.write("=" * 60 + "\n\n")
        f.write(content)
    return True


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Create a safe filename from a string."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:max_length]


def html_to_text(soup) -> str:
    """Convert HTML soup to structured text (shared across scrapers)."""
    lines = []
    for el in soup.descendants:
        if not hasattr(el, "name") or el.name is None:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(el.name[1])
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n{'#' * level} {text}\n")
        elif el.name == "p":
            text = el.get_text(separator=" ", strip=True)
            if text:
                lines.append(text + "\n")
        elif el.name == "li":
            text = el.get_text(separator=" ", strip=True)
            if text:
                lines.append(f"  - {text}")
        elif el.name in ("pre", "code"):
            text = el.get_text(strip=False)
            if text and el.parent.name != "pre":
                lines.append(f"\n```\n{text}\n```\n")
        elif el.name == "table":
            rows = el.find_all("tr")
            if rows:
                for row in rows:
                    cells = row.find_all(["th", "td"])
                    cell_texts = [
                        c.get_text(separator=" ", strip=True) for c in cells
                    ]
                    lines.append(" | ".join(cell_texts))
        elif el.name == "dt":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n**{text}**")
        elif el.name == "dd":
            text = el.get_text(separator=" ", strip=True)
            if text:
                lines.append(f"  {text}")

    # Deduplicate consecutive identical lines
    result = []
    prev = None
    for line in lines:
        if line != prev:
            result.append(line)
            prev = line

    return "\n".join(result).strip()


# Import scraper modules to trigger registration
from services.scrapers import solink, sonos, powerbi, olo, rockbot, r365, microsoft, shift4, lightspeed, square, oracle, billcom, paytronix, intercom, toast  # noqa: F401, E402
