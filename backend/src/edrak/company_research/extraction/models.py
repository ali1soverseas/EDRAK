"""What fact extraction returns: facts ready to store, how they relate to stored ones, and counts."""

from __future__ import annotations

from dataclasses import dataclass, field

from edrak.company_research.models import NewFact


@dataclass
class NewKey:
    """A key the model proposed that no canonical key matches. It is stored on the facts as it is;
    it is not added to the key table, so a person can promote it later."""

    category: str
    key: str
    label: str = ""
    facts: int = 0


@dataclass
class ExtractionStats:
    documents: int = 0
    skipped_empty: int = 0
    skipped_risky: int = 0
    skipped_profile: int = 0  # pages that only restate the profile
    skipped_over_limit: int = 0  # pages left out when the call limit was reached
    cut_documents: int = 0
    batches: int = 0
    failed_batches: int = 0
    facts_returned: int = 0
    dropped_unknown_source: int = 0
    dropped_no_quote: int = 0
    dropped_invalid: int = 0
    mapped_keys: int = 0  # keys renamed to a canonical key
    new_keys: int = 0
    merged_duplicates: int = 0  # same fact from another source, discarded
    already_known: int = 0  # same value as a fact already stored
    conflicts: int = 0  # groups of facts with the same key and different values
    created: int = 0


@dataclass
class ExtractionResult:
    """The outcome of a run, as changes to apply to the stored facts. Nothing here has been saved."""

    facts: list[NewFact] = field(default_factory=list)  # to create, as suggested facts
    supersede: list[str] = field(default_factory=list)  # ids of suggested, unedited facts a better source replaces
    conflict_groups: dict[str, str] = field(default_factory=dict)  # stored fact id -> group it joins; no other field changes
    new_keys: list[NewKey] = field(default_factory=list)
    stats: ExtractionStats = field(default_factory=ExtractionStats)
    warnings: list[str] = field(default_factory=list)
