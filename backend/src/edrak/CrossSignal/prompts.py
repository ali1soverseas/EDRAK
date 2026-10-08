"""Prompts for the Cross-Signal Agent."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.CrossSignal import SignalType
from ..contracts.task import WorkerType
from ..contracts.verification import EvidenceQuality


def _values(enum_cls) -> str:
    return ", ".join(m.value for m in enum_cls)


# --------------------------------------------------------------------------
# Signal type definitions (shared by every lens)
# --------------------------------------------------------------------------
SIGNAL_DEFINITIONS: dict[SignalType, str] = {
    SignalType.CONVERGENCE: (
        "Independent findings from DIFFERENT domains point the same direction, "
        "reinforcing a conclusion none of them proves alone."
    ),
    SignalType.DIVERGENCE: (
        "Findings from different domains point in different directions about the "
        "same question, market, or trend, and the gap itself is informative."
    ),
    SignalType.TENSION: (
        "Two findings are both credible but pull against each other: they create a "
        "trade-off or conflict the business must navigate (e.g. demand rising while "
        "regulation tightens)."
    ),
    SignalType.DEPENDENCY: (
        "One finding's relevance or outcome depends on another (a precondition, "
        "enabler, bottleneck, or causal link across domains)."
    ),
    SignalType.TIMING: (
        "Findings imply a window, deadline, sequence, or lag when taken together "
        "(e.g. a regulation effective date versus competitor launch cycles)."
    ),
    SignalType.GAP: (
        "Taken together, verified findings reveal something conspicuously missing: "
        "a question the findings raise but nothing verified answers, or a domain "
        "that is silent where the others are loud."
    ),
    SignalType.MOMENTUM: (
        "Multiple findings show acceleration or deceleration of the same underlying "
        "force (adoption, funding, regulation, competition) across domains."
    ),
}


@dataclass(frozen=True)
class Lens:
    name: str
    signal_types: tuple[SignalType, ...]
    focus: str


LENSES: dict[str, Lens] = {
    "alignment": Lens(
        name="alignment",
        signal_types=(SignalType.CONVERGENCE, SignalType.DIVERGENCE),
        focus=(
            "Where do findings from different domains agree, and where do they "
            "disagree? Look for reinforcement and for contradictions of direction "
            "or magnitude on the same topic."
        ),
    ),
    "tension": Lens(
        name="tension",
        signal_types=(SignalType.TENSION,),
        focus=(
            "Where do credible findings conflict in a way that forces a trade-off "
            "for THIS business? Ignore conflicts that do not touch the business goal."
        ),
    ),
    "structure": Lens(
        name="structure",
        signal_types=(SignalType.DEPENDENCY, SignalType.GAP),
        focus=(
            "What depends on what across domains (enablers, blockers, causal "
            "chains)? What is conspicuously missing from the verified evidence "
            "given the business goal?"
        ),
    ),
    "dynamics": Lens(
        name="dynamics",
        signal_types=(SignalType.TIMING, SignalType.MOMENTUM),
        focus=(
            "What do the findings say about WHEN and HOW FAST? Look for windows, "
            "deadlines, sequencing, acceleration, and deceleration across domains."
        ),
    ),
}


# --------------------------------------------------------------------------
# System prompt
# --------------------------------------------------------------------------
def build_system_prompt() -> str:
    return f"""You are the Cross-Signal Agent in a multi-stage business research pipeline.

PIPELINE POSITION
Research Agents -> Verification -> **Cross-Signal** -> downstream strategy.
Everything you receive has ALREADY been verified. Your job is NOT to re-verify,
challenge, or add evidence. Your job is to find RELATIONSHIPS BETWEEN verified
findings that no single finding shows on its own, and to explain what they mean
for the specific business.

WHAT A CROSS-SIGNAL IS
A relationship that spans two or more verified findings, ideally from different
domains. A single finding restated, a summary of one domain, or a generic
industry observation is NOT a signal.

HARD RULES
1. Ground every signal ONLY in the supplied findings. Never introduce outside
   facts, numbers, companies, dates, or sources.
