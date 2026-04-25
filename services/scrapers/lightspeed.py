"""Lightspeed restaurant POS support scraper (Zendesk Help Center).

Fetches articles from Lightspeed's K-Series and L-Series Zendesk instances.
K-Series: ~501 articles, L-Series: ~200+ articles.

Multiple Zendesk instances are scraped into a single module directory.
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.zendesk import scrape_zendesk

logger = logging.getLogger(__name__)

# Lightspeed restaurant product Zendesk instances
INSTANCES = [
    ("K-Series", "https://k-series-support.lightspeedhq.com"),
    ("L-Series", "https://resto-support.lightspeedhq.com"),
]


@register("lightspeed")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Lightspeed restaurant support articles into output_dir.

    When max_docs is set, the cap is applied across both Zendesk instances
    combined: each instance receives a remaining budget so the total saved
    across K-Series and L-Series does not exceed max_docs.
    """
    combined = {"saved": 0, "skipped": 0, "errors": 0, "total": 0}

    for name, base_url in INSTANCES:
        # Compute remaining budget so the cap is global across all instances
        remaining = None
        if max_docs is not None:
            remaining = max_docs - combined["saved"]
            if remaining <= 0:
                logger.info("lightspeed: reached max_docs=%d, skipping %s", max_docs, name)
                break
        logger.info("Starting Lightspeed %s scraper from %s", name, base_url)
        stats = scrape_zendesk(base_url, output_dir, max_docs=remaining)
        for key in combined:
            combined[key] += stats.get(key, 0)
        logger.info("Lightspeed %s: %s", name, stats)

    logger.info("Lightspeed combined: %s", combined)
    return combined
