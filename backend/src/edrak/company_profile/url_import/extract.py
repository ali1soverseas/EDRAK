"""Main text of a page, without navigation, footers and comments."""

from __future__ import annotations

import trafilatura


def extract_main_text(html: str, url: str = "") -> str:
    """The readable body text of `html`, tables kept. Empty when nothing readable is found."""
    if not html or not html.strip():
        return ""
    text = trafilatura.extract(
        html,
        url=url or None,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
        output_format="txt",
    )
    return (text or "").strip()
