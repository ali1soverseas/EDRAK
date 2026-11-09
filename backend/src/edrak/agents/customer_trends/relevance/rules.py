"""Stage A: free rules that drop comments with nothing to analyze. No model, and no judgement on
language: an Arabic comment and an English one are treated alike."""

import re
from collections import Counter
from collections.abc import Callable, Sequence

from edrak.agents.customer_trends.relevance.models import (
    DUPLICATE,
    EMPTY,
    LINK_ONLY,
    MENTIONS_ONLY,
    SPAM,
    TOO_SHORT,
    Comment,
    Verdict,
)
from edrak.agents.customer_trends.relevance.normalize import normalize

MIN_WORDS = 2
SPAM_MIN_LENGTH = 6  # letters and digits; shorter text is left to the short-comment rule
SPAM_SHARE = 0.8  # one character making up this share of the letters and digits
SPAM_UNIT = 3  # a block of up to this many characters repeated over and over
SPAM_REPEATS = 4  # how many times the block must repeat

_SHORTENERS = r"(?:bit\.ly|t\.co|tinyurl\.com|wa\.me|linktr\.ee|goo\.gl|fb\.me|youtu\.be)"
_LINK = re.compile(rf"(?:https?://|www\.)\S+|\b{_SHORTENERS}/\S*", re.IGNORECASE)
_MENTION_OR_TAG = re.compile(r"[@#][\w.]+")
_WORD = re.compile(r"[^\W_]+")

Decision = Verdict | None
HasTerm = Callable[[str], bool]


def has_text(text: str) -> bool:
    """Whether there is a letter or a digit at all. Emoji and punctuation do not count."""
    return any(c.isalnum() for c in text)


def without_links(text: str) -> str:
    return _LINK.sub(" ", text)


def without_links_and_tags(text: str) -> str:
    return _MENTION_OR_TAG.sub(" ", without_links(text))


def is_character_spam(text: str) -> bool:
    """Mostly one character ("aaaaaaa", "ههههههههه") or a short block repeated over and over
    ("hahahahaha", "lolololol"). Only letters and digits count, so "wow!!!!!!!" is not spam."""
    chars = "".join(c for c in text.casefold() if c.isalnum())
    if len(chars) < SPAM_MIN_LENGTH:
        return False
    if Counter(chars).most_common(1)[0][1] / len(chars) >= SPAM_SHARE:
        return True
    for unit in range(1, SPAM_UNIT + 1):
        if len(chars) >= unit * SPAM_REPEATS and chars == (chars[:unit] * len(chars))[: len(chars)]:
            return True
    return False


def word_count(text: str) -> int:
    return len(_WORD.findall(without_links_and_tags(text)))


def standalone_verdict(text: str) -> Decision:
    """The drop that needs only this comment: nothing to read, only a link, only mentions or tags,
    or character spam."""
    if not has_text(text):
        return Verdict("irrelevant", EMPTY, "a")
    if _LINK.search(text) and not has_text(without_links_and_tags(text)):
        return Verdict("irrelevant", LINK_ONLY, "a")
    if _MENTION_OR_TAG.search(text) and not has_text(without_links_and_tags(text)):
        return Verdict("irrelevant", MENTIONS_ONLY, "a")
    if is_character_spam(text):
        return Verdict("irrelevant", SPAM, "a")
    return None


def too_short(text: str, has_term: HasTerm) -> Decision:
    """Under two words, unless the comment names the brand, a product or a competitor."""
    if word_count(text) >= MIN_WORDS or has_term(text):
        return None
    return Verdict("irrelevant", TOO_SHORT, "a")


class Duplicates:
    """Remembers the first comment of each platform and normalized text and counts the repeats."""

    def __init__(self) -> None:
        self._first: dict[tuple[str, str], str] = {}
        self.repeats: Counter[str] = Counter()

    def see(self, comment: Comment) -> str | None:
        """The id of the first comment with this text, when this one repeats it; else None."""
        key = (comment.platform, normalize(comment.text))
        first = self._first.setdefault(key, comment.id)
        if first == comment.id:
            return None
        self.repeats[first] += 1
        return first


def stage_a(
    comments: Sequence[Comment], has_term: HasTerm
) -> tuple[dict[str, Verdict], Duplicates]:
    """Verdicts for the comments Stage A drops, and the duplicate counts.

    Comments are taken in the order given, so the first of a set of duplicates is the one kept.
    A comment judged earlier only registers its text. The short-comment rule spares a comment
    that holds a match term, which Stage B then marks relevant.
    """
    verdicts: dict[str, Verdict] = {}
    seen = Duplicates()
    for comment in comments:
        if comment.already:
            seen.see(comment)
            continue
        dropped = standalone_verdict(comment.text)
        if dropped is None:
            first = seen.see(comment)
            if first is not None:
                dropped = Verdict("irrelevant", DUPLICATE, "a", {"duplicate_of": first})
        if dropped is None:
            dropped = too_short(comment.text, has_term)
        if dropped is not None:
            verdicts[comment.id] = dropped
    return verdicts, seen
