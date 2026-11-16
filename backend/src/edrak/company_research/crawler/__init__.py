"""A bounded crawl of a company's own website, with a headless browser for pages that load late."""

from edrak.company_research.crawler.crawl import CrawlLimits, crawl_company_site
from edrak.company_research.crawler.models import (
    CrawledPage,
    CrawlResult,
    CrawlStats,
    FetchMode,
    PdfLink,
    StopReason,
)

__all__ = [
    "CrawlLimits",
    "CrawlResult",
    "CrawlStats",
    "CrawledPage",
    "FetchMode",
    "PdfLink",
    "StopReason",
    "crawl_company_site",
]
