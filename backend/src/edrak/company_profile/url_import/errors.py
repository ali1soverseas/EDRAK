"""Failures of the website import. Each carries a message that is safe to show a person."""

from __future__ import annotations

from edrak.company_profile.extractor import ProfileImportError


class UrlBlocked(ProfileImportError):
    """The address is not a public http(s) website address, or points somewhere that must not be fetched."""


class SiteUnreachable(ProfileImportError):
    """The website could not be read: not found, refused, timed out, or not a web page."""
