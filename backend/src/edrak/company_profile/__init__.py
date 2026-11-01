"""Turn source text (website pages, PDF pages) into a company profile draft.

The importers collect SourceDocuments and call build_profile_draft. The draft goes back to the
company setup screen for review; saving is done there, through the existing company endpoints.
"""

from edrak.company_profile.draft import build_profile_draft
from edrak.company_profile.extractor import (
    ProfileExtractionError,
    ProfileImportError,
    ProfileLLMError,
)
from edrak.company_profile.models import (
    Language,
    ProfileDraft,
    ProfileFields,
    SourceDocument,
    SourceRef,
    SourceType,
)

__all__ = [
    "Language",
    "ProfileDraft",
    "ProfileExtractionError",
    "ProfileFields",
    "ProfileImportError",
    "ProfileLLMError",
    "SourceDocument",
    "SourceRef",
    "SourceType",
    "build_profile_draft",
]
