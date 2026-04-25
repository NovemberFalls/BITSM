"""Shift4/SkyTab support center scraper (Zendesk Help Center).

Fetches articles from https://shift4.zendesk.com via public Zendesk API.
~2,105 articles across SkyTab, Payments, POS, and other categories.
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.zendesk import scrape_zendesk

logger = logging.getLogger(__name__)

BASE_URL = "https://shift4.zendesk.com"


@register("shift4")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Shift4/SkyTab support articles into output_dir."""
    logger.info("Starting Shift4 scraper (Zendesk) from %s", BASE_URL)
    return scrape_zendesk(BASE_URL, output_dir, max_docs=max_docs)
