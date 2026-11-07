"""Arabic for generated prose, remembered once.

English requests return the stored text and do not load the model. An Arabic
request translates each distinct generated sentence, stores it, and reuses that
row the next time the same sentence is shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
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
})
# Workstream names are generated labels. Other names (people, companies) are not.
ACTION_KEYS = PROSE_KEYS | {"name"}
FACT_KEYS = PROSE_KEYS | {"value"}

# Quoted source text and anything the person typed.
SKIP_KEYS = frozenset({
    "excerpt", "quote", "page_text", "source_span", "question", "goal", "url",
    "source_url", "email", "note",
})

# Parameters of a fixed sentence that are themselves generated.
PROSE_PARAMS = frozenset({"gap", "reason", "summary", "statement", "title"})

_ARABIC = re.compile(r"[\u0600-\u06FF]")
_LATIN = re.compile(r"[A-Za-z]")
_ENUM = re.compile(r"[a-z0-9_.:/-]+")
_NUMBER = re.compile(r"[\d\s.,%+\-/:]+")
_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+")
_ID = re.compile(r"[0-9a-fA-F-]{16,}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_PROFILE = frozenset({"offerings", "target_customers", "aliases"})


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


async def translate_cached(
    db: aiosqlite.Connection,
    texts: list[str],
    *,
    translate: Translator | None = None,
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
    if missing:
        try:
            translated = await asyncio.to_thread(translate or _default_translator, missing)
        except Exception:
            # A model failure leaves the stored English in place and does not cache it.
            return found
        for source, arabic in zip(missing, translated):
            if not str(arabic or "").strip():
                continue
            found[source] = arabic
            await db.execute(
                "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) "
                "VALUES (?, 'ar', ?, ?) "
                "ON CONFLICT(source_hash, target_lang) DO UPDATE SET translated_text = excluded.translated_text",
                (_hash(source), source, arabic),
            )
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
    mapped = await translate_cached(db, texts, translate=translate)
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
