"""EDRAK core module."""

from edrak.core.config import settings
from edrak.core.profiles import (
    COMPANY_PROFILES,
    GITLAB_COMPANY_PROFILE,
    get_company_profile,
    get_detailed_gitlab_profile,
    get_gitlab_profile,
)

__all__ = [
    "COMPANY_PROFILES",
    "GITLAB_COMPANY_PROFILE",
    "get_company_profile",
    "get_detailed_gitlab_profile",
    "get_gitlab_profile",
    "settings",
]
