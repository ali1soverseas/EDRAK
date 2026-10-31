
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from ..contracts.base import new_id
from ..contracts.CrossSignal import (
    CrossSignal,
    CrossSignalInput,
    CrossSignalOutput,
    CrossSignalSummary,
    DecisionReadyContext,
    SignalType,
)
from ..core.llm import schema_instruction
from .state import (
    CrossSignalSettings,
    CrossSignalState,
    SignalDetectionResult,
)
from .tracing import trace_cross_signal

logger = logging.getLogger(__name__)


# ============================================================================
# ANALYTICAL LENSES
# ============================================================================


LENSES = (
    "strategic_alignment",
    "dependencies_and_evidence_gaps",
    "timing_and_momentum",
)


# ============================================================================
# ENUM / SERIALIZATION HELPERS
# ============================================================================


def _enum_value(value: Any) -> str:

    if value is None:
        return ""

    if hasattr(value, "value"):
        return str(value.value)

    return str(value)


def _safe_json(value: Any) -> str:

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )

    except Exception:
        return str(value)


# ============================================================================
# FINDING HELPERS
# ============================================================================


def _finding_id(
    finding: Any,
) -> str:

    if hasattr(
        finding,
        "finding_id",
    ):
        return str(
            finding.finding_id
        ).strip()

    if isinstance(
        finding,
        dict,
    ):
        return str(
            finding.get(
                "finding_id",
                "",
            )
        ).strip()

    return ""


def _finding_statement(
    finding: Any,
) -> str:

    if hasattr(
        finding,
        "statement",
    ):
        return str(
            finding.statement
        ).strip()

    if isinstance(
        finding,
        dict,
    ):
        return str(
            finding.get(
                "statement",
                "",
            )
        ).strip()

    return ""


def _finding_worker(
    finding: Any,
) -> Any:
    """
    FindingVerdict's authoritative domain field is `worker`.

    Do not use:
        worker_type
        domain
        source_worker
    """

    if hasattr(
        finding,
        "worker",
    ):
        return getattr(
            finding,
            "worker",
        )

    if isinstance(
        finding,
        dict,
    ):
        return finding.get(
            "worker"
        )

    return None


def _finding_domain(
    finding: Any,
) -> str:

    worker = _finding_worker(
        finding
    )

    if worker is None:
        return "unknown"

    return (
        _enum_value(worker)
        or "unknown"
    )


def _finding_quality(
    finding: Any,
) -> str:

    if hasattr(
        finding,
        "evidence_quality",
    ):
        return _enum_value(
            finding.evidence_quality
        )

    if isinstance(
        finding,
        dict,
    ):
        return str(
            finding.get(
                "evidence_quality",
                "",
            )
        )

    return ""


