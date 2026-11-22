"""Arabic for brief prose, remembered once.

English stays the stored brief. A background task translates it after save.
An Arabic read substitutes sentences already in the cache and does not load
the model. Chat, alerts, and plans stay English.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import logging
import re
import time
from typing import Any, Awaitable, Callable

import aiosqlite
from fastapi import Request

from edrak.db.util import fetch_all, loads
from edrak.i18n.fixed_ar import fixed_arabic

logger = logging.getLogger(__name__)

Translator = Callable[[list[str]], list[str]]
_TASKS: set[asyncio.Task[None]] = set()

# Generated prose. A key that is not in this set is never sent to the model.
PROSE_KEYS = frozenset({
    "title", "headline", "summary", "why_visual", "statement", "text",
     "signal", "interpretation",
    "caveat", "focus", "rationale", "description", "why", "why_not",
    "objective", "explanation", "highlight", "supported_by",
    "executive_summary", "would_change_if", "how_to_capture", "early_warning",
    "mitigation", "deliverable", "success_measure", "value_text", "message",
    "reason", "gap", "proposal", "content", "next_steps", "resources",
    # Brief / deck fields that the UI shows as generated prose.
    "what_we_would_do", "trade_offs", "cost_of_waiting", "why_it_matters",
    "definition", "collapsed_reason", "subtitle", "framing", "analysis_title",
    "limitations", "decision_question", "evidence_gaps",     "support",
    "themes", "impacts",
    # Shown on brief cards and missed by the earlier key list.
    "label", "guardrail", "missing_information", "conflicts",
})

# Unused PROSE_KEYS:
# extracted_fact: a generated sentence that is not a quote. Quoted text next to an excerpt or a URL is left in English.

# Workstream names are generated labels. Other names (people, companies) are not.
ACTION_KEYS = PROSE_KEYS | {"name", "validation", "go_criteria", "kill_criteria", "owner_role"}
FACT_KEYS = PROSE_KEYS | {"value"}

# Quoted source text and anything the person typed.
SKIP_KEYS = frozenset({
    "excerpt", "quote", "page_text", "source_span", "question", "goal", "url",
    "source_url", "email", "note",
})

# Full run dumps embedded in the brief, and the finding sentences. The findings stay
# in English. Walking them re-translates every worker finding and blocks Arabic brief loads.
SKIP_BRANCHES = frozenset({
    "request", "orchestration", "verification", "cross_signal", "decision_analysis", "result",
    "findings", "key_findings",
})

# Parameters of a fixed sentence that are themselves generated.
PROSE_PARAMS = frozenset({"gap", "reason", "summary", "statement", "title"})

# Soft budget so a cold Arabic open returns instead of hanging the page.
TRANSLATE_BUDGET_S = 8.0
TRANSLATE_CHUNK = 24

_ARABIC = re.compile(r"[\u0600-\u06FF]")
_LATIN = re.compile(r"[A-Za-z]")
_ENUM = re.compile(r"[a-z0-9_.:/-]+")
_NUMBER = re.compile(r"[\d\s.,%+\-/:]+")
_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+")
_ID = re.compile(r"[0-9a-fA-F-]{16,}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|(?<=\n)\s*")
_PROTECT = re.compile(
    r"\b(?:[A-Z]{2,}[A-Za-z0-9+./-]{0,24}|[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+|"
    r"[A-Z][a-zA-Z0-9&+./-]{1,}(?:\s+[A-Z][a-zA-Z0-9&+./-]{1,}){1,4}|"
    r"[A-Z][a-zA-Z0-9&+./-]{1,})\b"
)
_ACRONYM = re.compile(r"\b[A-Z]{2,}s?\b")
# Product codes stay in English: CoWoS, CoWoS-L, HBM3e, GB200, B200, Q1, FY2026.
_PRODUCT = re.compile(
    r"\b(?:"
    r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+"
    r"|[A-Z][A-Za-z]*\d[A-Za-z0-9]*"
    r"|[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+"
    r")\b"
)
# Words the model translates into the wrong technical sense, and the section label it drops.
_GLOSSARY = (
    ("bottlenecks", "اختناقات"),
    ("bottleneck", "اختناق"),
    ("stage gate", "بوابة المراحل"),
    ("on premise", "في المقر"),
    ("decisions", "القرارات"),
)
# Title-case English that is not a company or product name.
_ORDINARY = frozenset(
    """
    list price number annual revenue release total potential current future however recent
    supply chain loss growth delay offer build partner expand implement negotiate engage
    maintain hold watch lead accelerate differentiate launch managed service cloud partners
    availability rack sales buffer inventory enterprise prioritize capacity demand market
    share software hardware platform using focus create commit what which when where while
    with from into over after before under their this that these those have been were was
    are not but for and the per via its our may can will also new more most some any all
    one two key net raw end top out use used using both each such other than then them you
    your high low paid plan seat month customers subscribers billed unit publicly reported
    quarter slower direct native upcoming limited strong rapid quantitative strategic
    """.split()
)
_EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_WORD = re.compile(r"[A-Za-z]{3,}")
# Argos copies "the" and turns a lone "a" into the letter alef. Arabic uses ال and has no word for "a".
_ARTICLES = {"the": "ال", "an": "", "a": ""}
# Invisible Arabic letter. dir="auto" takes direction from the first strong character,
# so a translation that starts with NVIDIA or GPU would otherwise run left to right.
_RTL = "\u061c"
_AR_MARK = "ع:"
_NOTE_LISTS = ("missing_information", "conflicts")

_PROFILE = frozenset({"offerings", "target_customers", "aliases"})
_NAME_KEYS = frozenset({"entity", "company", "competitor", "publisher"})


def ui_language(request: Request) -> str:
    """The language the page is showing. English unless the client says Arabic."""
    raw = request.headers.get("x-edrak-lang") or request.query_params.get("lang") or "en"
    return "ar" if str(raw).lower().startswith("ar") else "en"


def skip_string(text: str) -> bool:
    """True when a string is not a sentence a model should translate."""
    value = text.strip()
    if len(value) < 2:
        return True
    if _URL.fullmatch(value) or _EMAIL.fullmatch(value) or _ID.fullmatch(value) or _DATE.match(value):
        return True
    if _NUMBER.fullmatch(value) or _ENUM.fullmatch(value):
        return True
    arabic = _ARABIC.findall(value)
    latin = _LATIN.findall(value)
    return bool(arabic) and len(arabic) >= len(latin)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_translator(texts: list[str]) -> list[str]:
    from edrak.briefing.translate import translate_many

    return translate_many(texts)


def _split_sentences(text: str) -> list[str]:
    """Break a paragraph into units Argos handles better; short strings stay whole."""
    value = text.strip()
    if len(value) < 90 or value.count(". ") + value.count("! ") + value.count("? ") < 1:
        return [value]
    parts = [p.strip() for p in _SENTENCE.split(value) if p and p.strip()]
    return parts if len(parts) > 1 else [value]


_PLACEHOLDER_BASE = 810001


def _marker(index: int) -> str:
    """A number the translator copies unchanged. It cannot occur in the sentence."""
    return str(_PLACEHOLDER_BASE + index)


def _fresh_marker(text: str, used: set[str], index: int) -> tuple[str, int]:
    while _marker(index) in text or _marker(index) in used:
        index += 1
    token = _marker(index)
    used.add(token)
    return token, index + 1


def _mask_acronyms(text: str) -> tuple[str, dict[str, str]]:
    """Swap all-caps tokens for markers. A string replace puts the English token back."""
    token_for: dict[str, str] = {}
    used: set[str] = set()
    index = 0
    for match in _ACRONYM.finditer(text):
        word = match.group(0)
        if word in token_for:
            continue
        token_for[word], index = _fresh_marker(text, used, index)
    if not token_for:
        return text, {}
    pieces: list[str] = []
    cursor = 0
    for match in _ACRONYM.finditer(text):
        end = match.end()
        if text[end:end + 2] in ("'s", "’s"):
            end += 2
        pieces.append(text[cursor:match.start()])
        pieces.append(token_for[match.group(0)])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), {token: word for word, token in token_for.items()}


def _opens_sentence(text: str, start: int, span: str) -> bool:
    """A short title-case word that opens a sentence is ordinary English, not a name."""
    if " " in span or not span.istitle() or len(span) >= 12:
        return False
    before = text[:start].rstrip(" \t\n\r\"'“”‘’(")
    return not before or before[-1] in ".!?"


def _name_span(text: str, start: int, end: int, span: str) -> tuple[int, str] | None:
    """Where a capital-word match should stay in English. Drops a word that only opens a sentence."""
    opener = span.split()[0]
    if not _opens_sentence(text, start, opener):
        return start, span
    if " " not in span:
        return None
    rest = start + len(opener)
    while rest < end and text[rest] == " ":
        rest += 1
    if rest >= end:
        return None
    return rest, text[rest:end]


def _kept_words(text: str, *, titles: bool = True) -> list[str]:
    """Tokens that must stay in English: all-caps, and, outside options, a capitalised word."""
    found: list[str] = []
    seen: set[str] = set()

    def add(span: str) -> None:
        if span and span not in seen:
            seen.add(span)
            found.append(span)

    for match in _ACRONYM.finditer(text):
        add(match.group(0))
    for match in _PRODUCT.finditer(text):
        add(match.group(0))
    if not titles:
        return found
    for match in _PROTECT.finditer(text):
        kept = _name_span(text, match.start(), match.end(), match.group(0))
        if kept:
            add(kept[1])
    return found


def _mask_parentheses(text: str, mapping: dict[str, str], used: set[str], index: int) -> tuple[str, int]:
    """Swap each parenthetical for a marker, innermost first. The original text is put back."""
    pattern = re.compile(r"\([^()]*\)")
    while True:
        match = pattern.search(text)
        if match is None:
            return text, index
        token, index = _fresh_marker(text, used, index)
        mapping[token] = match.group(0)
        text = text[:match.start()] + token + text[match.end():]


def _mask_brackets(text: str, mapping: dict[str, str], used: set[str], index: int) -> tuple[str, int]:
    """Swap a label such as [Intel] for one marker, brackets included."""
    pattern = re.compile(r"\[[^\[\]]*\]")
    while True:
        match = pattern.search(text)
        if match is None:
            return text, index
        token, index = _fresh_marker(text, used, index)
        mapping[token] = match.group(0)
        text = text[: match.start()] + token + text[match.end() :]


def _mask_glossary(text: str, mapping: dict[str, str], used: set[str], index: int) -> tuple[str, int]:
    """Swap a term the model mistranslates for a marker. The marker comes back as Arabic."""
    for phrase, arabic in _GLOSSARY:
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
        while True:
            match = pattern.search(text)
            if match is None:
                break
            token, index = _fresh_marker(text, used, index)
            mapping[token] = arabic
            text = text[: match.start()] + token + text[match.end() :]
    return text, index


def _mask_products(text: str, mapping: dict[str, str], used: set[str], index: int) -> tuple[str, int]:
    """Swap a product code such as CoWoS-L for a marker. It is put back in English."""
    while True:
        match = _PRODUCT.search(text)
        if match is None:
            return text, index
        token, index = _fresh_marker(text, used, index)
        mapping[token] = match.group(0)
        text = text[: match.start()] + token + text[match.end() :]


def _surface_names(text: str) -> list[str]:
    """A capitalised word used as a name in the middle of a sentence, such as Azure."""
    found: list[str] = []
    for match in _PROTECT.finditer(text):
        kept = _name_span(text, match.start(), match.end(), match.group(0))
        if kept is None:
            continue
        span = kept[1]
        if " " in span or span.isupper() or len(span) < 3:
            continue
        if span.casefold() in _ORDINARY:
            continue
        found.append(span)
    return found


def _protect_names(text: str, names: list[str], *, titles: bool = True) -> tuple[str, dict[str, str]]:
    """Swap parentheticals, acronyms, and capital words for markers. A string replace puts them back.

    Option text passes titles=False. A title-case phrase such as "Rack Availability" is wording,
    so it is translated. Acronyms, product codes, known names, and parentheses still stay in English.
    """
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    mapping: dict[str, str] = {}
    used: set[str] = set()
    text, index = _mask_glossary(text, mapping, used, 0)
    text, index = _mask_brackets(text, mapping, used, index)
    text, index = _mask_parentheses(text, mapping, used, index)
    text, acronym_map = _mask_acronyms(text)
    mapping.update(acronym_map)
    used = set(mapping)
    text, index = _mask_products(text, mapping, used, 0)
    out = text
    used = set(mapping)
    index = 0
    ordered = sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True)
    for name in ordered:
        if re.fullmatch(r"[A-Za-z0-9.+-]+", name):
            pattern = re.compile(rf"\b{re.escape(name)}\b")
            if pattern.search(out) is None:
                continue
            token, index = _fresh_marker(out, used, index)
            mapping[token] = name
            out = pattern.sub(token, out)
            continue
        if name not in out:
            continue
        token, index = _fresh_marker(out, used, index)
        mapping[token] = name
        out = out.replace(name, token)
    if not titles:
        return out, mapping
    pieces: list[str] = []
    cursor = 0
    for match in _PROTECT.finditer(out):
        span = match.group(0)
        if match.start() > 0 and out[match.start() - 1] == "(" and match.end() < len(out) and out[match.end()] == ")":
            continue
        kept = _name_span(out, match.start(), match.end(), span)
        if kept is None:
            continue
        keep_at, span = kept
        if " " not in span and span.casefold() in _ORDINARY:
            continue
        token, index = _fresh_marker(out, used, index)
        mapping[token] = span
        pieces.append(out[cursor:keep_at])
        pieces.append(token)
        cursor = match.end()
    pieces.append(out[cursor:])
    return "".join(pieces), mapping


def _normalize_markers(text: str) -> str:
    """Argos rewrites digits into Arabic numerals and escapes quotes. Put both back."""
    return html.unescape(text or "").translate(_EASTERN_DIGITS)


def _placeholder_patterns(token: str) -> list[str]:
    """The marker, and the same digits with the first digit moved to the end.

    The model rewrites 810001 as 10001 8. An extra 0 between digits is allowed.
    """
    sep = r"[\s,.\u060c\u066c]*"
    patterns = [r"(?<!\d)" + sep.join(re.escape(digit) for digit in token) + r"(?!\d)"]
    if len(token) < 4:
        return patterns
    body, head = token[1:], token[0]
    rotated = r"(?<!\d)"
    for index, digit in enumerate(body):
        rotated += re.escape(digit) + sep
        if index != len(body) - 1:
            rotated += r"0?" + sep
    rotated += re.escape(head) + r"(?!\d)"
    patterns.append(rotated)
    return patterns


def _restore_names(text: str, mapping: dict[str, str]) -> str:
    out = _normalize_markers(text)
    ordered = sorted(mapping.items(), key=lambda item: (len(item[0]), item[0]), reverse=True)
    for token, name in ordered:
        if token.isdigit():
            for pattern in _placeholder_patterns(token):
                out = re.sub(pattern, lambda _match, value=name: value, out)
            continue
        inner = token[1:-1] if token.startswith("(") and token.endswith(")") else token
        for variant in (token, f"( {inner} )", f"){inner}(", f") {inner} ("):
            out = out.replace(variant, name)
        out = re.sub(rf"(?<![A-Za-z]){re.escape(inner)}(?![A-Za-z])", name, out)
    return _strip_brackets(out)


def _strip_brackets(text: str) -> str:
    """Drop leftover name-tag brackets. The name itself is restored before this."""
    if not text or not re.search(r"[<>＜＞]", text):
        return text
    cleaned = re.sub(r"[<>＜＞]", "", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _translate_articles(text: str) -> str:
    """Replace a leftover English article. 'the' becomes ال. 'a' and 'an' are removed.

    An all-caps article is a kept token, so THE stays THE.
    """
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.isupper():
            return word
        arabic = _ARTICLES[word.lower()]
        return f" {arabic} " if arabic else " "

    out = re.sub(r"\b(?:the|an|a)\b", replace, text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _splice_word_translations(text: str, translate: Translator) -> str:
    """When Argos copies a short label unchanged, translate its ordinary words one by one."""
    out = _translate_articles(text)
    words = [word for word in dict.fromkeys(_WORD.findall(out)) if word.lower() not in _ARTICLES]
    if not words:
        return out
    rendered = translate(words)
    for word, arabic in zip(words, rendered):
        arabic = _normalize_markers(str(arabic or "")).strip()
        if not _ARABIC.search(arabic) or arabic == word:
            continue
        out = re.sub(rf"\b{re.escape(word)}\b", lambda _match, value=arabic: value, out)
    return out


def _keep_acronyms(source: str, arabic: str, *, titles: bool = True) -> str:
    """Put any kept English token the translation lost back onto the end of an Arabic sentence."""
    if not arabic or not _ARABIC.search(arabic):
        return arabic
    missing = [
        token
        for token in _kept_words(source, titles=titles)
        if token not in arabic and token.casefold() not in _ORDINARY
    ]
    if not missing:
        return arabic
    return arabic.rstrip() + " " + " ".join(missing)


def _keeps_acronyms(source: str, arabic: str, *, titles: bool = True) -> bool:
    """A token like GPU stays as written. A translation that drops one is not stored."""
    return all(
        token in arabic
        for token in _kept_words(source, titles=titles)
        if token.casefold() not in _ORDINARY
    )


def _with_ar_mark(arabic: str) -> str:
    """Put ع: in front of a translation before it is stored."""
    text = (arabic or "").strip()
    if not text or text.startswith(_AR_MARK):
        return text
    return _AR_MARK + text


def _mark_rtl(arabic: str) -> str:
    """Put an invisible Arabic letter in front so a leading English word keeps the line right to left."""
    text = arabic or ""
    if not text or text.startswith(_RTL):
        return text
    return _RTL + text


_DASH = re.compile(r"[\u002d\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d]+")


def _soften_dashes(text: str) -> str:
    """Turn a hyphen into a space. A product code such as CoWoS-L keeps its hyphen."""

    def replace(match: re.Match[str]) -> str:
        start, end = match.start(), match.end()
        left = start
        while left > 0 and text[left - 1].isalnum():
            left -= 1
        right = end
        while right < len(text) and text[right].isalnum():
            right += 1
        token = text[left:start] + text[end:right]
        if re.search(r"\d", token) or len(re.findall(r"[A-Z]", token)) >= 2:
            return "-"
        return " "

    return _DASH.sub(replace, text)


def _prepare_source(text: str) -> str:
    """The sentence sent to the translator. The cache is still keyed by the original.

    Dashes become spaces outside parentheses, except inside a product code.
    Text inside parentheses is left as written.
    """
    cleaned = _strip_repeated_arabic(text)
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\([^()]*\)", cleaned):
        pieces.append(_soften_dashes(cleaned[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_soften_dashes(cleaned[cursor:]))
    return re.sub(r"[ \t]{2,}", " ", "".join(pieces)).strip()


def _strip_repeated_arabic(text: str) -> str:
    """Drop a repeated word, a run of the same Arabic letter, and tatweel.

    A doubled Arabic letter stays, so a real word is left intact. The same English
    or Arabic word written twice in a row is kept once.
    """
    if not text:
        return text
    collapsed = text
    if _ARABIC.search(collapsed):
        collapsed = re.sub(r"\u0640+", "", collapsed)
        collapsed = re.sub(r"([\u0600-\u06FF])\1{2,}", r"\1", collapsed)
    kept: list[str] = []
    for word in collapsed.split():
        if kept and word.casefold() == kept[-1].casefold():
            continue
        kept.append(word)
    return re.sub(r"[ \t]{2,}", " ", " ".join(kept)).strip()


_NUM = re.compile(r"\d+")
_CURRENCY = re.compile(r"دولار|يورو|جنيه")
_MONEY = re.compile(r"\$|\bdollars?\b|\bUSD\b|\beuros?\b|\bpounds?\b", re.IGNORECASE)


def _invented_number(source: str, arabic: str) -> bool:
    """A digit run the English sentence does not contain. Placeholder debris looks like one."""
    allowed = set(_NUM.findall(source))
    return any(number not in allowed for number in _NUM.findall(arabic))


def _invented_currency(source: str, arabic: str) -> bool:
    """The model turns a price sentence into a dollar amount the English never stated."""
    return bool(_CURRENCY.search(arabic)) and _MONEY.search(source) is None


def _usable_arabic(source: str, arabic: str, *, titles: bool = True) -> bool:
    """Reject blank, unchanged, Latin-only, invented figures, or a dropped English token."""
    value = (arabic or "").strip()
    if not value or value == source.strip():
        return False
    if re.search(r"[<>＜＞]|ZQK|\(زك|(?<![A-Za-z])a\d+\b|(?<!\d)810\d{3}(?!\d)", value, re.IGNORECASE):
        return False
    if _invented_number(source, value) or _invented_currency(source, value):
        return False
    if not _ARABIC.findall(value) or not _keeps_acronyms(source, value, titles=titles):
        return False
    return True


_CONTENT = re.compile(r"[A-Za-z]{3,}")
_SMALL = frozenset(
    "the and for with that from this are was were has have its into over per not but than then also while their they you our out any may can will via".split()
)


def _strip_invented(source: str, arabic: str) -> str:
    """Drop a figure or a currency word the English sentence never stated."""
    allowed = set(_NUM.findall(source))
    text = _NUM.sub(lambda match: match.group(0) if match.group(0) in allowed else "", arabic)
    if _MONEY.search(source) is None:
        text = _CURRENCY.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _thin_translation(source: str, arabic: str, *, folded: bool = False) -> bool:
    """The Arabic kept too few words, so a clause was dropped."""
    english = [word for word in _CONTENT.findall(source) if word.lower() not in _SMALL]
    arabic_words = [word for word in arabic.split() if word]
    if len(english) < 4:
        return False
    if folded and len(english) <= 8:
        return len(arabic_words) < len(english)
    return len(arabic_words) < len(english) * 0.75


def _put_back(arabic: str, mapping: dict[str, str]) -> str:
    """Append a name or glossary term the model deleted with its marker."""
    if not arabic or not _ARABIC.search(arabic):
        return arabic
    missing = [name for name in mapping.values() if name not in arabic]
    missing = [name for name in missing if not any(name != other and name in other for other in missing)]
    if not missing:
        return arabic
    return arabic.rstrip() + " " + " ".join(dict.fromkeys(missing))


def _settle(source: str, protected: str, arabic: str, mapping: dict[str, str], translate: Translator, *, folded: bool) -> str | None:
    """Pick a sentence translation, or the word fallback when that sentence drops text or invents a figure."""

    def accept(text: str) -> str:
        restored = _strip_repeated_arabic(_restore_names(text, mapping).strip())
        restored = re.sub(r"\[\s*\]|\]\s*\[", " ", restored)
        if _ARABIC.search(restored):
            restored = _translate_articles(restored)
        restored = _keep_acronyms(source, restored, titles=not folded)
        restored = _put_back(restored, mapping)
        restored = _strip_invented(source, restored)
        return _strip_repeated_arabic(restored)

    first = accept(arabic)
    first_ok = _usable_arabic(source, first, titles=not folded)
    needs_words = not first_ok or (folded and _thin_translation(source, first, folded=folded))
    if not needs_words:
        return first
    spliced = accept(_splice_word_translations(protected, translate))
    if _usable_arabic(source, spliced, titles=not folded) and not (folded and _thin_translation(source, spliced, folded=folded)):
        return spliced
    if first_ok:
        return first
    if _usable_arabic(source, spliced, titles=not folded):
        return spliced
    return None


def _translate_units(units: list[str], translate: Translator, names: list[str], fold: set[str] | None = None) -> dict[str, str]:
    """Translate sentence units with name protection. Returns unit -> arabic."""
    if not units:
        return {}
    fold = fold or set()
    out: dict[str, str] = {}
    direct: list[str] = []
    for unit in units:
        if unit not in fold or ", " not in unit:
            direct.append(unit)
            continue
        clauses = [clause.strip() for clause in unit.split(", ") if clause.strip()]
        if len(clauses) < 2:
            direct.append(unit)
            continue
        parts = _translate_units(clauses, translate, names, set(clauses))
        if all(clause in parts for clause in clauses):
            out[unit] = "، ".join(parts[clause] for clause in clauses)
        else:
            direct.append(unit)
    if not direct:
        return out
    prepared: list[str] = []
    maps: list[dict[str, str]] = []
    sources: list[str] = []
    folded_units = [unit in fold for unit in direct]
    for unit, folded_unit in zip(direct, folded_units):
        source = _prepare_source(unit)
        protected, mapping = _protect_names(source, names, titles=not folded_unit)
        if folded_unit:
            protected = protected.lower()
        sources.append(source)
        prepared.append(protected)
        maps.append(mapping)
    raw = [""] * len(prepared)
    send = [(index, text) for index, text in enumerate(prepared) if _LATIN.search(text)]
    if send:
        translated = translate([text for _index, text in send])
        for (index, _text), arabic in zip(send, translated):
            raw[index] = arabic
    for unit, source, protected, arabic, mapping, folded_unit in zip(direct, sources, prepared, raw, maps, folded_units):
        settled = _settle(source, protected, str(arabic or ""), mapping, translate, folded=folded_unit)
        if settled:
            out[unit] = settled
    return out


async def _load_cached(db: aiosqlite.Connection, hashes: dict[str, str], folded: set[str] | None = None) -> dict[str, str]:
    """Stored Arabic for these exact strings. A row that dropped an acronym is ignored."""
    found: dict[str, str] = {}
    pending = list(hashes.values())
    for start in range(0, len(pending), 80):
        chunk = pending[start:start + 80]
        marks = ",".join("?" for _ in chunk)
        rows = await fetch_all(
            db,
            f"SELECT source_hash, source_text, translated_text FROM text_translations "
            f"WHERE target_lang = 'ar' AND source_hash IN ({marks})",
            chunk,
        )
        for row in rows:
            source = str(row["source_text"] or "")
            arabic = str(row["translated_text"] or "").strip()
            option = source in (folded or ())
            if re.search(r"[<>＜＞]", arabic):
                _protected, mapping = _protect_names(_prepare_source(source), [], titles=not option)
                arabic = _restore_names(arabic, mapping)
            arabic = _strip_repeated_arabic(arabic)
            if hashes.get(source) == row["source_hash"] and _usable_arabic(source, arabic, titles=not option):
                found[source] = arabic
    return found


async def _store(db: aiosqlite.Connection, source: str, arabic: str, cache_key: str | None = None) -> None:
    text = _with_ar_mark(arabic)
    await db.execute(
        "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) "
        "VALUES (?, 'ar', ?, ?) "
        "ON CONFLICT(source_hash, target_lang) DO UPDATE SET translated_text = excluded.translated_text",
        (cache_key or _hash(source), source, text),
    )


async def translate_cached(
    db: aiosqlite.Connection,
    texts: list[str],
    *,
    translate: Translator | None = None,
    names: list[str] | None = None,
    fill: bool = False,
    budget_s: float | None = None,
    fold: set[str] | None = None,
) -> dict[str, str]:
    """Arabic for each string. Reads only use the cache. `fill` translates what is missing."""
    unique = list(dict.fromkeys(text for text in texts if text and not skip_string(text)))
    if not unique:
        return {}
    fixed_now: dict[str, str] = {}
    rest: list[str] = []
    for text in unique:
        hit = fixed_arabic(text)
        if hit:
            fixed_now[text] = hit
        else:
            rest.append(text)
    folded = set(fold or ())

    def cache_hash(text: str) -> str:
        # v3 ignores rows from before names, glossary terms, and invented figures were kept out.
        marker = "\noption" if text in folded else ""
        return _hash(text + marker + "\nv3")

    found = await _load_cached(db, {text: cache_hash(text) for text in rest}, folded) if rest else {}
    found.update(fixed_now)
    missing = [text for text in rest if text not in found]
    if not missing:
        return _present(found, fixed_now)

    # Sentence rows let a paragraph be reused without translating it again as one blob.
    unit_for: dict[str, list[str]] = {}
    for text in missing:
        parts = _split_sentences(text)
        unit_for[text] = parts
        if text in folded:
            folded.update(parts)
    units = list(dict.fromkeys(unit for parts in unit_for.values() for unit in parts))
    unit_found = await _load_cached(db, {unit: cache_hash(unit) for unit in units}, folded)

    ran = False
    stopped_early = False
    stored = 0
    started_at = 0.0
    to_run: list[str] = []
    if fill:
        to_run = [unit for unit in units if unit not in unit_found]
        if to_run:
            ran = True
            started_at = time.monotonic()
            logger.warning(
                "\n======== ARABIC TRANSLATION START ========\n"
                "Sentences to translate: %d\n"
                "Already in the cache: %d\n"
                "==========================================",
                len(to_run),
                len(unit_found),
            )
            translator = translate or _default_translator
            known_names = list(names or [])
            deadline = None if budget_s is None else time.monotonic() + max(0.5, budget_s)
            for start in range(0, len(to_run), TRANSLATE_CHUNK):
                if deadline is not None and time.monotonic() >= deadline:
                    stopped_early = True
                    break
                batch = to_run[start:start + TRANSLATE_CHUNK]
                try:
                    mapped = await asyncio.to_thread(_translate_units, batch, translator, known_names, folded)
                except Exception:
                    logger.exception("Brief translation failed")
                    stopped_early = True
                    break
                for source, arabic in mapped.items():
                    unit_found[source] = arabic
                    await _store(db, source, arabic, cache_hash(source))
                    stored += 1
                await db.commit()

    stored_join = False
    for text, parts in unit_for.items():
        if not all(part in unit_found for part in parts):
            continue
        joined = " ".join(unit_found[part] for part in parts).strip()
        if not _usable_arabic(text, joined, titles=text not in folded):
            continue
        found[text] = joined
        if fill and len(parts) > 1:
            await _store(db, text, joined, cache_hash(text))
            stored_join = True
    if stored_join:
        await db.commit()
    if ran:
        rejected = sum(1 for unit in to_run if unit not in unit_found)
        still_english = sum(1 for text in unique if text not in found)
        logger.warning(
            "\n======== ARABIC TRANSLATION STOP ========\n"
            "Elapsed: %.1f s\n"
            "Stored: %d\n"
            "Rejected: %d\n"
            "Still English: %d of %d shown strings\n"
            "Stopped early: %s\n"
            "=========================================",
            time.monotonic() - started_at,
            stored,
            rejected,
            still_english,
            len(unique),
            "yes" if stopped_early else "no",
        )
    return _present(found, fixed_now)


def _present(found: dict[str, str], fixed: dict[str, str]) -> dict[str, str]:
    """Model Arabic is marked. A hardcoded sentence is already Arabic, so it is returned as written."""
    return {source: arabic if source in fixed else _mark_rtl(_with_ar_mark(arabic)) for source, arabic in found.items()}


def _bilingual(value: dict[str, Any]) -> bool:
    return isinstance(value.get("en"), str) and isinstance(value.get("ar"), str)


def _shell(obj: dict[str, Any], key: str) -> bool:
    """A fixed sentence already has a dictionary key, so its stored English stays put."""
    return bool(
        (key == "message" and obj.get("message_key"))
        or (key == "text" and obj.get("text_key"))
        or (key == "note" and obj.get("note_key"))
        or (key == "activity" and obj.get("activity_key"))
        or (key == "why_it_matters" and obj.get("why_key"))
        or (key == "what_changed" and obj.get("what_key"))
        or (key == "title" and obj.get("title_key"))
        or (key == "body" and obj.get("body_key"))
        or (key == "limits" and obj.get("limits_key"))
        or (key == "content" and obj.get("content_key"))
        or (key == "collapsed_reason" and obj.get("collapsed_reason_key"))
        or (key == "explanation" and obj.get("explanation_key"))
        or (key == "subtitle" and obj.get("subtitle_key"))
        or (key == "framing" and obj.get("framing_key"))
        or (key == "caveat" and obj.get("caveat_key"))
        or (key == "why_visual" and obj.get("why_visual_key"))
        or (key == "signal" and obj.get("signal_key"))
        or (key == "support" and obj.get("support_key"))
        or (key == "statement" and obj.get("statement_key"))
        or (key == "headline" and obj.get("headline_key"))
        or (key == "label" and obj.get("label_key"))
        or (key == "message" and not obj.get("generated") and "message_key" in obj)
    )


def _translate_key(obj: dict[str, Any], key: str, keys: frozenset[str]) -> bool:
    if key in SKIP_KEYS or key not in keys:
        return False
    if _shell(obj, key):
        return False
    if key == "message" and not obj.get("generated"):
        return False
    if key == "content" and obj.get("role") not in (None, "assistant"):
        return False
    if key == "rationale" and ({"decision_id", "recorded_at", "option_label"} & obj.keys()):
        return False
    if key == "description" and (_PROFILE & obj.keys()):
        return False
    if key == "text" and ({"excerpt", "source_url", "page_text"} & obj.keys()):
        return False
    if key == "value" and "metric_id" in obj:
        return False
    if key == "name" and "actions" not in obj:
        return False
    return True


def _collect_names(value: Any, found: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_names(item, found)
        return
    if not isinstance(value, dict) or _bilingual(value):
        return
    for name, item in value.items():
        if isinstance(item, str):
            if name in _NAME_KEYS and item.strip():
                found.append(item.strip())
            found.extend(_surface_names(item))
            continue
        if name == "company_profile" and isinstance(item, dict):
            profile_name = item.get("name")
            if isinstance(profile_name, str) and profile_name.strip():
                found.append(profile_name.strip())
            for alias in item.get("aliases") or []:
                if isinstance(alias, str) and alias.strip():
                    found.append(alias.strip())
            for competitor in item.get("competitors") or []:
                if isinstance(competitor, str) and competitor.strip():
                    found.append(competitor.strip())
                elif isinstance(competitor, dict) and isinstance(competitor.get("name"), str):
                    found.append(competitor["name"].strip())
        else:
            _collect_names(item, found)


def _collect(value: Any, keys: frozenset[str], found: list[str], fold: set[str] | None = None, *, lower: bool = False) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, keys, found, fold, lower=lower)
        return
    if not isinstance(value, dict) or _bilingual(value):
        return
    if _PROFILE & value.keys() and "statement" not in value:
        # Company profile fields stay as the person saved them. Nested prose still walks.
        for name, item in value.items():
            if name in _PROFILE or name in {"description", "name", "notes"}:
                continue
            _collect(item, keys, found, fold, lower=lower)
        return
    for name, item in value.items():
        if name in SKIP_BRANCHES:
            _collect_verifier_notes(name, item, found)
            continue
        option_branch = name == "options" and _option_list(item)
        if isinstance(item, str) and _translate_key(value, name, keys):
            found.append(item)
            if lower and fold is not None:
                fold.add(item)
        elif isinstance(item, list) and name in keys and name != "notes" and all(isinstance(x, str) for x in item):
            if not (name == "content" and value.get("role") not in (None, "assistant")):
                found.extend(item)
                if lower and fold is not None:
                    fold.update(item)
        elif option_branch:
            _collect(item, keys, found, fold, lower=True)
        else:
            _collect(item, keys, found, fold, lower=lower)


def _option_list(item: Any) -> bool:
    """The three decision options. Their wording is lowercased before translation."""
    return isinstance(item, list) and any(
        isinstance(entry, dict) and ("option_id" in entry or (entry.get("key") in {"A", "B", "C"} and "title" in entry))
        for entry in item
    )


def _note_lists(summary: Any) -> dict[str, list[str]]:
    if not isinstance(summary, dict):
        return {}
    found: dict[str, list[str]] = {}
    for key in _NOTE_LISTS:
        values = summary.get(key)
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            found[key] = values
    return found


def _collect_verifier_notes(name: str, item: Any, found: list[str]) -> None:
    """The card drawer shows these two lists. The rest of the run tree stays untranslated."""
    if name != "verification" or not isinstance(item, dict):
        return
    for values in _note_lists(item.get("control_summary")).values():
        found.extend(values)


def _apply(value: Any, keys: frozenset[str], mapped: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_apply(item, keys, mapped) for item in value]
    if not isinstance(value, dict) or _bilingual(value):
        return value
    if _PROFILE & value.keys() and "statement" not in value:
        out = dict(value)
        for name, item in value.items():
            if name in _PROFILE or name in {"description", "name", "notes"}:
                continue
            out[name] = _apply(item, keys, mapped)
        return out
    out: dict[str, Any] = {}
    for name, item in value.items():
        if name in SKIP_BRANCHES:
            out[name] = _apply_verifier_notes(name, item, mapped)
            continue
        if isinstance(item, str) and _translate_key(value, name, keys):
            out[name] = mapped.get(item, item)
        elif isinstance(item, list) and name in keys and name != "notes" and all(isinstance(x, str) for x in item):
            if name == "content" and value.get("role") not in (None, "assistant"):
                out[name] = item
            else:
                out[name] = [mapped.get(x, x) for x in item]
        else:
            out[name] = _apply(item, keys, mapped)
    return out


def _apply_verifier_notes(name: str, item: Any, mapped: dict[str, str]) -> Any:
    if name != "verification" or not isinstance(item, dict):
        return item
    summary = item.get("control_summary")
    lists = _note_lists(summary)
    if not lists:
        return item
    out = dict(item)
    copied = dict(summary)
    for key, values in lists.items():
        copied[key] = [mapped.get(value, value) for value in values]
    out["control_summary"] = copied
    return out


def _prepared(payload: Any, keys: frozenset[str] | set[str] | None) -> tuple[frozenset[str], list[str], list[str], set[str]]:
    chosen = frozenset(keys) if keys is not None else PROSE_KEYS
    texts: list[str] = []
    names: list[str] = []
    fold: set[str] = set()
    _collect(payload, chosen, texts, fold)
    _collect_names(payload, names)
    return chosen, texts, names, fold


async def fill_arabic(
    db: aiosqlite.Connection,
    payload: Any,
    *,
    translate: Translator | None = None,
    keys: frozenset[str] | set[str] | None = None,
) -> None:
    """Translate a brief into the cache. The caller does not wait on a page request."""
    _chosen, texts, names, fold = _prepared(payload, keys)
    await translate_cached(db, texts, translate=translate, names=names, fill=True, fold=fold)


async def localize_generated(
    db: aiosqlite.Connection,
    payload: Any,
    lang: str,
    *,
    keys: frozenset[str] | set[str] | None = None,
    fill: bool = True,
) -> Any:
    """A copy of `payload` with cached Arabic when `lang` is ar.

    `fill` translates what is missing. Lists and notices pass fill=False so a
    title already translated for the report is reused and nothing new is sent.
    """
    if lang != "ar":
        return payload
    chosen, texts, names, fold = _prepared(payload, keys)
    mapped = await translate_cached(db, texts, names=names, fill=fill, fold=fold)
    return _apply(payload, chosen, mapped)


async def localize_text(db: aiosqlite.Connection, text: str, lang: str) -> str:
    if lang != "ar" or skip_string(text):
        return text
    mapped = await translate_cached(db, [text], fill=False)
    return mapped.get(text, text)


def schedule_brief_translation(document: dict[str, Any], *, translate: Translator | None = None) -> asyncio.Task[None]:
    """Translate one saved brief without blocking the run that just stored it."""
    payload = copy.deepcopy(document)
    task = asyncio.get_running_loop().create_task(_fill_document(payload, translate=translate))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def schedule_brief_backfill() -> asyncio.Task[None]:
    """Translate briefs already in the database. Startup does not wait for this."""
    task = asyncio.get_running_loop().create_task(_backfill_briefs())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _fill_document(document: dict[str, Any], *, translate: Translator | None = None) -> None:
    from edrak.db.connection import get_db

    try:
        async with get_db() as db:
            await fill_arabic(db, document, translate=translate)
    except Exception:
        logger.exception("Could not translate a brief")


async def _backfill_briefs() -> None:
    from edrak.db.connection import get_db

    try:
        async with get_db() as db:
            rows = await fetch_all(db, "SELECT id, document FROM briefs")
    except Exception:
        logger.exception("Could not list briefs for Arabic backfill")
        return
    for row in rows:
        document = loads(row["document"], {})
        if not isinstance(document, dict):
            continue
        await _fill_document(document)


async def search_sources(db: aiosqlite.Connection, like: str) -> list[str]:
    """Stored English sentences whose Arabic translation matches a search."""
    rows = await fetch_all(
        db,
        "SELECT source_text FROM text_translations WHERE target_lang = 'ar' AND translated_text LIKE ? LIMIT 40",
        (like,),
    )
    return [row["source_text"] for row in rows]


# Kept so a test can replace the model without importing the weights.
def translator_for_tests(fn: Translator) -> Callable[[list[str]], Awaitable[list[str]]]:
    async def _call(texts: list[str]) -> list[str]:
        return fn(texts)

    return _call
