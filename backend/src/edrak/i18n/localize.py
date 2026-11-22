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

# Full run dumps embedded in the brief. The UI never shows these trees; walking
# them re-translates every worker finding and blocks Arabic brief loads.
SKIP_BRANCHES = frozenset({
    "request", "orchestration", "verification", "cross_signal", "decision_analysis", "result",
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
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")
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


def _marker(index: int) -> str:
    """A letter code in parentheses. Argos copies it and still translates the sentence."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    chars: list[str] = []
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        chars.append(alphabet[rem])
    return "(ZQK" + "".join(reversed(chars)) + ")"


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
    before = text[:start].rstrip(" \t\n\r\"'“”‘’([")
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


def _kept_words(text: str) -> list[str]:
    """Tokens that must stay in English: all-caps, and any word that starts with a capital."""
    found: list[str] = []
    seen: set[str] = set()

    def add(span: str) -> None:
        if span and span not in seen:
            seen.add(span)
            found.append(span)

    for match in _ACRONYM.finditer(text):
        add(match.group(0))
    for match in _PROTECT.finditer(text):
        kept = _name_span(text, match.start(), match.end(), match.group(0))
        if kept:
            add(kept[1])
    return found


def _protect_names(text: str, names: list[str]) -> tuple[str, dict[str, str]]:
    """Swap acronyms and words that start with a capital for markers. A string replace puts the English back."""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text, mapping = _mask_acronyms(text)
    out = text
    used = set(mapping)
    index = 0
    ordered = sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True)
    for name in ordered:
        if name not in out:
            continue
        token, index = _fresh_marker(out, used, index)
        mapping[token] = name
        out = out.replace(name, token)
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


def _restore_names(text: str, mapping: dict[str, str]) -> str:
    out = _normalize_markers(text)
    for token, name in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        inner = token[1:-1] if token.startswith("(") and token.endswith(")") else token
        for variant in (token, f"( {inner} )", f"){inner}(", f") {inner} ("):
            out = out.replace(variant, name)
        out = re.sub(rf"(?<![A-Za-z]){re.escape(inner)}(?![A-Za-z])", name, out)
    return out


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


def _keep_acronyms(source: str, arabic: str) -> str:
    """Put any kept English token the translation lost back onto the end of an Arabic sentence."""
    if not arabic or not _ARABIC.search(arabic):
        return arabic
    missing = [token for token in _kept_words(source) if token not in arabic]
    if not missing:
        return arabic
    return arabic.rstrip() + " " + " ".join(missing)


def _keeps_acronyms(source: str, arabic: str) -> bool:
    """A token like GPU or Intel stays as written. A translation that drops one is not stored."""
    return all(token in arabic for token in _kept_words(source))


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


def _usable_arabic(source: str, arabic: str) -> bool:
    """Reject blank, unchanged, Latin-only, or results that dropped a kept English token."""
    value = (arabic or "").strip()
    if not value or value == source.strip():
        return False
    if not _ARABIC.findall(value) or not _keeps_acronyms(source, value):
        return False
    return True


def _translate_units(units: list[str], translate: Translator, names: list[str]) -> dict[str, str]:
    """Translate sentence units with name protection. Returns unit -> arabic."""
    if not units:
        return {}
    prepared: list[str] = []
    maps: list[dict[str, str]] = []
    for unit in units:
        protected, mapping = _protect_names(unit, names)
        prepared.append(protected)
        maps.append(mapping)
    raw = translate(prepared)
    out: dict[str, str] = {}
    for unit, protected, arabic, mapping in zip(units, prepared, raw, maps):
        restored = _restore_names(str(arabic or ""), mapping).strip()
        if _ARABIC.search(restored):
            restored = _translate_articles(restored)
        restored = _keep_acronyms(unit, restored)
        if not _usable_arabic(unit, restored):
            restored = _keep_acronyms(unit, _restore_names(_splice_word_translations(protected, translate), mapping).strip())
        if _usable_arabic(unit, restored):
            out[unit] = restored
    return out


async def _load_cached(db: aiosqlite.Connection, hashes: dict[str, str]) -> dict[str, str]:
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
            if hashes.get(source) == row["source_hash"] and _usable_arabic(source, arabic):
                found[source] = arabic
    return found


async def _store(db: aiosqlite.Connection, source: str, arabic: str) -> None:
    text = _with_ar_mark(arabic)
    await db.execute(
        "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) "
        "VALUES (?, 'ar', ?, ?) "
        "ON CONFLICT(source_hash, target_lang) DO UPDATE SET translated_text = excluded.translated_text",
        (_hash(source), source, text),
    )


async def translate_cached(
    db: aiosqlite.Connection,
    texts: list[str],
    *,
    translate: Translator | None = None,
    names: list[str] | None = None,
    fill: bool = False,
    budget_s: float | None = None,
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
    found = await _load_cached(db, {text: _hash(text) for text in rest}) if rest else {}
    found.update(fixed_now)
    missing = [text for text in rest if text not in found]
    if not missing:
        return _present(found, fixed_now)

    # Sentence rows let a paragraph be reused without translating it again as one blob.
    unit_for = {text: _split_sentences(text) for text in missing}
    units = list(dict.fromkeys(unit for parts in unit_for.values() for unit in parts))
    unit_found = await _load_cached(db, {unit: _hash(unit) for unit in units})

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
                    mapped = await asyncio.to_thread(_translate_units, batch, translator, known_names)
                except Exception:
                    logger.exception("Brief translation failed")
                    stopped_early = True
                    break
                for source, arabic in mapped.items():
                    unit_found[source] = arabic
                    await _store(db, source, arabic)
                    stored += 1
                await db.commit()

    stored_join = False
    for text, parts in unit_for.items():
        if not all(part in unit_found for part in parts):
            continue
        joined = " ".join(unit_found[part] for part in parts).strip()
        if not _usable_arabic(text, joined):
            continue
        found[text] = joined
        if fill and len(parts) > 1:
            await _store(db, text, joined)
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
        if name in _NAME_KEYS and isinstance(item, str) and item.strip():
            found.append(item.strip())
        elif name == "company_profile" and isinstance(item, dict):
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


def _collect(value: Any, keys: frozenset[str], found: list[str]) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, keys, found)
        return
    if not isinstance(value, dict) or _bilingual(value):
        return
    if _PROFILE & value.keys() and "statement" not in value:
        # Company profile fields stay as the person saved them. Nested prose still walks.
        for name, item in value.items():
            if name in _PROFILE or name in {"description", "name", "notes"}:
                continue
            _collect(item, keys, found)
        return
    for name, item in value.items():
        if name in SKIP_BRANCHES:
            _collect_verifier_notes(name, item, found)
            continue
        if isinstance(item, str) and _translate_key(value, name, keys):
            found.append(item)
        elif isinstance(item, list) and name in keys and name != "notes" and all(isinstance(x, str) for x in item):
            if not (name == "content" and value.get("role") not in (None, "assistant")):
                found.extend(item)
        else:
            _collect(item, keys, found)


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


def _prepared(payload: Any, keys: frozenset[str] | set[str] | None) -> tuple[frozenset[str], list[str], list[str]]:
    chosen = frozenset(keys) if keys is not None else PROSE_KEYS
    texts: list[str] = []
    names: list[str] = []
    _collect(payload, chosen, texts)
    _collect_names(payload, names)
    return chosen, texts, names


async def fill_arabic(
    db: aiosqlite.Connection,
    payload: Any,
    *,
    translate: Translator | None = None,
    keys: frozenset[str] | set[str] | None = None,
) -> None:
    """Translate a brief into the cache. The caller does not wait on a page request."""
    _chosen, texts, names = _prepared(payload, keys)
    await translate_cached(db, texts, translate=translate, names=names, fill=True)


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
    chosen, texts, _names = _prepared(payload, keys)
    mapped = await translate_cached(db, texts, fill=fill)
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