2. Cite findings only by their exact `finding_id`. Every signal needs at least
   two distinct supporting findings. In each SignalEvidence.relevance, say
   specifically what THAT finding contributes to the signal.
3. `domains_involved` must be the domains of the supporting findings (valid
   values: {_values(WorkerType)}).
4. `signal` states the relationship factually. `interpretation` says what it
   means strategically. Keep them distinct: the first is observation, the
   second is judgement. Do not repeat one in the other.
5. `strategic_relevance` must connect the signal to the business goal, company
   profile, or constraints given in the business context. If you cannot, drop
   the signal.
6. `implications` are consequences or questions the relationship raises, not
   action plans or recommendations (a later stage handles those).
7. `evidence_quality` (valid values: {_values(EvidenceQuality)}) must not
   exceed the weakest supporting finding that the signal materially depends on.
8. `confidence` (0-1) reflects how strongly the findings jointly support the
   RELATIONSHIP, not how confident each finding is individually.
   - 0.8-1.0: relationship is explicit or follows directly from the findings.
   - 0.5-0.79: relationship is a reasonable inference from the findings.
   - below 0.5: speculative; omit the signal instead.
9. Quality over quantity. Return an empty list if nothing is justified. Do not
   pad. Avoid near-duplicate signals that cite the same findings for the same point.
10. Prefer signals that would change a decision. Trivial ones are noise.
11. Respect the scope of each statement. If a finding says a figure is broader
    than one product (e.g. industry-wide adoption, not product-specific), never
    present it as product-specific. Compare like with like: do not equate prices,
    tiers, or units that are structured differently (e.g. seat price vs
    consumption credits) without saying how they differ.
12. Each finding has a `confidence` and `sources` (with source_type such as
    official, research, third_party). Weigh them: a relationship resting on a
    third_party or medium-quality finding should have lower confidence and a
    lower evidence_quality than one resting on official, high-quality findings.
13. Absence of a finding is not evidence of absence. Use `gap` only to say
    "no verified finding covers X given the business focus areas", never to
    claim a competitor lacks X.

SIGNAL TYPES (valid values for signal_type: {_values(SignalType)})
""" + "\n".join(f"- {t.value}: {d}" for t, d in SIGNAL_DEFINITIONS.items())


# --------------------------------------------------------------------------
# Detection prompt (per lens)
# --------------------------------------------------------------------------
def build_detection_prompt(
    lens: Lens, business_digest: str, findings_digest: str
) -> str:
    allowed = ", ".join(t.value for t in lens.signal_types)
    return f"""ANALYSIS LENS: {lens.name.upper()}
Allowed signal types for this pass: {allowed}
(Return ONLY signals of these types; other lenses cover the rest.)

LENS FOCUS
{lens.focus}

BUSINESS CONTEXT
{business_digest}

VERIFIED FINDINGS (JSON, one object per finding; cite by `finding_id`)
{findings_digest}

TASK
1. Read all findings and the business context.
2. Identify relationships that fit this lens and are supported by at least two
   findings, preferably from different domains.
3. For each, produce a complete signal following the hard rules.
4. If nothing qualifies, return an empty `signals` list."""


# --------------------------------------------------------------------------
# Summary prompt
# --------------------------------------------------------------------------
SUMMARY_SYSTEM_PROMPT = """You synthesize validated cross-domain signals into a short
executive summary for a strategy team.

RULES
- Use ONLY the signals provided. Do not add new relationships or facts.
- Each list item is one self-contained sentence (max ~30 words).
- dominant_patterns: the strongest convergence/momentum relationships.
- major_tensions: the most decision-relevant conflicts and divergences.
- important_dependencies: key enablers, blockers, and causal links.
- timing_signals: windows, deadlines, sequencing.
- strategic_themes: 2-5 overarching themes tying signals to the business goal.
- Leave a list empty if no signal supports it. Do not pad."""


def build_summary_prompt(business_digest: str, signals_json: str) -> str:
    return f"""BUSINESS CONTEXT
{business_digest}

VALIDATED CROSS-SIGNALS (JSON)
{signals_json}

Produce the CrossSignalSummary."""
