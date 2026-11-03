"""Company research: facts about a company found beyond the fixed profile fields.

The data layer lives here and in `edrak.db.repositories` (company_facts, fact_keys, research_runs).
Facts are stored as suggestions; a person approves or rejects them before anything uses them.
"""

from edrak.company_research.categories import CATEGORIES, CATEGORY_KEYS, OTHER, Category, get_category, is_category
from edrak.company_research.keys import normalize_key
from edrak.company_research.models import (
    CompanyFact,
    Confidence,
    FactKey,
    FactKeySpec,
    FactStatus,
    KeyOrigin,
    NewFact,
    ResearchRun,
    ResearchRunStatus,
    SourceTier,
    Synonyms,
    ValueType,
)

__all__ = [
    "CATEGORIES",
    "CATEGORY_KEYS",
    "OTHER",
    "Category",
    "CompanyFact",
    "Confidence",
    "FactKey",
    "FactKeySpec",
    "FactStatus",
    "KeyOrigin",
    "NewFact",
    "ResearchRun",
    "ResearchRunStatus",
    "SourceTier",
    "Synonyms",
    "ValueType",
    "get_category",
    "is_category",
    "normalize_key",
]
