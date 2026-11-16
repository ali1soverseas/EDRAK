"""The public entry point: source documents in, a profile draft out. Nothing is saved."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from pydantic import ValidationError

from edrak.company_profile.context import pack_context
from edrak.company_profile.extractor import LLMCall, extract_profile
from edrak.company_profile.models import FIELD_NAMES, ProfileDraft, ProfileFields, SourceDocument
from edrak.company_profile.validate import validate_extraction
from edrak.contracts.request import SocialAccount, SocialPlatform
from edrak.core.config import settings
from edrak.db.repositories.company import normalise_url


def _clean_links(links: Optional[Iterable[Any]]) -> tuple[list[SocialAccount], list[str]]:
    """The links as the company endpoints take them: one website at most, no repeated URL."""
    kept: list[SocialAccount] = []
    seen: set[str] = set()
    ignored = 0
    for item in links or []:
        try:
            link = item if isinstance(item, SocialAccount) else SocialAccount.model_validate(item)
        except ValidationError:
            ignored += 1
            continue
        url = normalise_url(link.url)
        if not url:
            ignored += 1
            continue
        has_website = any(k.platform == SocialPlatform.WEBSITE for k in kept)
        if url.lower() in seen or (link.platform == SocialPlatform.WEBSITE and has_website):
            continue
        seen.add(url.lower())
        kept.append(SocialAccount(platform=link.platform, url=url))
    warnings = [f"{ignored} link(s) were ignored because they were not valid."] if ignored else []
    return kept, warnings


def build_profile_draft(
    documents: Sequence[SourceDocument],
    extra_links: Optional[Iterable[Any]] = None,
    *,
    llm: Optional[LLMCall] = None,
) -> ProfileDraft:
    """A draft company profile from `documents`, for a person to review and save.

    `extra_links` are website and social links the importer found itself (dicts with `platform`
    and `url`, or SocialAccount). `llm` replaces the model call, for tests.

    Raises ProfileLLMError when the model cannot be reached and ProfileExtractionError when its
    reply does not validate after one repair attempt.
    """
    packed = pack_context(documents)
    links, link_warnings = _clean_links(extra_links)
    warnings = packed.warnings + link_warnings

    if not packed.sources:
        warnings.append("There was no readable text to build a profile from.")
        return ProfileDraft(
            profile=ProfileFields(), links=links, gaps=list(FIELD_NAMES), warnings=warnings, sources=[]
        )

    if packed.char_count < settings.PROFILE_IMPORT_MIN_CHARS:
        warnings.append(
            f"Only {packed.char_count} characters of readable text were found, so the draft is likely incomplete."
        )

    extraction = extract_profile(packed.text, llm)
    checked = validate_extraction(extraction, packed.sources)
    return ProfileDraft(
        profile=checked.profile,
        links=links,
        field_sources=checked.field_sources,
        gaps=checked.gaps,
        warnings=warnings + checked.warnings,
        sources=packed.sources,
    )
