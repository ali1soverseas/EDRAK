"""Reasons a PDF is turned away. Each carries a code and a message that is safe to show a person."""

from __future__ import annotations

from edrak.company_profile.extractor import ProfileImportError


class PdfRejected(ProfileImportError):
    """The upload cannot be imported: not a PDF, too big, encrypted, corrupt or empty."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
