"""Arabic for generated prose, remembered once.

English requests return the stored text and do not load the model. An Arabic
request translates each distinct generated sentence, stores it, and reuses that
row the next time the same sentence is shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any, Awaitable, Callable

import aiosqlite
from fastapi import Request

from edrak.db.util import fetch_all

Translator = Callable[[list[str]], list[str]]

# Generated prose. A key that is not in this set is never sent to the model.
PROSE_KEYS = frozenset({
    "title", "headline", "summary", "summary_text", "why_visual", "statement",
    "extracted_fact", "thesis", "signal", "interpretation", "implications",
    "caveat", "focus", "rationale", "description", "text", "why", "why_not",
    "objective", "explanation", "highlight", "supported_by", "business_rationale",
    "executive_summary", "would_change_if", "how_to_capture", "early_warning",
    "mitigation", "deliverable", "success_measure", "value_text", "message",
    "reason", "gap", "proposal", "content", "next_steps", "resources",
    # Brief / deck fields that the UI shows as generated prose.
    "what_we_would_do", "trade_offs", "cost_of_waiting", "why_it_matters",
    "definition", "collapsed_reason", "subtitle", "framing", "analysis_title",
    "limitations", "decision_question", "evidence_gaps", "support",
    "patterns", "themes", "impacts",
})
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
    "request", "orchestration", "verification", "cross_signal", "decision_analysis",
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
    r"[A-Z][a-zA-Z0-9&+./-]{1,}(?:\s+[A-Z][a-zA-Z0-9&+./-]{1,}){1,4})\b"
)

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


def _protect_names(text: str, names: list[str]) -> tuple[str, dict[str, str]]:
    """Swap known and likely proper nouns for placeholders Argos will leave alone."""
    mapping: dict[str, str] = {}
    out = text
    ordered = sorted({n.strip() for n in names if n and len(n.strip()) >= 2}, key=len, reverse=True)
    for i, name in enumerate(ordered):
        if name not in out:
            continue
        token = f"⟦N{i}⟧"
        mapping[token] = name
        out = out.replace(name, token)
    pieces: list[str] = []
    cursor = 0
    for i, match in enumerate(_PROTECT.finditer(out)):
        span = match.group(0)
        if span.startswith("⟦"):
            continue
        # Sentence starters that are ordinary English words stay unprotected when alone.
        if " " not in span and span.istitle() and match.start() == 0 and len(span) < 12:
            continue
        token = f"⟦P{i}⟧"
        mapping[token] = span
        pieces.append(out[cursor:match.start()])
        pieces.append(token)
        cursor = match.end()
    pieces.append(out[cursor:])
    return "".join(pieces), mapping


def _restore_names(text: str, mapping: dict[str, str]) -> str:
    out = text
    for token, name in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        out = out.replace(token, name)
        # Argos sometimes strips the brackets or inserts spaces around the marker.
        bare = token.strip("⟦⟧")
        out = out.replace(bare, name)
        out = out.replace(f"⟦ {bare} ⟧", name).replace(f"⟦{bare} ⟧", name).replace(f"⟦ {bare}⟧", name)
    return out


def _usable_arabic(source: str, arabic: str) -> bool:
    """Reject blank, unchanged, or Latin-only results so we do not cache junk."""
    value = (arabic or "").strip()
    if not value or value == source.strip():
        return False
    return bool(_ARABIC.findall(value))


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
    for unit, arabic, mapping in zip(units, raw, maps):
        restored = _restore_names(str(arabic or ""), mapping).strip()
        if _usable_arabic(unit, restored):
            out[unit] = restored
    return out


async def translate_cached(
    db: aiosqlite.Connection,
    texts: list[str],
    *,
    translate: Translator | None = None,
    names: list[str] | None = None,
    budget_s: float = TRANSLATE_BUDGET_S,
) -> dict[str, str]:
    """Arabic for each string. A string already in the cache is not translated again."""
    unique = list(dict.fromkeys(text for text in texts if text and not skip_string(text)))
    if not unique:
        return {}
    hashes = {text: _hash(text) for text in unique}
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
            arabic = str(row["translated_text"] or "").strip()
            if hashes.get(row["source_text"]) == row["source_hash"] and arabic:
                found[row["source_text"]] = arabic
    missing = [text for text in unique if text not in found]
    if not missing:
        return found

    # Translate at sentence granularity so long paragraphs cache and read better.
    unit_for: dict[str, list[str]] = {text: _split_sentences(text) for text in missing}
    units = list(dict.fromkeys(u for parts in unit_for.values() for u in parts))
    unit_hashes = {u: _hash(u) for u in units}
    unit_found: dict[str, str] = {}
    need = list(unit_hashes.values())
    for start in range(0, len(need), 80):
        chunk = need[start:start + 80]
        marks = ",".join("?" for _ in chunk)
        rows = await fetch_all(
            db,
            f"SELECT source_hash, source_text, translated_text FROM text_translations "
            f"WHERE target_lang = 'ar' AND source_hash IN ({marks})",
            chunk,
        )
        for row in rows:
            arabic = str(row["translated_text"] or "").strip()
            if unit_hashes.get(row["source_text"]) == row["source_hash"] and arabic:
                unit_found[row["source_text"]] = arabic

    to_run = [u for u in units if u not in unit_found]
    translator = translate or _default_translator
    known_names = list(names or [])
    deadline = time.monotonic() + max(0.5, budget_s)
    for start in range(0, len(to_run), TRANSLATE_CHUNK):
        if time.monotonic() >= deadline:
            break
        batch = to_run[start:start + TRANSLATE_CHUNK]
        try:
            mapped = await asyncio.to_thread(_translate_units, batch, translator, known_names)
        except Exception:
            break
        for source, arabic in mapped.items():
            unit_found[source] = arabic
            await db.execute(
                "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) "
                "VALUES (?, 'ar', ?, ?) "
                "ON CONFLICT(source_hash, target_lang) DO UPDATE SET translated_text = excluded.translated_text",
                (_hash(source), source, arabic),
            )
        await db.commit()

    for text, parts in unit_for.items():
        if all(part in unit_found for part in parts):
            joined = " ".join(unit_found[part] for part in parts).strip()
            if _usable_arabic(text, joined):
                found[text] = joined
                if len(parts) > 1:
                    await db.execute(
                        "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) "
                        "VALUES (?, 'ar', ?, ?) "
                        "ON CONFLICT(source_hash, target_lang) DO UPDATE SET translated_text = excluded.translated_text",
                        (_hash(text), text, joined),
                    )
    if any(len(parts) > 1 for parts in unit_for.values()):
        await db.commit()
    return found


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
            continue
        if isinstance(item, str) and _translate_key(value, name, keys):
            found.append(item)
        elif isinstance(item, list) and name in keys and name != "notes" and all(isinstance(x, str) for x in item):
            if not (name == "content" and value.get("role") not in (None, "assistant")):
                found.extend(item)
        else:
            _collect(item, keys, found)


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
            out[name] = item
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


async def localize_generated(
    db: aiosqlite.Connection,
    payload: Any,
    lang: str,
    *,
    keys: frozenset[str] | set[str] | None = None,
    translate: Translator | None = None,
) -> Any:
    """A copy of `payload` with generated prose in Arabic when `lang` is ar."""
    if lang != "ar":
        return payload
    chosen = frozenset(keys) if keys is not None else PROSE_KEYS
    texts: list[str] = []
    _collect(payload, chosen, texts)
    names: list[str] = []
    _collect_names(payload, names)
    mapped = await translate_cached(db, texts, translate=translate, names=names)
    return _apply(payload, chosen, mapped)


async def localize_text(
    db: aiosqlite.Connection,
    text: str,
    lang: str,
    *,
    translate: Translator | None = None,
) -> str:
    if lang != "ar" or skip_string(text):
        return text
    mapped = await translate_cached(db, [text], translate=translate)
    return mapped.get(text, text)


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
