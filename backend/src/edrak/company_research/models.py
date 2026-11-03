"""Types for researched company facts, their canonical keys, and the runs that find them.

A fact is a categorized claim about the company with the exact text that supports it. It starts as
`suggested`; a person approves or rejects it before anything uses it.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from edrak.company_research.categories import OTHER, is_category

KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_PROPOSED_CATEGORY_CHARS = 80


class ValueType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    URL = "url"
    LIST = "list"


class SourceTier(str, Enum):
    """Where a fact was read, best first. Third party is off by default and never overrides the others."""

    OWN_SITE = "own_site"
    OWN_PDF = "own_pdf"
    OWN_SOCIAL = "own_social"
    THIRD_PARTY = "third_party"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FactStatus(str, Enum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"


class KeyOrigin(str, Enum):
    SEED = "seed"
    PROMOTED = "promoted"


class ResearchRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED_STATUSES = (ResearchRunStatus.DONE, ResearchRunStatus.FAILED, ResearchRunStatus.CANCELLED)


def check_category(value: str) -> str:
    if not is_category(value):
        raise ValueError(f"unknown category: {value!r}")
    return value


def check_key(value: str) -> str:
    if not KEY_SHAPE.match(value):
        raise ValueError(f"a key is lowercase snake_case English, got {value!r}")
    return value


def check_value(value: str, value_type: ValueType) -> str:
    """`value` as it is stored: not blank, and for a list a JSON array of strings."""
    if not value.strip():
        raise ValueError("a fact needs a value")
    if value_type == ValueType.LIST:
        try:
            items: Any = json.loads(value)
        except ValueError:
            items = None
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ValueError("a list value is a JSON array of strings")
    return value


def check_url(value: str) -> str:
    if not value.startswith(("http://", "https://")) or " " in value:
        raise ValueError(f"not an http(s) address: {value!r}")
    return value


class Synonyms(BaseModel):
    en: list[str] = Field(default_factory=list)
    ar: list[str] = Field(default_factory=list)


class FactKeySpec(BaseModel):
    """A canonical key as seeded or promoted: what normalization matches against."""

    category: str
    key: str
    label_en: str
    label_ar: str
    synonyms: Synonyms = Field(default_factory=Synonyms)
    value_type: ValueType = ValueType.TEXT

    _category = field_validator("category")(check_category)
    _key = field_validator("key")(check_key)


class FactKey(FactKeySpec):
    id: str
    created_at: str
    origin: KeyOrigin


class NewFact(BaseModel):
    """A fact about to be stored. `proposed_category` is required for `other` and refused for the rest."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    category: str
    proposed_category: Optional[str] = None
    key: str
    value: str
    value_type: ValueType = ValueType.TEXT
    source_tier: SourceTier
    source_url: str
    pdf_page: Optional[int] = Field(default=None, ge=1)
    quote: str
    observed_at: Optional[str] = Field(default=None, description="When the source was read. Now, when not given.")
    confidence: Confidence
    status: FactStatus = FactStatus.SUGGESTED
    conflict_group: Optional[str] = None
    run_id: Optional[str] = None
    edited_by_user: bool = False

    _category = field_validator("category")(check_category)
    _key = field_validator("key")(check_key)
    _source_url = field_validator("source_url")(check_url)

    @field_validator("quote")
    @classmethod
    def _quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a fact needs the text that supports it")
        return value

    @field_validator("proposed_category")
    @classmethod
    def _proposed(cls, value: Optional[str]) -> Optional[str]:
        text = " ".join((value or "").split())
        return text[:_MAX_PROPOSED_CATEGORY_CHARS] or None

    @model_validator(mode="after")
    def _consistent(self) -> NewFact:
        check_value(self.value, self.value_type)
        if self.category == OTHER and not self.proposed_category:
            raise ValueError("a fact in the other category carries the category the model proposed")
        if self.category != OTHER and self.proposed_category:
            raise ValueError("only a fact in the other category carries a proposed category")
        return self


class CompanyFact(BaseModel):
    """A stored fact. Timestamps are the schema's UTC text."""

    id: str
    workspace_id: str
    category: str
    proposed_category: Optional[str] = None
    key: str
    value: str
    value_type: ValueType
    source_tier: SourceTier
    source_url: str
    pdf_page: Optional[int] = None
    quote: str
    observed_at: str
    confidence: Confidence
    status: FactStatus
    conflict_group: Optional[str] = None
    run_id: Optional[str] = None
    created_at: str
    updated_at: str
    edited_by_user: bool = False


class ResearchRun(BaseModel):
    id: str
    workspace_id: str
    status: ResearchRunStatus
    input: dict[str, Any] = Field(default_factory=dict, description="The seed URL and any options.")
    pages_crawled: int = 0
    pdfs_read: int = 0
    facts_found: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    stage: Optional[str] = Field(default=None, description="The step the run is in, while it runs.")
    stats: dict[str, Any] = Field(default_factory=dict, description="Counts from each step, filled as the run goes.")
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
