"""Turn verified facts into facts to store: keys mapped, duplicates merged, conflicts grouped.

Plain code, no model call. Within a run, facts with the same category, key and value are one fact,
kept from the best source. Facts with the same key and different values are all kept and share a
conflict group; none is chosen. Against facts stored by earlier runs, anything a person approved or
edited is left exactly as it is: a new value for its key is added as a suggestion in the same group.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from pydantic import ValidationError

from edrak.company_research.categories import OTHER
from edrak.company_research.extraction.models import ExtractionStats, NewKey
from edrak.company_research.extraction.verify import Verified
from edrak.company_research.keys import build_index, normalize_phrase
from edrak.company_research.models import (
    CompanyFact,
    Confidence,
    FactKeySpec,
    FactStatus,
    NewFact,
    SourceTier,
    ValueType,
)
from edrak.db.util import new_id

# Best first. A lower number wins; a third-party source never beats a company one.
TIER_RANK = {SourceTier.OWN_SITE: 0, SourceTier.OWN_PDF: 1, SourceTier.OWN_SOCIAL: 2, SourceTier.THIRD_PARTY: 3}
CONFIDENCE_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}

# Keys a company has several of: more than one value is expected, so different values are not a
# conflict. Everything else (and any key not listed here) is taken to have one value.
MULTI_VALUED: frozenset[tuple[str, str]] = frozenset(
    (category, key)
    for category, keys in {
        "leadership": ("founder", "board_member", "executive_team_member"),
        "ownership_and_funding": ("shareholder", "subsidiary", "investor", "funding_round"),
        "financials": ("annual_report",),
        "customers": ("customer_name", "customer_segment", "case_study"),
        "partners": ("partner", "distributor", "supplier"),
        "locations": ("branch", "factory", "warehouse", "country_presence", "address"),
        "team": ("team_member", "department", "open_positions"),
        "pricing": ("pricing_plan", "price", "discount", "transaction_fee"),
        "technology": ("tech_stack", "platform", "integration", "infrastructure", "patent", "ai_capability"),
        "certifications": ("iso_certification", "certification", "quality_standard", "security_certification", "professional_membership"),
        "regulatory": ("license", "regulator", "compliance"),
        "news_and_events": ("press_release", "event", "product_launch", "media_mention", "expansion"),
        "awards": ("award", "ranking", "recognition"),
        "history": ("milestone", "former_name", "acquisition"),
    }.items()
    for key in keys
)

_KEY_NOISE = re.compile(r"[^a-z0-9]+")
_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")
_DIGIT_COMMA = re.compile(r"(?<=\d),(?=\d)")
_ARABIC_DIGITS = {0x0660 + i: ord("0") + i for i in range(10)} | {0x06F0 + i: ord("0") + i for i in range(10)}
_MAX_KEY_CHARS = 60
_EMPHASIS = re.compile(r"\*\*|__")
_TABLE_BAR = re.compile(r"\s*\|\s*")


@dataclass
class Candidate:
    """A verified fact in the form the rules below work on."""

    category: str
    proposed_category: Optional[str]
    key: str
    label: str
    value: str
    value_type: ValueType
    tier: SourceTier
    url: str
    pdf_page: Optional[int]
    quote: str
    confidence: Confidence
    exact: bool
    order: int
    mapped: bool = False

    def group(self) -> tuple[str, str, str]:
        """What a conflict is judged within: the category and key, and for "other" the proposed category."""
        return self.category, self.key, normalize_phrase(self.proposed_category or "") if self.category == OTHER else ""

    def identity(self) -> str:
        return normalize_value(self.value, self.value_type)

    def quality(self) -> tuple[int, int, int, int]:
        """Lower is better: tier, then confidence, then an exact quote over a close one, then the order found."""
        return TIER_RANK[self.tier], CONFIDENCE_RANK[self.confidence], 0 if self.exact else 1, self.order


# ------------------------------------------------------------------ values and keys

# Words that, around a number, only say what it is measured in. Each maps to one form, "" for noise.
_AMOUNT_WORDS = {
    "$": "usd", "usd": "usd", "dollar": "usd", "dollars": "usd", "us": "", "€": "eur", "eur": "eur", "euro": "eur", "euros": "eur",
    "egp": "egp", "e£": "egp", "le": "egp", "pound": "egp", "pounds": "egp", "egyptian": "", "sar": "sar", "riyal": "sar",
    "riyals": "sar", "aed": "aed", "dirham": "aed", "dirhams": "aed",
    "bn": "billion", "b": "billion", "billion": "billion", "billions": "billion", "mn": "million", "mm": "million",
    "m": "million", "million": "million", "millions": "million", "k": "thousand", "thousand": "thousand",
    "tn": "trillion", "trillion": "trillion", "%": "percent", "percent": "percent",
}
_AMOUNT_TOKEN = re.compile(r"\d[\d.,]*|%|[$€]|[²³]|[^\W\d_]+£?", re.UNICODE)


def amount_key(text: str) -> Optional[str]:
    """A canonical form for a value that is only an amount: "EGP20,635,622,860", "20,635,622,860 EGP" and
    "20,635,622,860 Egyptian pounds" give the same key; "1.2 million" and "1.2 billion" do not. None when
    the value has any other word ("12,700 employees"), so only plain amounts are compared this way."""
    parts = []
    for token in _AMOUNT_TOKEN.findall(text.lower()):
        if token[0].isdigit():
            number = _DIGIT_COMMA.sub("", token).rstrip(".,")
            if not number:
                return None
            parts.append(number)
        elif token in _AMOUNT_WORDS:
            if _AMOUNT_WORDS[token]:
                parts.append(_AMOUNT_WORDS[token])
        else:
            return None
    return " ".join(sorted(parts)) if any(p[0].isdigit() for p in parts) else None


def normalize_value(value: str, value_type: ValueType) -> str:
    """`value` as it is compared for sameness: case, Arabic spelling, punctuation and plurals folded."""
    if value_type == ValueType.LIST:
        try:
            items = [normalize_phrase(str(i)) for i in json.loads(value)]
        except ValueError:
            items = [normalize_phrase(value)]
        return "|".join(sorted(i for i in items if i))
    text = value.translate(_ARABIC_DIGITS)
    if value_type in (ValueType.NUMBER, ValueType.TEXT):
        amount = amount_key(text)
        if amount:
            return amount
    if value_type == ValueType.NUMBER:
        text = _DIGIT_COMMA.sub("", text)
    if value_type == ValueType.URL:
        text = re.sub(r"^https?://(www\.)?", "", text.strip().lower()).rstrip("/")
    return normalize_phrase(text)


def display_quote(quote: str) -> str:
    """A verified quote as it is stored for a person to read: no bold markers from the page's markdown, and
    a table row or a line break in a PDF on one line. Verification ignores both, so the quote is still in its source."""
    text = _TABLE_BAR.sub(" | ", _EMPHASIS.sub("", quote))
    return " ".join(text.split()) or quote.strip()


def snake_key(text: str) -> str:
    """`text` as a key (lowercase snake_case English), or "" when nothing usable is left."""
    key = _KEY_NOISE.sub("_", text.lower()).strip("_")[:_MAX_KEY_CHARS].strip("_")
    return key if _KEY_SHAPE.match(key) else ""


def clean_list(value: str) -> Optional[str]:
    """A list value as a JSON array of strings, or None when it is not a list."""
    try:
        items = json.loads(value)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    texts = [" ".join(str(i).split()) for i in items if isinstance(i, (str, int, float)) and str(i).strip()]
    return json.dumps(texts, ensure_ascii=False) if texts else None


class KeyMapper:
    """Maps the key or label a model gave to a canonical key of the category, using the key table."""

    def __init__(self, keys: Sequence[FactKeySpec]) -> None:
        self._index = build_index(keys)
        self.canonical = {(spec.category, spec.key) for spec in keys}

    def canonical_key(self, category: str, *names: str) -> Optional[str]:
        phrases = self._index.get(category, {})
        for name in names:
            found = phrases.get(normalize_phrase(name)) if name else None
            if found:
                return found
        return None


def to_candidates(
    verified: Sequence[Verified], mapper: KeyMapper, stats: ExtractionStats, start: int = 0
) -> list[Candidate]:
    """Candidates from verified facts. A fact with no usable key or value is dropped and counted."""
    out: list[Candidate] = []
    for position, item in enumerate(verified):
        fact, doc = item.fact, item.doc
        value, value_type = " ".join(fact.value.split()), fact.value_type
        if value_type == ValueType.LIST:
            as_list = clean_list(fact.value)
            value, value_type = (as_list, value_type) if as_list else (value, ValueType.TEXT)
        category = fact.category
        mapped = mapper.canonical_key(category, fact.key, fact.label)
        key = mapped or snake_key(fact.key) or snake_key(fact.label)
        if not value or not key:
            stats.dropped_invalid += 1
            continue
        proposed = None
        if category == OTHER:
            proposed = " ".join((fact.proposed_category or fact.label or key.replace("_", " ")).split())
        out.append(
            Candidate(
                category=category, proposed_category=proposed, key=key, label=fact.label.strip(), value=value,
                value_type=value_type, tier=doc.tier, url=doc.url, pdf_page=doc.pdf_page, quote=display_quote(fact.quote),
                confidence=fact.confidence, exact=item.exact, order=start + position,
                mapped=bool(mapped) and mapped != fact.key.strip(),
            )
        )
    stats.mapped_keys += sum(1 for c in out if c.mapped)
    return out


def unpromoted_keys(candidates: Sequence[Candidate], mapper: KeyMapper) -> list[NewKey]:
    """Keys no canonical key matched, with how many facts use each. They are not added to the key table."""
    found: dict[tuple[str, str], NewKey] = {}
    for c in candidates:
        if (c.category, c.key) in mapper.canonical:
            continue
        entry = found.setdefault((c.category, c.key), NewKey(c.category, c.key, c.label))
        entry.facts += 1
        entry.label = entry.label or c.label
    return list(found.values())


# ------------------------------------------------------------------ merging

def merge_candidates(candidates: Sequence[Candidate], stats: ExtractionStats) -> list[Candidate]:
    """One candidate per category, key and value, from the best source. The others are discarded:
    a stored fact has room for one source only."""
    best: dict[tuple, Candidate] = {}
    for c in candidates:
        slot = (*c.group(), c.identity())
        if slot not in best or c.quality() < best[slot].quality():
            best[slot] = c
    stats.merged_duplicates += len(candidates) - len(best)
    return sorted(best.values(), key=lambda c: c.order)


def _protected(fact: CompanyFact) -> bool:
    return fact.status == FactStatus.APPROVED or fact.edited_by_user


def _stored_group(fact: CompanyFact) -> tuple[str, str, str]:
    other = fact.category == OTHER
    return fact.category, fact.key, normalize_phrase(fact.proposed_category or "") if other else ""


def _stored_quality(fact: CompanyFact) -> tuple[int, int]:
    return TIER_RANK[fact.source_tier], CONFIDENCE_RANK[fact.confidence]


@dataclass
class Plan:
    new: list[tuple[Candidate, Optional[str]]]  # candidate, conflict group
    supersede: list[str]
    conflict_groups: dict[str, str]
    conflicts: int = 0
    already_known: int = 0


def plan_changes(merged: Sequence[Candidate], existing: Sequence[CompanyFact]) -> Plan:
    """Decide what to create, what stored suggestions to replace, and which conflict groups to form."""
    stored: dict[tuple, list[CompanyFact]] = {}
    for fact in existing:
        stored.setdefault(_stored_group(fact), []).append(fact)

    plan = Plan(new=[], supersede=[], conflict_groups={})
    kept: list[Candidate] = []
    replaced: set[str] = set()
    for c in merged:
        same = [f for f in stored.get(c.group(), []) if normalize_value(f.value, f.value_type) == c.identity()]
        if any(_protected(f) or f.status == FactStatus.REJECTED for f in same):
            plan.already_known += 1  # a person decided on this value; it is not suggested again
            continue
        if same:
            if all(c.quality()[:2] < _stored_quality(f) for f in same):
                replaced.update(f.id for f in same)  # a better source for a suggestion nobody has touched
            else:
                plan.already_known += 1
                continue
        kept.append(c)
    plan.supersede = sorted(replaced)

    groups: dict[tuple, list[Candidate]] = {}
    for c in kept:
        groups.setdefault(c.group(), []).append(c)
    assigned: dict[int, Optional[str]] = {id(c): None for c in kept}
    for group, members in groups.items():
        if group[:2] in MULTI_VALUED:
            continue
        live = [f for f in stored.get(group, []) if f.status != FactStatus.REJECTED]
        others = [f for f in live if f.id not in replaced]
        values = {c.identity() for c in members} | {normalize_value(f.value, f.value_type) for f in others}
        if len(values) < 2:
            continue
        name = next((f.conflict_group for f in live if f.conflict_group), None) or new_id()
        plan.conflicts += 1
        for c in members:
            assigned[id(c)] = name
        for f in others:
            if f.conflict_group != name:
                plan.conflict_groups[f.id] = name
    plan.new = [(c, assigned[id(c)]) for c in kept]
    return plan


def build_fact(
    c: Candidate, group: Optional[str], workspace_id: str, run_id: Optional[str], observed_at: str
) -> Optional[NewFact]:
    """The fact to store for `c`, or None when it breaks a rule of the stored shape."""
    try:
        return NewFact(
            workspace_id=workspace_id, category=c.category, proposed_category=c.proposed_category, key=c.key,
            value=c.value, value_type=c.value_type, source_tier=c.tier, source_url=c.url, pdf_page=c.pdf_page,
            quote=c.quote, observed_at=observed_at, confidence=c.confidence, status=FactStatus.SUGGESTED,
            conflict_group=group, run_id=run_id,
        )
    except ValidationError:
        return None
