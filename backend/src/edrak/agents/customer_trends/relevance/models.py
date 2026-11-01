"""Plain data of the relevance filter: a comment as the filter sees it and a verdict on it."""

from dataclasses import dataclass, field
from typing import Any, Literal

Relevance = Literal["relevant", "irrelevant"]
Stage = Literal["a", "b", "c"]

# The reasons a verdict carries, by stage.
DUPLICATE = "duplicate"
EMPTY = "empty"
LINK_ONLY = "link_only"
MENTIONS_ONLY = "mentions_only"
SPAM = "spam"
TOO_SHORT = "too_short"
TERM_MATCH = "term_match"
LLM_RELEVANT = "llm_relevant"
LLM_IRRELEVANT = "llm_irrelevant"
KEPT_ON_ERROR = "kept_on_error"

UNKNOWN_PLATFORM = "unknown"


@dataclass(frozen=True)
class Comment:
    """One comment to judge. `already` marks one a previous pass judged (a replan collects more):
    it only takes part in duplicate detection."""

    id: str
    text: str
    platform: str = UNKNOWN_PLATFORM
    parent_key: str = ""
    parent_text: str = ""
    already: bool = False


@dataclass(frozen=True)
class Verdict:
    relevance: Relevance
    reason: str
    stage: Stage
    extra: dict[str, Any] = field(default_factory=dict)

    def mark(self) -> dict[str, Any]:
        """The metadata merged into the stored item."""
        return {
            "relevance": self.relevance,
            "relevance_reason": self.reason,
            "filter_stage": self.stage,
            **self.extra,
        }
