"""Every piece of context the chat agent sees gets a tag it can cite.

Tags:
    E12       evidence 12 of the brief this chat is about
    B3        brief 0003 (its title and summary)
    B3:E12    evidence 12 of brief 0003, found by a workspace search
    B3:N4     another item of brief 0003 (a card, gap, claim, verifier note or finding)
    C2        a tracked competitor, market or topic on the watchlist
    A1        a recent alert
    P         the company profile (what the person entered at onboarding)
    CF4       an approved company-research fact
    D1        a passage from an uploaded document
    W1        a web result (always unverified)

The guardrail only keeps citations that are in the book, checks numbers against the cited text, and
derives the answer's label from the kinds of sources cited rather than from what the model claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

TAG = r"(?:E\d+|B\d+(?::[EN]\d+)?|C\d+|A\d+|P|CF\d+|D\d+|W\d+)"
CITE_RE = re.compile(r"\[(" + TAG + r")\]")
# "[E1, E2]" or "[E1][E2]" and "[CF2; D1]" are all split into single tags.
CITE_GROUP_RE = re.compile(r"\[(" + TAG + r"(?:\s*[,;،]\s*" + TAG + r")+)\]")

KIND_OF_PREFIX = {"E": "brief", "B": "workspace", "C": "workspace", "A": "workspace", "P": "company", "CF": "company",
                  "D": "documents", "W": "web"}


def kind_of(tag: str) -> str:
    prefix = "CF" if tag.startswith("CF") else tag[0]
    return KIND_OF_PREFIX.get(prefix, "other")


@dataclass
class Source:
    tag: str
    kind: str                      # brief | workspace | company | documents | web
    title: str
    text: str                      # what the model read; numbers in an answer are checked against it
    url: Optional[str] = None
    brief_id: Optional[str] = None
    brief_no: Optional[int] = None
    display_no: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """What the person's screen gets: no full text, a short excerpt."""
        excerpt = re.sub(r"\s+", " ", self.text or "").strip()
        return {"tag": self.tag, "kind": self.kind, "title": self.title[:160], "excerpt": excerpt[:240], "url": self.url,
                "brief_id": self.brief_id, "brief_no": self.brief_no, "display_no": self.display_no}


class SourceBook:
    def __init__(self) -> None:
        self.items: dict[str, Source] = {}
        self._counters: dict[str, int] = {}

    def __contains__(self, tag: str) -> bool:
        return tag in self.items

    def get(self, tag: str) -> Optional[Source]:
        return self.items.get(tag)

    def next_tag(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}{n}"

    def add(self, kind: str, title: str, text: str, *, tag: Optional[str] = None, prefix: Optional[str] = None,
            dedupe_key: Optional[str] = None, **meta: Any) -> str:
        """Register a source and return its tag. The same `dedupe_key` (a URL, a fact id) returns the earlier tag."""
        if dedupe_key:
            for s in self.items.values():
                if s.extra.get("dedupe_key") == dedupe_key:
                    return s.tag
        tag = tag or self.next_tag(prefix or "S")
        extra = dict(meta.pop("extra", {}) or {})
        if dedupe_key:
            extra["dedupe_key"] = dedupe_key
        self.items[tag] = Source(tag=tag, kind=kind, title=title or tag, text=text or "", extra=extra, **meta)
        return tag

    def text_of(self, tags: list[str]) -> str:
        return " ".join(self.items[t].text for t in tags if t in self.items)


def split_groups(text: str) -> str:
    """[E1, E2] -> [E1][E2], so each citation is checked and rendered on its own."""
    return CITE_GROUP_RE.sub(lambda m: "".join(f"[{t.strip()}]" for t in re.split(r"[,;،]", m.group(1))), text)
