"""Facts out of crawled pages and PDF pages: model calls in batches, then plain-code checks.

The model reads batches of labeled sources and returns facts with a quote each. Everything after
that is code: the quote must be in the cited source, keys are mapped to the canonical ones, repeats
are merged, and differing values are put in conflict groups. A batch the model cannot answer is
recorded as a warning and the run goes on with the others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from edrak.company_profile.extractor import LLMCall, ProfileImportError, ProfileLLMError, ask_structured, schema_caller
from edrak.company_profile.models import ProfileFields
from edrak.company_research.extraction.merge import (
    KeyMapper,
    build_fact,
    merge_candidates,
    plan_changes,
    to_candidates,
    unpromoted_keys,
)
from edrak.company_research.extraction.models import ExtractionResult
from edrak.company_research.extraction.prompts import CONTEXT_INTRO, SYSTEM_PROMPT, categories_block, profile_block
from edrak.company_research.extraction.schema import FactExtraction
from edrak.company_research.extraction.sources import Batch, SourceDoc, make_batches
from edrak.company_research.extraction.verify import verify_facts
from edrak.company_research.models import CompanyFact, FactKeySpec
from edrak.company_research.seed_keys import SEED_KEYS
from edrak.core.config import settings
from edrak.core.llm import schema_instruction
from edrak.db.util import now_iso

logger = logging.getLogger(__name__)


@dataclass
class ExtractionLimits:
    batch_chars: int = 24000
    max_batches: int = 30
    ocr_tolerance: float = 0.9

    @classmethod
    def from_settings(cls) -> "ExtractionLimits":
        return cls(
            batch_chars=max(1000, settings.COMPANY_RESEARCH_EXTRACT_BATCH_CHARS),
            max_batches=max(1, settings.COMPANY_RESEARCH_EXTRACT_MAX_BATCHES),
            ocr_tolerance=min(1.0, max(0.0, settings.COMPANY_RESEARCH_EXTRACT_OCR_TOLERANCE)),
        )


def _span(batch: Batch) -> str:
    ids = [d.id for d in batch.documents]
    return ids[0] if len(ids) == 1 else f"{ids[0]} to {ids[-1]}"


async def extract_company_facts(
    documents: Sequence[SourceDoc],
    *,
    workspace_id: str,
    run_id: Optional[str] = None,
    profile: Optional[ProfileFields] = None,
    existing: Sequence[CompanyFact] = (),
    keys: Optional[Sequence[FactKeySpec]] = None,
    llm: Optional[LLMCall] = None,
    limits: Optional[ExtractionLimits] = None,
) -> ExtractionResult:
    """Verified, deduplicated facts from `documents`, as changes to the facts already stored.

    `documents` come from `documents_from_crawl` and `documents_from_pdfs`. `profile` is what the
    company's profile already says: the model is told to leave it out, and a page that only restates
    it is not sent. `existing` are the workspace's stored facts (any status); anything approved or
    edited is never replaced. `keys` are the canonical keys (the seeds and any promoted ones; the
    seeds when not given). `llm` replaces the model call, for tests.

    The result's `facts` are new suggestions; `supersede` lists stored suggestions a better source
    replaces and `conflict_groups` the stored facts that join a group (only that field changes).
    A batch the model fails on is recorded in `warnings`; the run does not raise for it.
    """
    limits = limits or ExtractionLimits.from_settings()
    specs = list(keys) if keys is not None else list(SEED_KEYS)
    call = llm or schema_caller(FactExtraction)
    result = ExtractionResult()
    stats, warnings = result.stats, result.warnings
    stats.documents = len(documents)

    batched = make_batches(documents, limits.batch_chars, limits.max_batches, profile)
    stats.skipped_empty, stats.skipped_risky = batched.skipped_empty, batched.skipped_risky
    stats.skipped_profile, stats.skipped_over_limit = batched.skipped_profile, batched.skipped_over_limit
    stats.cut_documents, stats.batches = batched.cut, len(batched.batches)

    system = "\n\n".join(p for p in (SYSTEM_PROMPT, categories_block(specs), profile_block(profile)) if p)
    mapper = KeyMapper(specs)
    candidates = []
    for batch in batched.batches:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": CONTEXT_INTRO + batch.text},
            schema_instruction(FactExtraction),
        ]
        try:
            reply = await asyncio.to_thread(ask_structured, FactExtraction, messages, call, what="fact")
        except ProfileImportError as exc:
            stats.failed_batches += 1
            reason = "the model could not be reached" if isinstance(exc, ProfileLLMError) else "its reply was not valid after one retry"
            warnings.append(f"Facts could not be read from sources {_span(batch)}: {reason}.")
            logger.warning("Fact extraction failed for sources %s: %s", _span(batch), exc)
            continue
        stats.facts_returned += len(reply.facts)
        checked = verify_facts(reply.facts, {d.id: d for d in batch.documents}, limits.ocr_tolerance)
        stats.dropped_unknown_source += checked.unknown_source
        stats.dropped_no_quote += checked.no_quote
        candidates += to_candidates(checked.kept, mapper, stats, start=len(candidates))

    merged = merge_candidates(candidates, stats)
    result.new_keys = unpromoted_keys(merged, mapper)
    stats.new_keys = len(result.new_keys)
    plan = plan_changes(merged, existing)
    stats.already_known, stats.conflicts = plan.already_known, plan.conflicts
    observed_at = now_iso()
    for candidate, group in plan.new:
        fact = build_fact(candidate, group, workspace_id, run_id, observed_at)
        if fact is None:
            stats.dropped_invalid += 1
        else:
            result.facts.append(fact)
    result.supersede, result.conflict_groups = plan.supersede, plan.conflict_groups
    stats.created = len(result.facts)
    warnings.extend(_summary(result))
    return result


def _summary(result: ExtractionResult) -> list[str]:
    s, notes = result.stats, []
    if s.skipped_over_limit:
        notes.append(f"{s.skipped_over_limit} document(s) were not read: the run reached its limit of model calls.")
    if s.cut_documents:
        notes.append(f"{s.cut_documents} document(s) were longer than one model call and were cut.")
    if s.skipped_profile:
        notes.append(f"{s.skipped_profile} page(s) only restated the company profile and were not read.")
    if s.dropped_unknown_source:
        notes.append(f"{s.dropped_unknown_source} fact(s) were dropped because they cited a source that was not sent.")
    if s.dropped_no_quote:
        notes.append(f"{s.dropped_no_quote} fact(s) were dropped because their quote was not found in the source.")
    if s.dropped_invalid:
        notes.append(f"{s.dropped_invalid} fact(s) were dropped because they had no usable key or value.")
    return notes
