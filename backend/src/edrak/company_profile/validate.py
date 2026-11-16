"""Check the model's reply with plain code before a person sees it. No model call.

The model can cite a source that was not sent, repeat itself, spell the same Arabic word two ways, or
invent an industry. This keeps only what is cited, tidy and allowed, and says what is missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from edrak.agents.customer_trends.utils.text import search_key
from edrak.company_profile.models import FIELD_NAMES, ProfileExtraction, ProfileFields, SourceRef
from edrak.core.config import settings
from edrak.db.repositories.company import INDUSTRY_LABELS

OTHER_INDUSTRY = "other"
# The values the industry chips offer. "health" is an older spelling of "healthcare".
ALLOWED_INDUSTRIES: tuple[str, ...] = tuple(k for k in INDUSTRY_LABELS if k != "health")
_INDUSTRY_BY_NAME = {name.casefold(): label.casefold() for name, label in INDUSTRY_LABELS.items()}
_INDUSTRY_BY_NAME.update({label.casefold(): label.casefold() for label in INDUSTRY_LABELS.values()})

_ID_NOISE = re.compile(r"[\[\]\s]+")


@dataclass
class Validated:
    profile: ProfileFields
    field_sources: dict[str, list[str]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clean_text(value: Optional[str], limit: int) -> str:
    """`value` with whitespace collapsed, cut to `limit` characters."""
    text = " ".join((value or "").split())
    if len(text) > limit:
        text = text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def clean_list(
    values: Sequence[str], *, max_items: int, max_chars: int, exclude: Sequence[str] = ()
) -> list[str]:
    """Trimmed, capped items with repeats removed.

    Two items are the same when `search_key` agrees, so "Cairo" and "cairo", or the same Arabic word
    with and without diacritics or with another alef form, count once. The first spelling wins.
    """
    seen = {search_key(e) for e in exclude}
    kept: list[str] = []
    for value in values:
        text = clean_text(value, max_chars)
        key = search_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return kept[:max_items]


def map_industry(value: Optional[str]) -> Optional[str]:
    """An allowed industry value for `value`; `other` when it names something not on the list."""
    text = " ".join((value or "").split())
    if not text:
        return None
    return _INDUSTRY_BY_NAME.get(text.casefold(), OTHER_INDUSTRY)


def _citations(raw: Sequence[str], valid_ids: set[str]) -> list[str]:
    """The cited ids that were sent to the model, once each, in the order cited."""
    kept: list[str] = []
    for item in raw:
        source_id = _ID_NOISE.sub("", str(item)).upper()
        if source_id in valid_ids and source_id not in kept:
            kept.append(source_id)
    return kept


def validate_extraction(extraction: ProfileExtraction, sources: Sequence[SourceRef]) -> Validated:
    """The extraction cleaned, with every kept field backed by a source that was sent.

    A field is kept only when it has a value and at least one valid citation. A value with no valid
    citation is dropped, since nothing shows the sources say it, and the field becomes a gap.
    """
    max_items = settings.PROFILE_IMPORT_MAX_LIST_ITEMS
    max_chars = settings.PROFILE_IMPORT_MAX_ITEM_CHARS
    raw = extraction.profile
    valid_ids = {s.id for s in sources}

    name = clean_text(raw.name, max_chars)
    values: dict[str, object] = {
        "name": name or None,
        "aliases": clean_list(raw.aliases, max_items=max_items, max_chars=max_chars, exclude=[name]),
        "notes": clean_text(raw.notes, settings.PROFILE_IMPORT_MAX_NOTES_CHARS) or None,
        "products": clean_list(raw.products, max_items=max_items, max_chars=max_chars),
        "industry": map_industry(raw.industry),
        "markets": clean_list(raw.markets, max_items=max_items, max_chars=max_chars),
        "goals": clean_list(raw.goals, max_items=max_items, max_chars=max_chars),
    }

    result = Validated(profile=ProfileFields())
    uncited: list[str] = []
    for name_ in FIELD_NAMES:
        value = values[name_]
        if not value:
            result.gaps.append(name_)
            continue
        cited = _citations(extraction.field_sources.get(name_, []), valid_ids)
        if not cited:
            result.gaps.append(name_)
            uncited.append(name_)
            continue
        setattr(result.profile, name_, value)
        result.field_sources[name_] = cited

    if uncited:
        result.warnings.append(
            "Left out because no source that was read backs them: " + ", ".join(uncited) + "."
        )
    return result