def _finding_confidence(
    finding: Any,
) -> float:

    if hasattr(
        finding,
        "confidence",
    ):
        try:
            return float(
                finding.confidence
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.0

    if isinstance(
        finding,
        dict,
    ):
        try:
            return float(
                finding.get(
                    "confidence",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.0

    return 0.0


def _request_entities(cross_input: CrossSignalInput) -> list[str]:
    """Return the company and named competitors whose mentions need support."""
    request = cross_input.business_request
    profile = request.company_profile
    context = request.business_context
    entities = [profile.name, *profile.aliases, *context.targets]
    entities.extend(competitor.name for competitor in context.competitors)
    entities.extend(
        re.sub(
            r"\s+(?:corporation|incorporated|inc\.?|corp\.?|limited|ltd\.?|llc)\.?$",
            "",
            entity,
            flags=re.IGNORECASE,
        )
        for entity in list(entities)
    )
    return _unique_nonempty(entities)


def _mentions_entity(text: str, entity: str) -> bool:
    return bool(
        re.search(
            rf"(?<!\w){re.escape(entity)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
    )


# ============================================================================
# SUPPORT OBJECT HELPERS
# ============================================================================


def _support_finding_id(
    support: Any,
) -> str:

    if hasattr(
        support,
        "finding_id",
    ):
        return str(
            support.finding_id
        ).strip()

    if isinstance(
        support,
        dict,
    ):
        return str(
            support.get(
                "finding_id",
                "",
            )
        ).strip()

    return ""


def _set_support_finding_id(
    support: Any,
    finding_id: str,
) -> Any:

    if isinstance(
        support,
        dict,
    ):
        support[
            "finding_id"
        ] = finding_id

        return support

    try:
        support.finding_id = finding_id

        return support

    except Exception:
        pass

    if hasattr(
        support,
        "model_copy",
    ):
        try:
            return support.model_copy(
                update={
                    "finding_id": finding_id
                }
            )

        except Exception:
            pass

    return support


# ============================================================================
# FINDING REFERENCE NORMALIZATION
# ============================================================================


def _clean_reference(
    value: Any,
) -> str:

    if value is None:
        return ""

    raw = str(
        value
    ).strip()

    if not raw:
        return ""

    raw = raw.strip(
        "\"'` "
    )

    raw = re.sub(
        r"\s+",
        " ",
        raw,
    )

    return raw


def _extract_reference_candidates(
    value: Any,
) -> list[str]:

    raw = _clean_reference(
        value
    )

    if not raw:
        return []

    candidates: list[str] = []

    def add(
        candidate: str,
    ) -> None:

        candidate = candidate.strip()

        if (
            candidate
            and candidate not in candidates
        ):
            candidates.append(
                candidate
            )

    # Exact raw value.
    add(raw)

    # Case-normalized value.
    add(
        raw.upper()
    )

    # F1 / F01 / f1 / F-1 / F_1
    match = re.search(
        r"\bF[\s_-]*(\d+)\b",
        raw,
        flags=re.IGNORECASE,
    )

    if match:
        number = int(
            match.group(1)
        )

        add(
            f"F{number}"
        )

        add(
            f"F{number:02d}"
        )

    # finding_1 / finding 1 / Finding F1
    match = re.search(
        r"\bfinding[\s_-]*f?(\d+)\b",
        raw,
        flags=re.IGNORECASE,
    )

    if match:
        number = int(
            match.group(1)
        )

        add(
            f"F{number}"
        )

        add(
            f"F{number:02d}"
        )

    # Pure number
    if re.fullmatch(
        r"\d+",
        raw,
    ):
        number = int(
            raw
        )

        add(
            f"F{number}"
        )

        add(
            f"F{number:02d}"
        )

    return candidates


def _resolve_finding_id(
    value: Any,
    findings_by_id: dict[str, Any],
    finding_aliases: dict[str, str],
) -> str | None:

    candidates = (
        _extract_reference_candidates(
            value
        )
    )

    if not candidates:
        return None

    # Exact actual finding ID.
    for candidate in candidates:
        if candidate in findings_by_id:
            return candidate

    # Case-insensitive actual ID.
    actual_ids = {
        str(actual_id)
        .strip()
        .lower(): actual_id
        for actual_id
        in findings_by_id
    }

    for candidate in candidates:
        actual_id = actual_ids.get(
            candidate.lower()
        )

        if actual_id:
            return actual_id

    # Alias resolution.
    aliases = {
        str(alias)
        .strip()
        .upper(): actual_id
        for alias, actual_id
        in finding_aliases.items()
    }

    for candidate in candidates:
        actual_id = aliases.get(
            candidate.upper()
        )

        if actual_id:
            return actual_id

    return None


# ============================================================================
# ACTUAL SUPPORTING FINDINGS
# ============================================================================


def _actual_supporting_findings(
    signal: CrossSignal,
    findings_by_id: dict[str, Any],
    finding_aliases: dict[str, str] | None = None,
) -> list[Any]:

    finding_aliases = (
        finding_aliases
        or {}
    )

    results: list[Any] = []

    seen: set[str] = set()

    for support in (
        signal.supporting_findings
    ):
        raw_id = _support_finding_id(
            support
        )

        resolved_id = (
            _resolve_finding_id(
                raw_id,
                findings_by_id,
                finding_aliases,
            )
        )

        if resolved_id is None:
            continue

        if resolved_id in seen:
            continue

        finding = findings_by_id.get(
            resolved_id
        )

        if finding is None:
            continue

        seen.add(
            resolved_id
        )

        results.append(
            finding
        )

    return results


# ============================================================================
# DOMAIN DERIVATION
# ============================================================================


def _actual_domains(
    signal: CrossSignal,
    findings_by_id: dict[str, Any],
    finding_aliases: dict[str, str] | None = None,
) -> list[Any]:

    finding_aliases = (
        finding_aliases
        or {}
    )

    domains: list[Any] = []

    seen: set[str] = set()

    findings = (
        _actual_supporting_findings(
            signal,
            findings_by_id,
            finding_aliases,
        )
    )

    for finding in findings:
        worker = _finding_worker(
            finding
        )

        if worker is None:
            continue

        key = _enum_value(
            worker
        )

        if (
            not key
            or key in seen
        ):
            continue

        seen.add(
            key
        )

        domains.append(
            worker
        )

    return domains


# ============================================================================
# EVIDENCE QUALITY
# ============================================================================


def _quality_rank(
    value: Any,
) -> int:

    normalized = (
        _enum_value(value)
        .lower()
        .strip()
    )

    ranking = {
        "low": 1,
        "medium": 2,
        "high": 3,
    }

    return ranking.get(
        normalized,
        0,
    )


def _weakest_quality(
    signal: CrossSignal,
    findings_by_id: dict[str, Any],
    finding_aliases: dict[str, str] | None = None,
) -> Any:

    finding_aliases = (
        finding_aliases
        or {}
    )

    findings = (
        _actual_supporting_findings(
            signal,
            findings_by_id,
            finding_aliases,
        )
    )

    if not findings:
        return signal.evidence_quality

    qualities: list[
        tuple[Any, Any]
    ] = []

    for finding in findings:
        if hasattr(
            finding,
            "evidence_quality",
        ):
            quality = (
                finding.evidence_quality
            )

        elif isinstance(
            finding,
            dict,
        ):
            quality = finding.get(
                "evidence_quality"
            )

        else:
            quality = None

        qualities.append(
            (
                finding,
                quality,
            )
        )

    if not qualities:
        return signal.evidence_quality

    _, weakest = min(
        qualities,
        key=lambda item: _quality_rank(
            item[1]
        ),
    )

    return weakest


# ============================================================================
# TEXT NORMALIZATION
# ============================================================================
#
# IMPORTANT:
#
# No stopword filtering.
#
# This normalization is ONLY for deterministic duplicate detection.
#
# It is NOT used to determine whether a relationship exists.
# ============================================================================


def _normalize_text(
    text: str,
) -> str:

    text = str(
        text or ""
    ).lower()

    # Keep words and numbers.
    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text,
    )

    # Collapse whitespace.
    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _token_set(
    text: str,
) -> set[str]:

    normalized = _normalize_text(
        text
    )

    return set(
        normalized.split()
    )


def _jaccard_similarity(
    left: str,
    right: str,
) -> float:

    a = _token_set(
        left
    )

    b = _token_set(
        right
    )

    if not a or not b:
        return 0.0

    return (
        len(a & b)
        /
        len(a | b)
    )


# ============================================================================
# SIGNAL TEXT
# ============================================================================


def _signal_text(
    signal: CrossSignal,
) -> str:

    return " ".join(
        [
            str(
                signal.title
            ),
            str(
                signal.signal
            ),
            str(
                signal.interpretation
            ),
        ]
    )


def _support_ids(
    signal: CrossSignal,
) -> set[str]:

    return {
        _support_finding_id(
            item
        )
        for item
        in signal.supporting_findings
        if _support_finding_id(
            item
        )
    }


def _support_overlap(
    left: CrossSignal,
    right: CrossSignal,
) -> float:

    a = _support_ids(
        left
    )

    b = _support_ids(
        right
    )

    if not a or not b:
        return 0.0

    return (
        len(a & b)
        /
        len(a | b)
    )


# ============================================================================
# DUPLICATE DETECTION
# ============================================================================


def _is_duplicate_signal(
    candidate: CrossSignal,
    existing: CrossSignal,
) -> bool:

    support_overlap = (
        _support_overlap(
            candidate,
            existing,
        )
    )

    text_similarity = (
        _jaccard_similarity(
            _signal_text(candidate),
            _signal_text(existing),
        )
    )

    same_type = (
        _enum_value(
            candidate.signal_type
        )
        ==
        _enum_value(
            existing.signal_type
        )
    )

    if (
        same_type
        and support_overlap >= 0.75
    ):
        return True

    if (
        support_overlap >= 0.60
        and text_similarity >= 0.50
    ):
        return True

    if text_similarity >= 0.78:
        return True

    return False


# ============================================================================
# SIGNAL STRENGTH
# ============================================================================


def _signal_strength(
    signal: CrossSignal,
    findings_by_id: dict[str, Any],
    finding_aliases: dict[str, str] | None = None,
) -> float:

    finding_aliases = (
        finding_aliases
        or {}
    )

    findings = (
        _actual_supporting_findings(
            signal,
            findings_by_id,
            finding_aliases,
        )
    )

    if not findings:
        return 0.0

    confidence = (
        sum(
            _finding_confidence(
                finding
            )
            for finding in findings
        )
        /
        len(findings)
    )

    weakest_quality = (
        _weakest_quality(
            signal,
            findings_by_id,
            finding_aliases,
        )
    )

    quality = (
        _quality_rank(
            weakest_quality
        )
        /
        3.0
    )

    support_bonus = min(
        len(findings) / 5.0,
        1.0,
    )

    return (
        confidence * 0.45
        +
        quality * 0.35
        +
        support_bonus * 0.20
    )


# ============================================================================
# FINDINGS DIGEST
# ============================================================================


def _build_findings_digest(
    findings: list[Any],
) -> str:

    rows: list[str] = []

    for index, finding in enumerate(
        findings,
        start=1,
    ):
        actual_id = _finding_id(
            finding
        )

        rows.append(
            "\n".join(
                [
                    f"SHORT_REFERENCE: F{index}",
                    f"ACTUAL_FINDING_ID: {actual_id}",
                    f"worker: {_finding_domain(finding)}",
                    (
                        "statement: "
                        f"{_finding_statement(finding)}"
                    ),
                    (
                        "evidence_quality: "
                        f"{_finding_quality(finding)}"
                    ),
                    (
                        "confidence: "
                        f"{_finding_confidence(finding):.2f}"
                    ),
                ]
            )
        )

    return "\n\n".join(
        rows
    )


# ============================================================================
# BUSINESS DIGEST
# ============================================================================


def _build_business_digest(
    request: CrossSignalInput,
) -> str:

    business_request = (
        request.business_request
    )

    try:
        payload = (
            business_request.model_dump(
                mode="json"
            )
        )

    except AttributeError:
        payload = dict(
            business_request
        )

    return _safe_json(
        payload
    )


# ============================================================================
# PROMPTS
# ============================================================================


def _lens_system_prompt(
    lens: str,
) -> str:

    base = """
You are the Cross-Signal analysis layer of an enterprise intelligence
platform.

Your input contains:

1. A business request.
2. Verified findings produced by the Verification stage.

The findings have already passed Verification.

Your responsibility is to identify meaningful relationships among the
verified findings.

You are an analytical synthesis layer.

You are NOT the verification layer.

You MUST NOT:

- verify evidence
- re-check sources
- challenge the verification result
- invent contradictions
- invent conflicts
- invent divergence
- invent facts
- introduce external information
- introduce facts from your general knowledge
- invent unsupported business context
- make final recommendations
- prescribe actions
- tell the organization what it should do

The downstream Decision stage owns recommendations and actions.

Your output must describe relationships that are actually supported
by the supplied findings.

A Cross-Signal must explain a relationship between findings.

Do not merely summarize individual findings.

Do not treat shared vocabulary as sufficient evidence of a relationship.

Every Cross-Signal MUST contain at least TWO DISTINCT verified findings.

The same finding may participate in multiple different signals when the
relationships are genuinely different.

Same-domain findings are valid.

Cross-domain findings are valid.

Use the following signal types:

convergence:
Multiple verified findings indicate a meaningful common direction,
pattern, capability movement, or strategic development.

gap:
Verified findings reveal a meaningful missing, weaker, absent, or
underdeveloped dimension relative to the supplied business context.

timing:
The timing, sequence, maturity, release, adoption, or progression of
verified developments materially changes their interpretation.

momentum:
Verified evidence indicates meaningful movement such as adoption,
usage, investment, expansion, acceleration, or measurable change.

dependency:
One verified development materially conditions, enables, constrains,
or depends on another verified development.

Prefer fewer strong signals over many weak signals.

Do not create a signal merely because two findings mention the same
topic.

Do not create recommendations.

Do not prescribe actions.

Do not use information outside the supplied input.
"""

    if lens == "strategic_alignment":
        return base + """

CURRENT LENS: STRATEGIC ALIGNMENT

Focus on:

- meaningful convergence
- strategic relationships
- capability relationships
- competitive relationships
- differentiation
- relationships between the business context and observed developments
- patterns that may matter to downstream strategic evaluation

Do not convert observations into recommendations.
"""

    if lens == "dependencies_and_evidence_gaps":
        return base + """

CURRENT LENS: DEPENDENCIES & EVIDENCE GAPS

Focus on:

- dependencies
- prerequisites and enablers
- bottlenecks and constraints
- important unanswered business questions
- gaps in verified evidence coverage

A gap means insufficient verified coverage. It is not proof that a
capability does not exist.
"""

    if lens == "timing_and_momentum":
        return base + """

CURRENT LENS: TIMING & MOMENTUM

Focus on:

- timing
- sequence
- maturity
- adoption
- usage
- measurable momentum
- release progression
- acceleration
- changes over time
- developments whose timing changes their strategic interpretation

Do not create multiple signals for the same underlying movement.
"""

    raise ValueError(f"Unknown Cross-Signal lens: {lens}")


def _lens_human_prompt(
    lens: str,
    business_digest: str,
    findings_digest: str,
    max_signals: int,
) -> str:

    return f"""
BUSINESS REQUEST
================
{business_digest}


VERIFIED FINDINGS
=================
{findings_digest}


CURRENT ANALYSIS LENS
=====================
{lens}


TASK
====

Generate up to {max_signals} strong Cross-Signals for this lens.

Do NOT fill the limit unnecessarily.

Return an empty list when the supplied findings do not support a
meaningful relationship.

A strong Cross-Signal:

- connects at least two distinct verified findings
- explains why the findings are related
- describes the relationship rather than repeating the findings
- has clear relevance to the supplied business request
- does not depend on external knowledge
- does not invent unsupported facts


FINDING REFERENCE RULE
======================

Every finding contains:

SHORT_REFERENCE:
F1, F2, F3, ...

ACTUAL_FINDING_ID:
the exact canonical finding_id produced by Verification.

For:

supporting_findings[].finding_id

ALWAYS RETURN THE EXACT ACTUAL_FINDING_ID WHEN POSSIBLE.

Do not return a made-up ID.

Do not transform the actual ID.

F1/F2/F3 are human-readable aliases only.

If you cannot confidently identify the exact actual ID, use the
corresponding F-number rather than inventing another identifier.


EXAMPLE
=======

If the supplied finding is:

SHORT_REFERENCE: F1
ACTUAL_FINDING_ID: 7b4a-actual-id-123

then the preferred output is:

{{
    "finding_id": "7b4a-actual-id-123",
    "relevance": "Explains the first part of the observed relationship."
}}


OUTPUT REQUIREMENTS
===================

For every signal provide:

- signal_type
- title
- signal
- interpretation
- supporting_findings
- confidence
- strategic_relevance
- implications
- decision_relevance
- opportunity_relevance
- risk_relevance
- affected_business_areas
- decision_areas
- decision_question
- urgency
- dependencies
- evidence_gaps


SEMANTIC RULES
==============

1. Every signal requires at least TWO DISTINCT verified findings.

2. The relationship must be meaningful, not merely topical.

3. Use only supplied findings.

4. Prefer exact ACTUAL_FINDING_ID values.

5. Never invent evidence.

6. Never invent facts.

6a. Every named company, product, market, or technology in a signal must appear
in at least one of that signal's supporting finding statements. Do not add a
competitor merely to create a comparison.

6b. Do not add dates, quantities, percentages, or other numbers unless they
appear in the supporting finding statements. F-number aliases are references,
not evidence for a numeric claim.

7. Never introduce unsupported competitors, products, markets,
   technologies, risks, opportunities, or business areas.

8. Do not make recommendations.

9. Do not prescribe actions.

10. Do not tell the organization what it should do.

11. affected_business_areas should describe business areas affected by
    the observed relationship.

12. decision_areas should describe areas requiring downstream evaluation.

13. decision_question must be analytical.

14. decision_question must not prescribe an action.

15. opportunity_relevance describes an observed opportunity implication,
    not a proposed action.

16. risk_relevance describes an observed risk implication,
    not a proposed action.

17. dependencies must describe relationships supported by the findings.

18. evidence_gaps should identify genuinely relevant information that
    is missing from the supplied findings and would improve
    interpretation.

19. Do not use evidence_gaps to challenge already verified evidence.

20. Do not duplicate signals.

21. Same-domain findings may form valid signals.

22. Cross-domain findings may form valid signals.

23. If no meaningful relationship exists, return an empty list.

24. Keep the output concise and decision-useful.

25. Use semantic reasoning rather than keyword matching.

26. Do not rely on lexical overlap alone to determine whether a signal
    exists.
"""


# ============================================================================
# MAIN NODE CLASS
# ============================================================================


class CrossSignalNodes:

    def __init__(
        self,
        llm: BaseChatModel,
        settings: CrossSignalSettings,
    ) -> None:

        self.llm = llm
        self.settings = settings

    # ========================================================================
    # PREPARE
    # ========================================================================

    async def prepare(
        self,
        state: CrossSignalState,
    ) -> dict[str, Any]:

        trace_cross_signal(
            "prepare.started",
            state,
        )

        cross_input = state[
            "input"
        ]

        findings = list(
            cross_input.verified_findings
        )

        findings_by_id: dict[
            str,
            Any,
        ] = {}

        finding_aliases: dict[
            str,
            str,
        ] = {}

        # Index verified findings.
        for index, finding in enumerate(
            findings,
            start=1,
        ):

            finding_id = _finding_id(
                finding
            )

            if not finding_id:

                logger.warning(
                    "Verified finding without finding_id: index=%d",
                    index,
                )

                continue

            findings_by_id[
                finding_id
            ] = finding

            finding_aliases[
                f"F{index}"
            ] = finding_id

            finding_aliases[
                f"F{index:02d}"
            ] = finding_id

        finding_domains = {
            finding_id: _finding_domain(
                finding
            )
            for finding_id, finding
            in findings_by_id.items()
        }

        findings_digest = (
            _build_findings_digest(
                findings
            )
        )

        business_digest = (
            _build_business_digest(
                cross_input
            )
        )

        warnings: list[str] = []

        if (
            len(findings)
            < self.settings.min_findings
        ):

            warnings.append(
                "Insufficient verified findings "
                "to produce meaningful cross-signals."
            )

        logger.info(
            (
                "Cross-Signal prepare: "
                "findings=%d indexed=%d aliases=%d"
            ),
            len(findings),
            len(findings_by_id),
            len(finding_aliases),
        )

        result = {
            "findings_by_id": findings_by_id,

            "finding_aliases": finding_aliases,

            "finding_domains": finding_domains,

            "findings_digest": findings_digest,

            "business_digest": business_digest,

            "candidate_signals": [],

            "signals": [],

            "warnings": warnings,

            "stats": {
                "verified_findings": len(
                    findings
                ),
                "unique_findings": len(
                    findings_by_id
                ),
                "finding_aliases": len(
                    finding_aliases
                ),
                "lenses": len(
                    LENSES
                ),
            },
        }

        trace_cross_signal(
            "prepare.completed",
            state,
            verified_findings=len(findings),
            indexed_findings=len(findings_by_id),
            finding_aliases=len(finding_aliases),
            lens_count=len(LENSES),
            warnings=warnings,
        )

        return result

    # ========================================================================
    # ROUTING
    # ========================================================================

    def route_after_prepare(
        self,
        state: CrossSignalState,
    ) -> str:

        return "detect_signals"

    # ========================================================================
    # DETECTION
    # ========================================================================

    async def detect_signals(
        self,
        state: CrossSignalState,
    ) -> dict[str, Any]:

        trace_cross_signal(
            "detect_signals.started",
            state,
            lens_count=len(LENSES),
        )

        findings_digest = state["findings_digest"]
        business_digest = state["business_digest"]

        all_signals: list[CrossSignal] = []

        per_lens_limit = min(
            self.settings.max_signals_per_lens,
            self.settings.max_signals,
        )

        logger.info(
            "Cross-Signal detection started: lenses=%d",
            len(LENSES),
        )

        # Use the existing structured-output schema.
        structured_llm = self.llm.with_structured_output(
            SignalDetectionResult,
            method="json_schema",
        )

        for lens in LENSES:
            try:
                trace_cross_signal(
                    "detect_signals.lens_started",
                    state,
                    lens=lens,
                    max_signals=per_lens_limit,
                )

                logger.info("Running Cross-Signal lens=%s", lens)

                system_prompt = _lens_system_prompt(lens)

                human_prompt = _lens_human_prompt(
                    lens=lens,
                    business_digest=business_digest,
                    findings_digest=findings_digest,
                    max_signals=per_lens_limit,
                )

                response = await structured_llm.ainvoke(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=human_prompt),
                        HumanMessage(
                            content=schema_instruction(
                                SignalDetectionResult
                            )["content"]
                        ),
                    ]
                )

                if isinstance(response, SignalDetectionResult):
                    signals = response.signals
                else:
                    # Handle dictionary-like structured output.
                    parsed = SignalDetectionResult.model_validate(response)
                    signals = parsed.signals

                accepted = 0
                discarded = 0

                for signal in signals:
                    try:
                        # Reject candidates without two distinct support
                        # references before adding them to the pipeline.
                        support_ids = {
                            _support_finding_id(item)
                            for item in signal.supporting_findings
                            if _support_finding_id(item)
                        }

                        if len(support_ids) < 2:
                            discarded += 1
                            logger.warning(
                                "Discarding weak signal from lens=%s: "
                                "only %d distinct supporting finding(s).",
                                lens,
                                len(support_ids),
                            )
                            continue

                        signal.signal_id = new_id()
                        all_signals.append(signal)
                        accepted += 1

                    except Exception:
                        discarded += 1
                        logger.exception(
                            "Discarding malformed candidate from lens=%s",
                            lens,
                        )
                        continue

                logger.info(
                    "Lens %s completed: accepted=%d discarded=%d",
                    lens,
                    accepted,
                    discarded,
                )

                trace_cross_signal(
                    "detect_signals.lens_completed",
                    state,
                    lens=lens,
                    accepted=accepted,
                    discarded=discarded,
                    returned_signals=len(signals),
                    total_candidates=len(all_signals),
                )

            except Exception as exc:
                # A failure in one lens must not stop the remaining lenses.
                logger.exception(
                    "Cross-Signal lens=%s failed; continuing to next lens. "
                    "Error: %s",
                    lens,
                    exc,
                )

                trace_cross_signal(
                    "detect_signals.lens_failed",
                    state,
                    lens=lens,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

                continue

        logger.info(
            "Cross-Signal detection completed: total_candidates=%d",
            len(all_signals),
        )

        trace_cross_signal(
            "detect_signals.completed",
            state,
            total_candidates=len(all_signals),
            lenses=len(LENSES),
        )

        # Always return a valid state update, even if every lens fails.
        return {
            "candidate_signals": all_signals,
            "warnings": (
                state.get("warnings", [])
                + (
                    ["Cross-Signal detection produced no valid candidates."]
                    if not all_signals
                    else []
                )
            ),
        }

    # ========================================================================
    # COLLECTION / NORMALIZATION / VALIDATION
    # ========================================================================

    async def collect_signals(
        self,
        state: CrossSignalState,
    ) -> dict[str, Any]:

        trace_cross_signal(
            "collect_signals.started",
            state,
            candidate_count=len(
                state.get("candidate_signals", [])
            ),
        )

        request_entities = _request_entities(state["input"])
        candidates = list(
            state.get(
                "candidate_signals",
                [],
            )
        )

        findings_by_id = state[
            "findings_by_id"
        ]

        finding_aliases = state.get(
            "finding_aliases",
            {},
        )

        normalized: list[
            CrossSignal
        ] = []

        discarded_missing_support = 0

        discarded_insufficient_support = 0

        unresolved_references: list[
            str
        ] = []

        resolved_reference_count = 0

        references_seen = 0

        # Process candidates.
        for signal_index, signal in enumerate(
            candidates,
            start=1,
        ):

            normalized_support = []

            seen_ids: set[str] = set()

            for support in (
                signal.supporting_findings
            ):

                references_seen += 1

                raw_id = (
                    _support_finding_id(
                        support
                    )
                )

                resolved_id = (
                    _resolve_finding_id(
                        raw_id,
                        findings_by_id,
                        finding_aliases,
                    )
                )

                if resolved_id is None:

                    discarded_missing_support += 1

                    unresolved_references.append(
                        raw_id
                    )

                    logger.warning(
                        (
                            "Unresolved support reference: "
                            "candidate=%d reference=%r"
                        ),
                        signal_index,
                        raw_id,
                    )

                    continue

                if resolved_id in seen_ids:
                    continue

                support = (
                    _set_support_finding_id(
                        support,
                        resolved_id,
                    )
                )

                seen_ids.add(
                    resolved_id
                )

                resolved_reference_count += 1

                normalized_support.append(
                    support
                )

            signal.supporting_findings = (
                normalized_support
            )

            # Minimum two distinct findings.
            if (
                len(
                    signal.supporting_findings
                )
                < 2
            ):

                discarded_insufficient_support += 1

                logger.warning(
                    (
                        "Discarding candidate=%d: "
                        "resolved_support=%d"
                    ),
                    signal_index,
                    len(
                        signal.supporting_findings
                    ),
                )

                continue

            # Recover actual findings.
            actual_findings = (
                _actual_supporting_findings(
                    signal,
                    findings_by_id,
                    finding_aliases,
                )
            )

            if len(
                actual_findings
            ) < 2:

                discarded_insufficient_support += 1

                continue

            support_text = " ".join(
                _finding_statement(finding) for finding in actual_findings
            ).casefold()
            unsupported_entities = [
                entity
                for entity in request_entities
                if _mentions_entity(signal.signal, entity)
                and not _mentions_entity(support_text, entity)
            ]
            if unsupported_entities:
                discarded_insufficient_support += 1
                logger.warning(
                    (
                        "Discarding candidate=%d: named entities lack supporting "
                        "findings: %s"
                    ),
                    signal_index,
                    ", ".join(unsupported_entities),
                )
                continue

            # Derive domains from actual findings.
            signal.domains_involved = (
                _actual_domains(
                    signal,
                    findings_by_id,
                    finding_aliases,
                )
            )

            if not signal.domains_involved:

                logger.warning(
                    (
                        "Discarding candidate=%d: "
                        "no domains could be derived."
                    ),
                    signal_index,
                )

                discarded_insufficient_support += 1

                continue

            # Derive evidence quality from actual findings.
            signal.evidence_quality = (
                _weakest_quality(
                    signal,
                    findings_by_id,
                    finding_aliases,
                )
            )

            if (
                signal.signal_type == SignalType.CONVERGENCE
                and len(
                    {
                        _enum_value(domain)
                        for domain in signal.domains_involved
                    }
                ) < 2
            ):
                discarded_insufficient_support += 1
                logger.warning(
                    (
                        "Discarding candidate=%d: convergence requires findings "
                        "from multiple workers."
                    ),
                    signal_index,
                )
                continue

            signal.signal_id = new_id()

            normalized.append(
                signal
            )

        # Rank candidates.
        normalized.sort(
            key=lambda signal: (
                _signal_strength(
                    signal,
                    findings_by_id,
                    finding_aliases,
                )
            ),
            reverse=True,
        )

        # Deduplicate.
        final_signals: list[
            CrossSignal
        ] = []

        for candidate in normalized:

            duplicate = any(
                _is_duplicate_signal(
                    candidate,
                    existing,
                )
                for existing
                in final_signals
            )

            if duplicate:
                continue

            final_signals.append(
                candidate
            )

            if (
                len(final_signals)
                >= self.settings.max_signals
            ):
                break

        # Fresh IDs.
        for signal in final_signals:
            signal.signal_id = new_id()

        # Diagnostics.
        stats = {
            **state.get(
                "stats",
                {},
            ),

            "candidate_signals": len(
                candidates
            ),

            "normalized_signals": len(
                normalized
            ),

            "final_signals": len(
                final_signals
            ),

            "support_references_seen": (
                references_seen
            ),

            "support_references_resolved": (
                resolved_reference_count
            ),

            "support_references_unresolved": sum(
                1
                for signal in final_signals
                for support in signal.supporting_findings
                if _resolve_finding_id(
                    _support_finding_id(support),
                    findings_by_id,
                    finding_aliases,
                ) is None
            ),

            "candidate_support_references_unresolved": len(
                unresolved_references
            ),

            "discarded_missing_support": (
                discarded_missing_support
            ),

            "discarded_insufficient_support": (
                discarded_insufficient_support
            ),

            "unresolved_candidate_support_references": (
                _unique_nonempty(
                    unresolved_references,
                    limit=(
                        self.settings
                        .max_unresolved_reference_examples
                    ),
                )
            ),
        }

        logger.info(
            (
                "Collection completed: "
                "candidates=%d normalized=%d final=%d "
                "seen_refs=%d resolved_refs=%d unresolved_candidate_refs=%d"
            ),
            len(candidates),
            len(normalized),
            len(final_signals),
            references_seen,
            resolved_reference_count,
            len(unresolved_references),
        )

        trace_cross_signal(
            "collect_signals.completed",
            state,
            candidate_signals=len(candidates),
            normalized_signals=len(normalized),
            final_signals=len(final_signals),
            support_references_seen=references_seen,
            support_references_resolved=resolved_reference_count,
            candidate_support_references_unresolved=len(
                unresolved_references
            ),
            discarded_missing_support=discarded_missing_support,
            discarded_insufficient_support=discarded_insufficient_support,
            stats=stats,
        )

        return {
            "signals": final_signals,
            "stats": stats,
        }

    # ========================================================================
    # SUMMARY
    # ========================================================================

    async def summarize(
        self,
        state: CrossSignalState,
    ) -> dict[str, Any]:

        trace_cross_signal(
            "summarize.started",
            state,
            signal_count=len(
                state.get("signals", [])
            ),
        )

        signals = list(
            state.get(
                "signals",
                [],
            )
        )

        dominant_patterns: list[str] = []

        dependencies: list[str] = []

        business_impacts: list[str] = []

        timing_signals: list[str] = []

        momentum_signals: list[str] = []

        opportunity_signals: list[str] = []

        risk_signals: list[str] = []

        evidence_gaps: list[str] = []

        strategic_themes: list[str] = []

        for signal in signals:

            signal_type = (
                _enum_value(
                    signal.signal_type
                ).lower()
            )

            if signal.signal:
                dominant_patterns.append(
                    signal.signal
                )

            dependencies.extend(
                signal.dependencies
            )

            business_impacts.extend(
                signal.implications
            )

            if signal_type == "timing":
                timing_signals.append(
                    signal.title
                )

            if signal_type == "momentum":
                momentum_signals.append(
                    signal.title
                )

            if signal.opportunity_relevance:
                opportunity_signals.append(
                    (
                        f"{signal.title}: "
                        f"{signal.opportunity_relevance}"
                    )
                )

            if signal.risk_relevance:
                risk_signals.append(
                    (
                        f"{signal.title}: "
                        f"{signal.risk_relevance}"
                    )
                )

            evidence_gaps.extend(
                signal.evidence_gaps
            )

            if signal.strategic_relevance:
                strategic_themes.append(
                    signal.strategic_relevance
                )

        max_items = (
            self.settings
            .max_summary_items
        )

        summary = CrossSignalSummary(
            dominant_patterns=(
                _unique_nonempty(
                    dominant_patterns,
                    limit=max_items,
                )
            ),

            important_dependencies=(
                _unique_nonempty(
                    dependencies,
                    limit=max_items,
                )
            ),

            business_impacts=(
                _unique_nonempty(
                    business_impacts,
                    limit=max_items,
                )
            ),

            timing_signals=(
                _unique_nonempty(
                    timing_signals,
                    limit=max_items,
                )
            ),

            momentum_signals=(
                _unique_nonempty(
                    momentum_signals,
                    limit=max_items,
                )
            ),

            opportunity_relevant_signals=(
                _unique_nonempty(
                    opportunity_signals,
                    limit=max_items,
                )
            ),

            risk_relevant_signals=(
                _unique_nonempty(
                    risk_signals,
                    limit=max_items,
                )
            ),

            evidence_gaps=(
                _unique_nonempty(
                    evidence_gaps,
                    limit=max_items,
                )
            ),

            strategic_themes=(
                _unique_nonempty(
                    strategic_themes,
                    limit=max_items,
                )
            ),
        )

        decision_ready_context = (
            self._build_decision_ready_context(
                state,
                signals,
            )
        )

        result = {
            "summary": summary,

            "decision_ready_context": (
                decision_ready_context
            ),
        }

        trace_cross_signal(
            "summarize.completed",
            state,
            signal_count=len(signals),
            dominant_patterns=len(summary.dominant_patterns),
            important_dependencies=len(summary.important_dependencies),
            business_impacts=len(summary.business_impacts),
            evidence_gaps=len(summary.evidence_gaps),
            decision_questions=len(
                decision_ready_context.decision_questions
            ),
        )

        return result

    # ========================================================================
    # DECISION-READY CONTEXT
    # ========================================================================

    def _build_decision_ready_context(
        self,
        state: CrossSignalState,
        signals: list[CrossSignal],
    ) -> DecisionReadyContext:

        cross_input = state[
            "input"
        ]

        business_request = (
            cross_input.business_request
        )

        business_goal = getattr(
            business_request,
            "goal",
            None,
        )

        if not business_goal:
            business_goal = (
                "Evaluate the strategic implications "
                "of the verified intelligence."
            )

        decision_areas: list[str] = []

        key_signals: list[str] = []

        opportunity_indicators: list[str] = []

        risk_indicators: list[str] = []

        dependencies: list[str] = []

        timing_signals: list[str] = []

        momentum_signals: list[str] = []

        evidence_gaps: list[str] = []

        decision_questions: list[str] = []

        for signal in signals:

            decision_areas.extend(
                signal.affected_business_areas
            )

            decision_areas.extend(
                signal.decision_areas
            )

            key_signals.append(
                signal.title
            )

            if signal.opportunity_relevance:
                opportunity_indicators.append(
                    (
                        f"{signal.title}: "
                        f"{signal.opportunity_relevance}"
                    )
                )

            if signal.risk_relevance:
                risk_indicators.append(
                    (
                        f"{signal.title}: "
                        f"{signal.risk_relevance}"
                    )
                )

            dependencies.extend(
                signal.dependencies
            )

            signal_type = (
                _enum_value(
                    signal.signal_type
                ).lower()
            )

            if signal_type == "timing":
                timing_signals.append(
                    signal.title
                )

            if signal_type == "momentum":
                momentum_signals.append(
                    signal.title
                )

            evidence_gaps.extend(
                signal.evidence_gaps
            )

            if signal.decision_question:
                decision_questions.append(
                    signal.decision_question
                )

        return DecisionReadyContext(
            business_goal=str(
                business_goal
            ),

            decision_areas=(
                _unique_nonempty(
                    decision_areas,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            key_signals=(
                _unique_nonempty(
                    key_signals,
                    limit=self.settings.max_signals,
                )
            ),

            opportunity_indicators=(
                _unique_nonempty(
                    opportunity_indicators,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            risk_indicators=(
                _unique_nonempty(
                    risk_indicators,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            dependencies=(
                _unique_nonempty(
                    dependencies,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            timing_signals=(
                _unique_nonempty(
                    timing_signals,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            momentum_signals=(
                _unique_nonempty(
                    momentum_signals,
                    limit=self.settings.max_signals,
                )
            ),

            evidence_gaps=(
                _unique_nonempty(
                    evidence_gaps,
                    limit=(
                        self.settings
                        .max_summary_items
                    ),
                )
            ),

            decision_questions=(
                _unique_nonempty(
                    decision_questions,
                    limit=self.settings.max_signals,
                )
            ),
        )

    # ========================================================================
    # ASSEMBLE
    # ========================================================================

    async def assemble(
        self,
        state: CrossSignalState,
    ) -> dict[str, Any]:

        trace_cross_signal(
            "assemble.started",
            state,
        )

        cross_input = state[
            "input"
        ]

        signals = list(
            state.get(
                "signals",
                [],
            )
        )

        summary = state[
            "summary"
        ]

        decision_ready_context = state[
            "decision_ready_context"
        ]

        stats = dict(
            state.get(
                "stats",
                {},
            )
        )

        stats.update(
            {
                "final_signal_count": len(
                    signals
                ),

                "decision_question_count": len(
                    decision_ready_context
                    .decision_questions
                ),

                "decision_area_count": len(
                    decision_ready_context
                    .decision_areas
                ),
            }
        )

        output = CrossSignalOutput(
            research_run_id=(
                cross_input.research_run_id
            ),

            status="completed",

            signals=signals,

            summary=summary,

            decision_ready_context=(
                decision_ready_context
            ),

            input_statistics=stats,

            warnings=(
                _unique_nonempty(
                    state.get(
                        "warnings",
                        [],
                    ),
                    limit=10,
                )
            ),
        )

        trace_cross_signal(
            "assemble.completed",
            state,
            status="completed",
            final_signal_count=len(signals),
            decision_question_count=len(
                decision_ready_context.decision_questions
            ),
            decision_area_count=len(
                decision_ready_context.decision_areas
            ),
            stats=stats,
            warnings=output.warnings,
            output=output.model_dump(mode="json"),
        )

        return {
            "output": output,
            "status": "completed",
        }


# ============================================================================
# GENERIC DEDUPLICATION UTILITY
# ============================================================================
#
# No stopwords.
#
# This is intentionally lexical only for duplicate detection.
# Semantic relationship detection remains the LLM's responsibility.
# ============================================================================


def _unique_nonempty(
    values: list[str],
    limit: int | None = None,
) -> list[str]:

    result: list[str] = []

    seen: set[str] = set()

    for value in values:

        value = str(
            value or ""
        ).strip()

        if not value:
            continue

        key = _normalize_text(
            value
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            value
        )

        if (
            limit is not None
            and len(result) >= limit
        ):
            break

    return result
