"""The shape of the model's reply when it extracts facts."""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from edrak.company_research.categories import CATEGORY_KEYS, OTHER, is_category
from edrak.company_research.models import Confidence, ValueType


def _serialize_list_value(value: Any) -> Any:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return value


FactValue = Annotated[
    str,
    BeforeValidator(_serialize_list_value, json_schema_input_type=str | list[str]),
]


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = Field(description=f"One of: {', '.join(CATEGORY_KEYS)}.")
    proposed_category: str | None = Field(
        default=None, description='Only when category is "other": a short English name for the category the fact belongs in.'
    )
    key: str = Field(description="A short lowercase snake_case English name for what the fact is, such as ceo or headquarters.")
    label: str = Field(default="", description="The name for this fact as the source writes it, in the source's language.")
    value: FactValue = Field(
        description="The fact's value. English, except names. Use a string or an array of strings for a list."
    )
    value_type: ValueType = ValueType.TEXT
    source: str = Field(description="The id of the source that states the fact, such as S3.")
    quote: str = Field(description="An exact quote copied from that source, in its language, that states the fact.")
    confidence: Confidence = Confidence.MEDIUM

    @model_validator(mode="before")
    @classmethod
    def _unknown_category(cls, data: Any) -> Any:
        """A category that is not on the list is filed under "other", keeping what the model called it."""
        if isinstance(data, dict):
            if isinstance(data.get("value"), list):
                data = {**data, "value_type": ValueType.LIST}
            category = str(data.get("category") or "").strip().lower()
            if is_category(category):
                data = {**data, "category": category}
            else:
                data = {**data, "category": OTHER, "proposed_category": data.get("proposed_category") or data.get("category")}
        return data


class FactExtraction(BaseModel):
    """The model's reply: the facts the sources state, and nothing else."""

    model_config = ConfigDict(extra="ignore")

    facts: list[ExtractedFact] = Field(default_factory=list)
