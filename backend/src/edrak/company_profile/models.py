"""Types for turning source text into a company profile draft.

A draft is what an importer returns to the company setup screen. It is never saved here: the person
reviews it and saves it through the existing company endpoints.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from edrak.contracts.request import SocialAccount

# Letters in the Arabic, Arabic Supplement, Extended-A and presentation-form blocks.
_ARABIC_LETTER = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

# Share of letters that are Arabic. At or above ARABIC_MIN the text is "ar", below LATIN_MAX it is "en".
ARABIC_MIN = 0.7
LATIN_MAX = 0.1


class SourceType(str, Enum):
    WEB_PAGE = "web_page"
    PDF_PAGE = "pdf_page"
    PAGE_METADATA = "page_metadata"


class Language(str, Enum):
    AR = "ar"
    EN = "en"
    MIXED = "mixed"


def detect_language(text: str) -> Language:
    """`ar`, `en` or `mixed` from the share of Arabic letters. Text with no letters counts as `en`."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return Language.EN
    ratio = len(_ARABIC_LETTER.findall("".join(letters))) / len(letters)
    if ratio >= ARABIC_MIN:
        return Language.AR
    if ratio < LATIN_MAX:
        return Language.EN
    return Language.MIXED


class SourceDocument(BaseModel):
    """One piece of source text: a website page, a PDF page, or a page's metadata.

    `priority` orders the context: the lowest number is packed first, so it is the last to be cut
    when the budget runs out. Equal priorities keep the order they were given in.
    """

    source_type: SourceType
    ref: str = Field(description='The URL, or "p.<n>" for a PDF page.')
    title: str = ""
    text: str
    language: Optional[Language] = Field(default=None, description="Detected from the text when not given.")
    priority: int = 0

    @model_validator(mode="after")
    def _detect_language(self) -> "SourceDocument":
        if self.language is None:
            self.language = detect_language(self.text)
        return self


class SourceRef(BaseModel):
    """A source that was sent to the model, under the id the model cites it by."""

    id: str
    type: SourceType
    ref: str
    title: str = ""


class ProfileFields(BaseModel):
    """What the model fills in. Empty means the sources do not say.

    `name`, `aliases`, `products` and `notes` are the same fields as the contract's CompanyProfile.
    The contract has no industry, markets or goals, so those three are named here as the company
    setup screen stores them.
    """

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, description="The company's name as it writes it.")
    aliases: list[str] = Field(
        default_factory=list,
        description="Other names for the company, including its name in the other language.",
    )
    notes: Optional[str] = Field(
        default=None, description="One to three plain English sentences on what the company does."
    )
    products: list[str] = Field(default_factory=list, description="Short names of products or services.")
    industry: Optional[str] = Field(default=None, description="One of the allowed industry values.")
    markets: list[str] = Field(default_factory=list, description="Countries or regions the company serves.")
    goals: list[str] = Field(default_factory=list, description="Goals the sources state explicitly.")


FIELD_NAMES: tuple[str, ...] = tuple(ProfileFields.model_fields)


class ProfileExtraction(BaseModel):
    """The model's reply: the fields and, for each, the source ids that support it."""

    model_config = ConfigDict(extra="ignore")

    profile: ProfileFields
    field_sources: dict[str, list[str]] = Field(
        default_factory=dict,
        description="For each filled field, the ids (S1, S2, ...) of the sources that state it.",
    )


class ProfileDraft(BaseModel):
    """The importer's response. The person reviews it; nothing here is saved."""

    profile: ProfileFields
    links: list[SocialAccount] = Field(default_factory=list)
    field_sources: dict[str, list[str]] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list, description="Fields the sources do not cover.")
    warnings: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
