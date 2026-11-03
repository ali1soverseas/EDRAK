"""The prompt text for fact extraction. All of it lives here."""

from __future__ import annotations

from typing import Optional, Sequence

from edrak.company_profile.models import ProfileFields
from edrak.company_research.categories import CATEGORIES, OTHER
from edrak.company_research.models import FactKeySpec
from edrak.guardrails.content import UNTRUSTED_RULE

SYSTEM_PROMPT = f"""You extract facts about one company from text taken from the company's own website and documents.

The sources are labeled blocks: [S1], [S2], and so on, each with its tier, url, and PDF page when it has one, then its text.
{UNTRUSTED_RULE}

Rules:
- Extract only facts the sources state about this company. Never use general knowledge and never infer a fact the text does not say.
- Return a list of facts. Each fact has:
  - category: one of the categories below.
  - key: a short lowercase snake_case English name for what the fact is. Prefer a key listed for its category; invent a new one only when none fits.
  - label: the name of the fact as the source writes it, in the source's language.
  - value: the fact itself, written in English. For a list, a JSON array of strings. See "Values" below.
  - value_type: text, number, date, url or list.
  - source: the id of the source that states it, such as S3. One source per fact.
  - quote: an exact quote copied from that source, character for character, in the source's language, that states the fact. Do not translate, shorten with ellipses, fix spelling or join separate passages.
  - confidence: high when the source states it plainly, medium when it is clear but indirect, low when it is unclear.
- A key describes the company as a whole. When a source gives a value for a division, subsidiary, product line, region, a named person's team, another organization (a grant, a customer, a partner) or a person other than the company's own leaders, use a more specific key that names that scope, such as "digital_industries_revenue", or leave the value out. Never put it under the company-wide key.
- When a source gives the same measure for several years, return the latest year only and put the year in the label. Return an older year only when it is the only one.
- Return facts that describe the company as it is, plus its main milestones (founding, name changes, mergers, acquisitions). Leave out anecdotes, one-off events and detail that does not help understand the company.
- Do not return facts that only repeat the company's profile fields: its name, other names, description, products, industry, markets and goals.
- If no category fits, use "{OTHER}" and give proposed_category, a short English name for the category the fact belongs in.
- If a source states nothing worth returning, return no facts for it. An empty list is a correct answer.
- Return each fact once. If the same fact appears in several sources, cite the first.

Values:
- A number is written with its unit and currency, such as "EGP 28.4 billion", "12,700 employees" or "19.6%". Never a bare number.
- A date is written as YYYY-MM-DD, or YYYY-MM or YYYY when less is known.
- Person names, places and addresses are written in Latin script, the way the company writes them in English; transliterate Arabic ones. Everything else is written in English.
"""

CONTEXT_INTRO = "Sources:\n\n"


def categories_block(keys: Sequence[FactKeySpec]) -> str:
    """The categories, each with the keys already in use for it, as a list for the prompt."""
    by_category: dict[str, list[str]] = {}
    for spec in keys:
        by_category.setdefault(spec.category, []).append(spec.key)
    lines = []
    for category in CATEGORIES:
        names = by_category.get(category.key, [])
        suffix = f": {', '.join(names)}" if names else ""
        lines.append(f"- {category.key} ({category.label_en}){suffix}")
    return "Categories, with the keys in use for each:\n" + "\n".join(lines)


def profile_block(profile: Optional[ProfileFields]) -> str:
    """What the company's profile already says, so the model leaves it out. Empty when there is none."""
    if profile is None:
        return ""
    parts = []
    for label, value in (
        ("name", profile.name),
        ("other names", ", ".join(profile.aliases)),
        ("description", profile.notes),
        ("products", ", ".join(profile.products)),
        ("industry", profile.industry),
        ("markets", ", ".join(profile.markets)),
        ("goals", "; ".join(profile.goals)),
    ):
        if value:
            parts.append(f"- {label}: {value}")
    return "The company's profile already says (do not return these as facts):\n" + "\n".join(parts) if parts else ""
