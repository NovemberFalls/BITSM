"""Paytronix help center scraper (Intercom-based).

Fetches articles from https://help-paytronix.theaccessgroup.com via Intercom API.
~537 articles across 13 collections (Loyalty, Gift, Online Ordering, Reporting, etc.).
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.intercom import scrape_intercom

logger = logging.getLogger(__name__)

BASE_URL = "https://help-paytronix.theaccessgroup.com"


@register("paytronix")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Paytronix help center articles into output_dir."""
    logger.info("Starting Paytronix scraper (Intercom) from %s", BASE_URL)
    return scrape_intercom(BASE_URL, output_dir, max_docs=max_docs)
