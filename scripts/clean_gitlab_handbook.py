"""GitLab Handbook HTML Cleaner.

Processes raw scraped pages from data/handbook/raw/, strips headers, navigation,
footers, scripts, and extracts clean, readable Markdown documents into data/handbook/cleaned/.
"""

import argparse
import json
import logging
from pathlib import Path
import re
import sys
from typing import Dict, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Ensure backend/src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))

from edrak.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_handbook")


def html_node_to_markdown(node) -> str:
    """Converts a BeautifulSoup HTML node to clean Markdown text."""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return str(node)

    tag_name = node.name.lower()

    # Skip boilerplate / non-content tags
    if tag_name in [
        "script", "style", "nav", "footer", "header", "aside", "noscript",
        "iframe", "svg", "button", "input", "form", "select"
    ]:
        return ""

    # Recurse on children
    inner_md = "".join(html_node_to_markdown(child) for child in node.children)

    if tag_name in ["h1"]:
        return f"\n\n# {inner_md.strip()}\n\n"
    elif tag_name in ["h2"]:
        return f"\n\n## {inner_md.strip()}\n\n"
    elif tag_name in ["h3"]:
        return f"\n\n### {inner_md.strip()}\n\n"
    elif tag_name in ["h4"]:
        return f"\n\n#### {inner_md.strip()}\n\n"
    elif tag_name in ["h5", "h6"]:
        return f"\n\n##### {inner_md.strip()}\n\n"
    elif tag_name == "p":
        clean_p = inner_md.strip()
        return f"\n\n{clean_p}\n\n" if clean_p else ""
    elif tag_name == "li":
        clean_li = inner_md.strip()
        return f"\n- {clean_li}" if clean_li else ""
    elif tag_name in ["ul", "ol"]:
        return f"\n{inner_md}\n"
    elif tag_name == "blockquote":
        lines = [f"> {line}" for line in inner_md.strip().split("\n") if line.strip()]
        return "\n\n" + "\n".join(lines) + "\n\n"
    elif tag_name == "code":
        if "\n" in inner_md:
            return f"\n```\n{inner_md.strip()}\n```\n"
        return f"`{inner_md.strip()}`"
    elif tag_name == "pre":
        return f"\n```\n{inner_md.strip()}\n```\n"
    elif tag_name in ["strong", "b"]:
        clean_bold = inner_md.strip()
        return f"**{clean_bold}**" if clean_bold else ""
    elif tag_name in ["em", "i"]:
        clean_it = inner_md.strip()
        return f"*{clean_it}*" if clean_it else ""
    elif tag_name == "a":
        link_text = inner_md.strip()
        href = node.get("href", "")
        if link_text and href and not href.startswith("#"):
            return f"[{link_text}]({href})"
        return link_text
    elif tag_name in ["table"]:
        return f"\n\n{inner_md.strip()}\n\n"
    elif tag_name in ["tr"]:
        cells = [c.get_text(strip=True) for c in node.find_all(["td", "th"])]
        if cells:
            return "| " + " | ".join(cells) + " |\n"
        return ""

    return inner_md


class HandbookCleaner:
    """Cleans raw HTML files and outputs high-quality Markdown documents."""

    def __init__(
        self,
        raw_dir: Optional[Path] = None,
        clean_dir: Optional[Path] = None,
    ):
        self.raw_dir = raw_dir or settings.get_absolute_raw_handbook_path()
        self.clean_dir = clean_dir or settings.get_absolute_cleaned_handbook_path()
        self.clean_dir.mkdir(parents=True, exist_ok=True)

    def clean_html(self, raw_html: str, url: str, page_title: str) -> str:
        """Extracts clean Markdown from raw page HTML."""
        soup = BeautifulSoup(raw_html, "lxml")

        # 1. Remove obvious navigational and decorative elements
        for element in soup.find_all([
            "nav", "footer", "header", "script", "style", "aside", "noscript",
            "svg", "form", "button", "dialog"
        ]):
            element.decompose()

        # Remove elements by class / ID patterns common to docs/handbooks
        for element in soup.find_all(attrs={"class": re.compile(
            r"(sidebar|navigation|cookie|banner|breadcrumb|footer|header|menu|nav|toc|search)",
            re.IGNORECASE
        )}):
            element.decompose()

        # 2. Find primary main content element if available
        main_content = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=re.compile(r"content|main", re.I))
            or soup.find("div", attrs={"class": re.compile(r"(content|markdown|handbook-content)", re.I)})
            or soup.body
            or soup
        )

        # 3. Convert to Markdown
        markdown_text = html_node_to_markdown(main_content)

        # 4. Clean up whitespace and empty lines
        markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text).strip()

        # 5. Extract section path from URL
        path = urlparse(url).path.strip("/")
        section = path.split("/")[0] if path else "handbook"

        # 6. Add Frontmatter metadata header
        frontmatter = (
            f"---\n"
            f"title: \"{page_title}\"\n"
            f"source_url: \"{url}\"\n"
            f"section: \"{section}\"\n"
            f"doc_type: \"internal_handbook\"\n"
            f"---\n\n"
        )

        return frontmatter + markdown_text

    def process_all(self) -> int:
        """Processes all raw JSON records in raw_dir."""
        logger.info("Starting cleaning of raw files in %s...", self.raw_dir)
        raw_files = list(self.raw_dir.glob("*.json"))
        if not raw_files:
            logger.warning("No raw files found in %s to clean.", self.raw_dir)
            return 0

        cleaned_count = 0
        for raw_file in raw_files:
            try:
                data = json.loads(raw_file.read_text(encoding="utf-8"))
                url = data.get("url", "")
                title = data.get("title", raw_file.stem)
                raw_html = data.get("html", "")

                if not raw_html:
                    continue

                clean_markdown = self.clean_html(raw_html, url, title)
                if len(clean_markdown.strip()) < 100:
                    logger.warning("Skipping %s (too little clean content)", raw_file.name)
                    continue

                out_filename = raw_file.stem.replace(".json", "") + ".md"
                out_path = self.clean_dir / out_filename
                out_path.write_text(clean_markdown, encoding="utf-8")
                cleaned_count += 1
                logger.info("Cleaned and saved: %s", out_path.name)
            except Exception as e:
                logger.error("Failed to clean %s: %s", raw_file.name, e)

        logger.info("Cleaned %d handbook files into %s", cleaned_count, self.clean_dir)
        return cleaned_count


def main():
    parser = argparse.ArgumentParser(description="Clean raw GitLab handbook pages to Markdown.")
    args = parser.parse_args()

    cleaner = HandbookCleaner()
    cleaner.process_all()


if __name__ == "__main__":
    main()
