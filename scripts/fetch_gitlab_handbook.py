"""GitLab Handbook Crawler / Scraper.

Crawls and fetches GitLab Handbook pages starting from https://handbook.gitlab.com/handbook/
and saves raw scraped pages into data/handbook/raw/.
"""

import argparse
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_handbook")

DEFAULT_START_URL = "https://handbook.gitlab.com/handbook/"
HANDBOOK_BASE_DOMAIN = "handbook.gitlab.com"


def clean_url(url: str) -> str:
    """Removes fragment, UTM parameters, and trailing slashes for canonical comparison."""
    defragged, _ = urldefrag(url)
    parsed = urlparse(defragged)
    # Strip query params like utm_source
    cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if cleaned.endswith("/") and len(parsed.path) > 1:
        cleaned = cleaned[:-1]
    return cleaned


class HandbookCrawler:
    """Fetches and stores raw HTML pages from GitLab Handbook."""

    def __init__(
        self,
        start_url: str = DEFAULT_START_URL,
        output_dir: Optional[Path] = None,
        max_pages: int = 25,
        delay_seconds: float = 0.5,
    ):
        self.start_url = clean_url(start_url)
        self.output_dir = output_dir or settings.get_absolute_raw_handbook_path()
        self.max_pages = max_pages
        self.delay_seconds = delay_seconds
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.visited: Set[str] = set()
        self.queue: List[str] = [self.start_url]
        self.headers = {
            "User-Agent": "EDRAK-InternalIntelligenceBot/1.0 (Business Intelligence Research; info@edrak.ai)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def is_valid_handbook_url(self, url: str) -> bool:
        """Determines if a URL belongs to the GitLab handbook domain and path."""
        parsed = urlparse(url)
        if parsed.netloc != HANDBOOK_BASE_DOMAIN:
            return False
        # Avoid binary assets, images, archives, downloads
        excluded_ext = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".zip", ".tar", ".gz", ".mp4")
        if any(parsed.path.lower().endswith(ext) for ext in excluded_ext):
            return False
        return True

    def extract_links(self, html: str, current_url: str) -> List[str]:
        """Extracts internal handbook links from page HTML."""
        links: List[str] = []
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                full_url = urljoin(current_url, href)
                cleaned = clean_url(full_url)
                if self.is_valid_handbook_url(cleaned) and cleaned not in self.visited and cleaned not in self.queue:
                    links.append(cleaned)
        except Exception as e:
            logger.debug("Error extracting links from %s: %s", current_url, e)
        return links

    def fetch_page(self, client: httpx.Client, url: str) -> Optional[Dict]:
        """Fetches a single page and returns the structured raw record."""
        try:
            logger.info("Fetching: %s", url)
            response = client.get(url, headers=self.headers, follow_redirects=True, timeout=15.0)
            if response.status_code != 200:
                logger.warning("Failed status %d for %s", response.status_code, url)
                return None

            html_content = response.text
            soup = BeautifulSoup(html_content, "lxml")
            title = soup.title.string.strip() if soup.title and soup.title.string else url

            # Generate unique filename based on URL hash
            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            slug = re.sub(r"[^a-zA-Z0-9_-]", "_", urlparse(url).path.strip("/"))[:40] or "index"
            filename = f"{slug}_{url_hash}.json"

            record = {
                "url": url,
                "title": title,
                "status_code": response.status_code,
                "fetched_at": datetime.utcnow().isoformat(),
                "html": html_content,
                "url_hash": url_hash,
            }

            file_path = self.output_dir / filename
            file_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Saved raw page to: %s", file_path.name)

            return {
                "record": record,
                "links": self.extract_links(html_content, str(response.url)),
            }
        except Exception as e:
            logger.error("Error fetching %s: %s", url, e)
            return None

    def crawl(self) -> int:
        """Executes the crawling loop up to max_pages (or unlimited if max_pages <= 0)."""
        limit_desc = "Unlimited" if self.max_pages <= 0 else str(self.max_pages)
        logger.info("Starting GitLab Handbook Crawl (Max Pages: %s)...", limit_desc)
        saved_count = 0

        with httpx.Client() as client:
            while self.queue and (self.max_pages <= 0 or saved_count < self.max_pages):
                current_url = self.queue.pop(0)
                if current_url in self.visited:
                    continue

                self.visited.add(current_url)
                result = self.fetch_page(client, current_url)

                if result:
                    saved_count += 1
                    new_links = result.get("links", [])
                    for link in new_links:
                        if link not in self.visited and link not in self.queue:
                            self.queue.append(link)

                time.sleep(self.delay_seconds)

        logger.info("Crawl finished. Successfully fetched %d pages to %s", saved_count, self.output_dir)
        return saved_count


def main():
    parser = argparse.ArgumentParser(description="Scrape GitLab Handbook pages.")
    parser.add_argument("--url", type=str, default=DEFAULT_START_URL, help="Starting URL for crawl")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum number of pages to scrape")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests in seconds")
    args = parser.parse_args()

    crawler = HandbookCrawler(
        start_url=args.url,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
    )
    crawler.crawl()


if __name__ == "__main__":
    main()
