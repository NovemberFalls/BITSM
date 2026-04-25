"""Rockbot support center scraper (Zendesk Help Center).

Fetches articles from https://support.rockbot.com via public Zendesk API.
~145 articles across 7 categories covering setup, troubleshooting, billing, etc.
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.zendesk import scrape_zendesk

logger = logging.getLogger(__name__)

BASE_URL = "https://support.rockbot.com"


@register("rockbot")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Rockbot support articles into output_dir."""
    logger.info("Starting Rockbot scraper (Zendesk) from %s", BASE_URL)
    return scrape_zendesk(BASE_URL, output_dir, max_docs=max_docs)
