"""Finding the post a comment sits under.

A comment does not carry its post's text, and each provider names the post differently: Instagram
and Facebook give `post_url`, YouTube a `video_id`, Reddit a `post_id`, and the URLs of TikTok and
YouTube comments contain the post's. So both sides are reduced to the same keys (a normalized URL
and, where the platform has one, the post's own id) and a comment is matched to a post on any key.
A comment whose post was not collected, or a platform that gives no hint (X replies), is grouped
with the others of its kind under no parent.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from edrak.agents.customer_trends.schemas.evidence import EvidenceItem

_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("yt", re.compile(r"(?:youtube\.com/(?:shorts|embed|live)/|youtu\.be/)([\w-]{6,})", re.I)),
    ("reddit", re.compile(r"/comments/(\w+)", re.I)),
    ("ig", re.compile(r"/(?:p|reel|reels|tv)/([\w-]+)", re.I)),
    ("tt", re.compile(r"/video/(\d+)", re.I)),
    ("x", re.compile(r"/status(?:es)?/(\d+)", re.I)),
    ("fb", re.compile(r"/(?:posts|videos|reel|permalink)/(\w+)", re.I)),
)
_PREFIXES = re.compile(r"^(?:www|m|mobile|old|new)\.")


def _url_keys(url: str) -> list[str]:
    parts = urlsplit(url.strip())
    host = _PREFIXES.sub("", (parts.hostname or "").lower())
    if not host:
        return []
    keys: list[str] = []
    query = parse_qs(parts.query)
    full = f"{host}{parts.path}"
    for name, pattern in _ID_PATTERNS:
        match = pattern.search(full)
        if match:
            keys.append(f"{name}:{match.group(1).lower()}")
    if "youtube.com" in host and query.get("v"):
        keys.append(f"yt:{query['v'][0].lower()}")
    for param in ("story_fbid", "fbid"):
        if query.get(param):
            keys.append(f"fb:{query[param][0].lower()}")
    keys.append(f"url:{host}{parts.path.rstrip('/')}")
    return keys


def ref_keys(platform: str, value: object) -> list[str]:
    """Every key a post URL or id answers to. A bare id is read by the platform it came from."""
    if not isinstance(value, str) or not value.strip():
        return []
    text = value.strip()
    if "/" in text or "." in text:
        return _url_keys(text if "://" in text else f"https://{text}")
    if platform == "youtube":
        return [f"yt:{text.lower()}"]
    if platform == "reddit":
        return [f"reddit:{text.removeprefix('t3_').lower()}"]
    return [f"id:{text.lower()}"]


def post_keys(post: EvidenceItem) -> list[str]:
    platform = post.platform.value if post.platform else ""
    meta = post.metadata
    values = [post.url, meta.get("video_id"), meta.get("post_id"), meta.get("native_id")]
    return list(dict.fromkeys(k for v in values for k in ref_keys(platform, v)))


def comment_hints(comment: EvidenceItem) -> list[str]:
    """What the provider says about a comment's post: `post_url`, `video_id` and `post_id`."""
    platform = comment.platform.value if comment.platform else ""
    meta = comment.metadata
    values = [meta.get("post_url"), meta.get("video_id"), meta.get("post_id")]
    return list(dict.fromkeys(k for v in values for k in ref_keys(platform, v)))


def comment_refs(comment: EvidenceItem) -> list[str]:
    """The hints, then the comment's own URL, which on TikTok, YouTube and Instagram contains the
    post's."""
    platform = comment.platform.value if comment.platform else ""
    return list(dict.fromkeys([*comment_hints(comment), *ref_keys(platform, comment.url)]))


@dataclass(frozen=True)
class Parent:
    key: str  # the post's first key, so every way of naming a post groups together
    text: str


NO_PARENT = Parent("", "")


class PostIndex:
    """The collected posts of a run, found by any of their keys."""

    def __init__(self, posts: Iterable[EvidenceItem]) -> None:
        self._by_key: dict[str, Parent] = {}
        for post in posts:
            keys = post_keys(post)
            if not keys:
                continue
            parent = Parent(keys[0], post.text)
            for key in keys:
                self._by_key.setdefault(key, parent)

    def parent_of(self, comment: EvidenceItem) -> Parent:
        for ref in comment_refs(comment):
            if ref in self._by_key:
                return self._by_key[ref]
        # A post that was not collected still groups its comments, with no text to show.
        hints = comment_hints(comment)
        return Parent(hints[0], "") if hints else NO_PARENT


def parents_of(comments: Sequence[EvidenceItem], posts: Iterable[EvidenceItem]) -> list[Parent]:
    index = PostIndex(posts)
    return [index.parent_of(comment) for comment in comments]
