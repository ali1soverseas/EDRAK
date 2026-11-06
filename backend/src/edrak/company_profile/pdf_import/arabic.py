"""Spot Arabic text that came out of a PDF in the wrong order.

PDFs often store Arabic in visual order, and an extractor that does not undo that returns it
backwards, either letter by letter (the words are mirrored) or word by word (the sentence reads from
the wrong end). These checks only notice that; they do not repair anything. The caller retries the
page with a different extractor and warns when that is still backwards.
"""

from __future__ import annotations

import re
import unicodedata

from edrak.agents.customer_trends.utils.text import search_key

_ARABIC_RUN = re.compile(r"[؀-ۿݐ-ݿ]+")
MIN_ARABIC_LETTERS = 20  # fewer than this and a page is not worth judging
MIN_WORD_LETTERS = 3

# Very common words, folded the way search_key folds them, and the same words spelled backwards.
_COMMON = frozenset(search_key(w) for w in ("في", "من", "على", "إلى", "الى", "عن", "التي", "الذي", "هذا", "هذه", "مع", "كما", "أن", "ان", "ذلك", "التى"))
_COMMON_BACKWARDS = frozenset(w[::-1] for w in _COMMON)
_ARTICLE = search_key("ال")


def arabic_words(text: str) -> list[str]:
    """The Arabic words in `text`, folded so presentation forms, diacritics and alef variants compare equal."""
    return [search_key(w) for w in _ARABIC_RUN.findall(unicodedata.normalize("NFKC", text))]


def arabic_letter_count(text: str) -> int:
    return sum(len(w) for w in _ARABIC_RUN.findall(unicodedata.normalize("NFKC", text)))


def letters_reversed(text: str) -> bool:
    """Words that look mirrored: the article "ال" at the end of words instead of the start."""
    words = [w for w in arabic_words(text) if len(w) >= MIN_WORD_LETTERS]
    forward = sum(w.startswith(_ARTICLE) for w in words) + sum(w in _COMMON for w in words)
    backwards = sum(w.endswith(_ARTICLE[::-1]) for w in words) + sum(w in _COMMON_BACKWARDS for w in words)
    return backwards >= 3 and backwards > 2 * forward


def words_reversed(text: str, reference: str) -> bool:
    """Whether the lines of `reference` appear in `text` with their words in the opposite order.

    `reference` is a second extraction of the same page. A line of three or more words that is found
    contiguous in `text` counts as agreeing, and found only back to front counts as reversed.
    """
    stream = " ".join(arabic_words(text))
    agree = flipped = 0
    for line in reference.splitlines():
        words = arabic_words(line)
        if len(words) < 3:
            continue
        if " ".join(words) in stream:
            agree += 1
        elif " ".join(reversed(words)) in stream:
            flipped += 1
    return flipped >= 1 and flipped > agree
