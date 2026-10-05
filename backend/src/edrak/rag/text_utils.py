"""Pure-Python text helpers shared by the EDRAK indexer and retriever.

No third-party imports, so everything here is unit-testable without ChromaDB.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# "**Document ID:** INT-COMP-2026-005"  ->  key="Document ID", val="INT-COMP-2026-005"
_FIELD_RE = re.compile(r"^\*\*(?P<key>[A-Za-z][A-Za-z0-9 _/-]*?):\*\*\s*(?P<val>.*?)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the "
    "their this to was were will with we our you your not can may also than then".split()
)

MIN_FACT_CHARS = 25


def slugify_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def as_bool(value: Any, default: bool = False) -> bool:
    """Parse booleans that Chroma metadata may have stringified.

    NOTE: bool("False") is True in Python, which is exactly the bug this avoids.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n", ""):
        return False
    return default


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split optional YAML-ish '---' frontmatter from the body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm: Dict[str, str] = {}
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip("\"'")
            return fm, parts[2].lstrip()
    return {}, text


def parse_doc_header(text: str) -> Tuple[Dict[str, str], str]:
    """Pull the leading '# Title' + '**Key:** value' block out of a document.

    Returns (header_metadata, body_without_header). Keys are slugified, e.g.
    document_id, classification, last_updated, author, plus 'title'.
    """
    meta: Dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s == "---":
            i += 1
            continue
        if s.startswith("# ") and "title" not in meta:
            meta["title"] = s[2:].strip()
            i += 1
            continue
        m = _FIELD_RE.match(s)
        if m:
            meta[slugify_key(m.group("key"))] = m.group("val")
            i += 1
            continue
        break
    return meta, "\n".join(lines[i:]).lstrip()


def split_sections(body: str, default_heading: str = "") -> List[Tuple[str, str]]:
    """Split markdown into (heading, text) sections. Ignores '#' inside code fences."""
    sections: List[Tuple[str, str]] = []
    heading = default_heading
    buf: List[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text and text != "---":
            sections.append((heading, text))

    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line.strip())
        if m and len(m.group(1)) <= 4:
            flush()
            buf = []
            heading = clean_markdown(m.group(2))
            continue
        if line.strip() == "---" and not in_fence:
            continue
        buf.append(line)
    flush()
    return sections


def clean_markdown(s: str) -> str:
    s = _LINK_RE.sub(r"\1", s)
    s = re.sub(r"^\s*(>+|[-*+]|\d+[.)])\s+", "", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"\s+", " ", s).strip(" *_")
    return s.strip()


def _tokens(s: str) -> set:
    return {w for w in _TOKEN_RE.findall(s.lower()) if len(w) > 2 and w not in _STOPWORDS}


def split_sentences(text: str) -> List[str]:
    return [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]


def best_fact(text: str, query: str = "", max_chars: int = 400) -> str:
    """Pick the most query-relevant complete sentence from a chunk.

    Skips headings, '---', '**Key:** value' header lines and tiny bullets such as
    '- Social media accounts'. Never truncates mid-sentence unless > max_chars.
    """
    units: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "---" or s.startswith("#") or s.startswith("```"):
            continue
        if _FIELD_RE.match(s):
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]*", c) for c in cells):
                continue
            # Skip table header rows
            if any(c.lower() in ("dimension", "feature", "metric", "category", "attribute") for c in cells):
                continue
            s = "; ".join(c for c in cells if c)
        cleaned = clean_markdown(s)
        if len(cleaned) < MIN_FACT_CHARS:
            continue
        units.extend(u for u in split_sentences(cleaned) if len(u) >= MIN_FACT_CHARS)

    if not units:
        fallback = clean_markdown(text)
        return fallback[:max_chars].rsplit(" ", 1)[0] if len(fallback) > max_chars else fallback

    q = _tokens(query)
    best = max(range(len(units)), key=lambda i: (len(q & _tokens(units[i])), -i))
    fact = units[best]
    if len(fact) > max_chars:
        fact = fact[:max_chars].rsplit(" ", 1)[0] + "…"
    return fact
