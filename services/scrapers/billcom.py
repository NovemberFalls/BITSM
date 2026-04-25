"""Bill.com (BILL) help center scraper (Salesforce Experience Cloud).

Fetches knowledge articles from https://help.bill.com/direct/s/
via sitemap discovery + Playwright rendering.

~1,000 articles covering: payments/invoicing, integrations (QuickBooks,
Xero, NetSuite, Oracle), cards/spending, reports, vendor management.

Requires: pip install playwright && playwright install chromium
"""

import logging
from pathlib import Path

from services.scrapers import register
from services.scrapers.salesforce import scrape_salesforce

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://help.bill.com/direct/s/sitemap-topicarticle-1.xml"
PATH_FILTER = "/s/article/"


@register("bill_com")
def run(output_dir: Path, max_docs: int | None = None) -> dict:
    """Scrape Bill.com help center articles into output_dir."""
    logger.info("Starting Bill.com scraper (Salesforce) from %s", SITEMAP_URL)
    return scrape_salesforce(SITEMAP_URL, output_dir, path_filter=PATH_FILTER, max_docs=max_docs)
