"""Text for matching. The stored text is never changed; only this copy is."""

import re

from edrak.agents.customer_trends.utils.text import search_key

# A letter written three or more times in a row ("جداااا", "sooooo"). Digits and punctuation are
# left alone, so 1000 stays 1000.
_ELONGATION = re.compile(r"([^\W\d_])\1{2,}")


def normalize(text: str) -> str:
    """Lowercase, one alef, taa marbuta as haa, alef maqsura as yaa, no diacritics or tatweel,
    letters repeated three or more times written once, whitespace collapsed.

    The first five come from `search_key`. So "الشركة", "الشركه" and "الشّركةُ" are one word, and so
    are "جداااااا" and "جدا".
    """
    return _ELONGATION.sub(r"\1", search_key(text))
