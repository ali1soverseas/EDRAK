"""Build a company profile draft from a company's website.

import_profile_from_url reads a few high-value pages of the site and hands their text to the
extraction core. It does not save anything.
"""

from edrak.company_profile.url_import.errors import SiteUnreachable, UrlBlocked
from edrak.company_profile.url_import.importer import import_profile_from_url

__all__ = ["SiteUnreachable", "UrlBlocked", "import_profile_from_url"]
