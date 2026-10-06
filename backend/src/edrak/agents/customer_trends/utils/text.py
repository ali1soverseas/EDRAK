"""Unicode-safe text helpers: normalization, hashing, language detection, truncation."""

import hashlib
import re
import unicodedata

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 0

ELLIPSIS = chr(0x2026)


def _char_class(*ranges: tuple[int, int]) -> str:
    """A regex character class from code point ranges, kept as hex so it stays readable."""
    return "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]"


_DIACRITICS = re.compile(
    _char_class(
        (0x0610, 0x061A),
        (0x064B, 0x065F),
        (0x0670, 0x0670),
        (0x06D6, 0x06DC),
        (0x06DF, 0x06E4),
        (0x06E7, 0x06E8),
        (0x06EA, 0x06ED),
    )
)
_TATWEEL = chr(0x0640)
_WHITESPACE = re.compile(r"\s+")
_NOISE = re.compile(r"https?://\S+|www\.\S+|[@#]\w+")
_ARABIC_LETTER = re.compile(
    _char_class(
        (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)
    )
)
_PERSIAN_ONLY_LETTERS = re.compile(
    _char_class((0x067E, 0x067E), (0x0686, 0x0686), (0x0698, 0x0698), (0x06AF, 0x06AF))
)
_ARABIC_SCRIPT_LOOKALIKES = frozenset({"fa", "ur", "ps", "sd", "ug", "ku"})
_MIN_LETTERS = 15
_SHORT_TEXT_LETTERS = 20
_SHORT_TEXT_MIN_CONFIDENCE = 0.9


def normalize_text(text: str) -> str:
    """NFKC, no Arabic diacritics or tatweel, collapsed whitespace. For hashing, not display."""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    return _WHITESPACE.sub(" ", text).strip()


def content_hash(text: str) -> str:
    """SHA-1 of the normalized, case-folded text: equal content hashes mean duplicates."""
    folded = normalize_text(text).casefold()
    return hashlib.sha1(folded.encode("utf-8"), usedforsecurity=False).hexdigest()


def evidence_id(source_key: str) -> str:
    """Deterministic 16-hex id from a provider-independent source key."""
    return hashlib.sha1(source_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def detect_language(text: str) -> str | None:
    """ISO 639-1 code, or None when the text has no letters or is too short to tell."""
    cleaned = _NOISE.sub(" ", text)
    letters = [c for c in cleaned if c.isalpha()]
    if not letters:
        return None
    arabic_share = sum(1 for c in letters if _ARABIC_LETTER.match(c)) / len(letters)
    short = len(letters) < _SHORT_TEXT_LETTERS
    if short and arabic_share >= 0.5:
        return "ar"
    if len(letters) < _MIN_LETTERS:
        return None
    try:
        best = detect_langs(cleaned)[0]
    except LangDetectException:
        return "ar" if arabic_share >= 0.5 else None
    if short and best.prob < _SHORT_TEXT_MIN_CONFIDENCE:
        return None
    code = str(best.lang).split("-")[0]
    lookalike = code in _ARABIC_SCRIPT_LOOKALIKES and arabic_share >= 0.8
    if lookalike and not _PERSIAN_ONLY_LETTERS.search(cleaned):
        return "ar"
    return code


def truncate(text: str, limit: int, suffix: str = ELLIPSIS) -> str:
    """Cut to at most `limit` characters, ending with `suffix` when something was removed."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix
