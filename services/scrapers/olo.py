"""Olo support center scraper (Zendesk Help Center).

Fetches articles from https://olosupport.zendesk.com via public Zendesk API.
~98 articles across 13 categories covering Dashboard, Menu, Ordering, Dispatch, etc.
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.zendesk import scrape_zendesk

logger = logging.getLogger(__name__)

BASE_URL = "https://olosupport.zendesk.com"


@register("olo")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Olo support articles into output_dir."""
    logger.info("Starting Olo scraper (Zendesk) from %s", BASE_URL)
    return scrape_zendesk(BASE_URL, output_dir, max_docs=max_docs)
