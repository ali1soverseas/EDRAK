"""Which metric set fits the company: the kind of business decides which figures help a decision.

A price per seat per month means nothing for a chip maker, and a price per GPU means nothing for a SaaS
company. Deterministic, so the planner, the metrics agent and the brief all pick the same set:

1. Clear hardware or infrastructure wording wins (a "Technology" company that makes GPUs sells hardware).
2. Otherwise the industries chosen at onboarding, in the order chosen.
3. A profile without industries falls back to words in its description and the question.
4. A company that only chose Other gets the `dynamic` set: a small shared core plus the figures the
   planner proposes for its field (see metrics.custom).

None means the default set, written for software sold by subscription.
"""

from __future__ import annotations

import re
from typing import Any, Optional

HARDWARE = "hardware_infrastructure"
FINANCE = "financial_services"
RETAIL = "retail_consumer"
LOGISTICS = "logistics"
HEALTH = "healthcare"
EDUCATION = "education"
DYNAMIC = "dynamic"

# Onboarding industry key -> metric set. SaaS and Technology use the default (None).
INDUSTRY_SET: dict[str, Optional[str]] = {
    "fintech": FINANCE, "retail": RETAIL, "logistics": LOGISTICS, "healthcare": HEALTH, "health": HEALTH,
    "education": EDUCATION, "manufacturing": HARDWARE, "saas": None, "technology": None,
}

_HARDWARE_WORDS = re.compile(
    r"\b(?:gpus?|chips?|semiconductors?|accelerators?|silicon|hardware|servers?|data ?cent(?:er|re)s?|supercomput\w*|"
    r"wafers?|fabs?|foundr(?:y|ies)|devices?|networking|switches|storage arrays?|ai infrastructure|compute infrastructure|"
    r"sovereign ai|systems? integrat\w*|manufactur\w*|equipment|telecom(?:munications)? infrastructure)\b",
    re.I,
)
_SOFTWARE_WORDS = re.compile(r"\b(?:saas|subscriptions?|per seat|per user|software as a service|web app|mobile app|platform plan)\b", re.I)
# For profiles saved before industries existed: words that name each field.
_FIELD_WORDS: dict[str, re.Pattern] = {
    FINANCE: re.compile(r"\b(?:payments?|fintech|bank\w*|lending|wallets?|remittances?|insur\w*|merchants? acquiring|credit)\b", re.I),
    RETAIL: re.compile(r"\b(?:retail\w*|stores?|shoppers?|e-?commerce|grocer\w*|fashion|apparel|consumer goods|fmcg)\b", re.I),
    LOGISTICS: re.compile(r"\b(?:logistics|shipping|deliver(?:y|ies)|couriers?|freight|parcels?|warehous\w*|last[- ]mile)\b", re.I),
    HEALTH: re.compile(r"\b(?:health\w*|clinics?|hospitals?|patients?|medical|pharma\w*|diagnostic\w*)\b", re.I),
    EDUCATION: re.compile(r"\b(?:education|edtech|schools?|universit(?:y|ies)|students?|learners?|courses?|tutor\w*)\b", re.I),
}


def _get(profile: Any, key: str) -> Any:
    if profile is None:
        return None
    return profile.get(key) if isinstance(profile, dict) else getattr(profile, key, None)


def business_model(profile: Any = None, *, offering: Optional[str] = "", goal: Optional[str] = "",
                   industries: Optional[list[str]] = None) -> Optional[str]:
    """The metric set for this company and question, or None for the default (software) set."""
    chosen = [str(i).lower() for i in (industries if industries is not None else (_get(profile, "industries") or []))]
    described = " ".join([_get(profile, "notes") or _get(profile, "description") or "",
                          " ".join(_get(profile, "products") or _get(profile, "offerings") or []),
                          _get(profile, "industry_other") or ""])
    text = f"{offering or ''} {goal or ''} {described}"

    hard = len(_HARDWARE_WORDS.findall(text))
    if hard >= 2 and hard > len(_SOFTWARE_WORDS.findall(text)):
        return HARDWARE
    for key in chosen:
        if INDUSTRY_SET.get(key):
            return INDUSTRY_SET[key]
    if any(k in INDUSTRY_SET for k in chosen):  # SaaS or Technology only
        return None
    # Other, or no industry saved: the words in the profile and the question, then the planner's own figures.
    scores = {field: len(rx.findall(text)) for field, rx in _FIELD_WORDS.items()}
    best = max(scores, key=lambda f: scores[f])
    if scores[best] >= 2:
        return best
    if len(_SOFTWARE_WORDS.findall(text)) >= 1:
        return None
    return DYNAMIC if "other" in chosen else None
