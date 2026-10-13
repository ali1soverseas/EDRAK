"""Which metric set fits the company: software sold by subscription, or hardware and infrastructure.

Deterministic, from words the company and the analysis already use, so the planner, the metrics agent and
the brief all pick the same set. A price per seat per month means nothing for a chip maker, and a price per
GPU means nothing for a SaaS company.
"""

from __future__ import annotations

import re
from typing import Any, Optional

HARDWARE = "hardware_infrastructure"

_HARDWARE_WORDS = re.compile(
    r"\b(?:gpus?|chips?|semiconductors?|accelerators?|silicon|hardware|servers?|data ?cent(?:er|re)s?|supercomput\w*|"
    r"wafers?|fabs?|foundr(?:y|ies)|devices?|networking|switches|storage arrays?|ai infrastructure|compute infrastructure|"
    r"sovereign ai|systems? integrat\w*|manufactur\w*|equipment|telecom(?:munications)? infrastructure)\b",
    re.I,
)
_SOFTWARE_WORDS = re.compile(r"\b(?:saas|subscriptions?|per seat|per user|software as a service|web app|mobile app|platform plan)\b", re.I)


def business_model(profile: Any = None, *, offering: Optional[str] = "", goal: Optional[str] = "", industries: Optional[list[str]] = None) -> Optional[str]:
    """`hardware_infrastructure` when the company or the offering is clearly hardware or infrastructure, else None."""
    parts = [offering or "", goal or "", " ".join(industries or [])]
    if profile is not None:
        get = profile.get if isinstance(profile, dict) else lambda k, d=None: getattr(profile, k, d)
        parts += [get("notes") or get("description") or "", " ".join(get("products") or get("offerings") or [])]
    text = " ".join(str(p) for p in parts)
    hard = len(_HARDWARE_WORDS.findall(text))
    soft = len(_SOFTWARE_WORDS.findall(text))
    return HARDWARE if hard >= 2 and hard > soft else None
