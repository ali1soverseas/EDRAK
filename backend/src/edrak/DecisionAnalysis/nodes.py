
from __future__ import annotations

import json
import logging
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from ..contracts.CrossSignal import CrossSignalOutput
from ..contracts.DecisionAnalysis import (
    ActionTimeHorizon,
    AssessmentLevel,
    DecisionAnalysisInput,
    DecisionAnalysisResult,
    DecisionAnalysisStatus,
    OpportunityAssessment,
    PriorityAssessment,
    PriorityBand,
    QuestionRecommendation,
    RecommendationDetails,
    RecommendedAction,
    RiskAssessment,
)
from ..core.llm import get_chat_model, schema_instruction
from .state import DecisionAnalysisState

load_dotenv()
logger = logging.getLogger(__name__)


# ============================================================================
# STRUCTURED LLM OUTPUT
# ============================================================================

class QuestionRecommendationsDraft(BaseModel):
    """Structured response containing one recommendation per question."""

    model_config = ConfigDict(extra="forbid")

    question_recommendations: list[QuestionRecommendation] = Field(
        description=(
            "Exactly one complete recommendation per original decision "
            "question, preserving its exact wording and order."
        )
    )


# ============================================================================
# PROMPTS
# ============================================================================

DECISION_ANALYSIS_SYSTEM_PROMPT = """
You are EDRAK's Decision Analysis agent.

Your job is to turn verified Cross-Signal findings into clear, complete,
evidence-grounded recommendations for business decision-makers.

You do not conduct new research. Use only the supplied business request,
original decision questions, and Cross-Signal evidence.

RULES

1. Answer every original decision question exactly once.
   Preserve its exact wording and original order.

2. Give a direct recommendation.
   State what the business should do, avoid, or do conditionally.
   Do not merely rephrase the question.

3. Every recommendation must contain:
   - decision: a clear recommended course of action
   - rationale: why the recommendation follows from the evidence
   - actions: specific, ordered next steps
   - opportunities: relevant potential benefits
   - risks: material risks and relevant mitigations
   - limitations: evidence gaps and constraints
   - success_criteria: observable or measurable acceptance criteria
   - supporting_signal_ids: exact supplied evidence IDs
   - confidence: low, medium, or high

4. Recommendations must be actionable.
   Specify what to compare, review, test, build, measure, or validate.
   Avoid vague advice such as "monitor the market."

5. Distinguish facts, assumptions, and proposed actions.
   Never present a hypothesis or proposed experiment as an established fact.

6. Never invent statistics, financial projections, market shares,
   customer commitments, product capabilities, or evidence IDs.

7. When evidence is insufficient, recommend a conditional next step.
   Explain what is unknown, how to validate it, and what must be
   established before an irreversible decision.

8. Use only supplied signal IDs. If no finding supports a recommendation,
   return an empty supporting_signal_ids list and low confidence.

9. Make the recommendation specific to the question.
   Do not repeat generic opportunities and risks just to fill fields.

10. Return only the structured response.
"""


def build_decision_analysis_user_prompt(
    context: dict[str, Any],
) -> str:
    """Build the recommendation prompt from the canonical context."""

    evidence = {
        "business_goal": context["business_goal"],
        "business_request": context["business_request"],
        "decision_questions": context["decision_questions"],
        "signals": context["signals"],
        "cross_signal": context["cross_signal"],
    }

    return (
        "Generate exactly one complete recommendation for each question "
        "in decision_questions.\n\n"
        "Preserve the exact wording and order of the questions. "
        "Every recommendation must make a direct decision and explain "
        "concrete next steps. Use only supplied evidence IDs.\n\n"
        "INPUT:\n"
        + json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    )


# ============================================================================
# GENERAL HELPERS
# ============================================================================

def _to_dict(value: Any) -> Any:
    """Convert Pydantic models, enums, and nested values to plain values."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if hasattr(value, "value"):
        return value.value

    if isinstance(value, dict):
        return {key: _to_dict(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_dict(item) for item in value]

    return value


def _get(
    value: Any,
    *path: str,
    default: Any = None,
) -> Any:
    """Read nested dictionary keys or object attributes."""

    current = value

    for key in path:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            current = getattr(current, key, default)

        if current is default:
            return default

    return current


def _first_nonempty(*values: Any) -> Any:
    """Return the first value that is not None or empty."""

    for value in values:
        if value is None:
            continue

        if isinstance(value, (str, list, dict, tuple)) and not value:
            continue

        return value

    return None


def _unique_strings(values: Any) -> list[str]:
    """Return unique, non-empty strings in their original order."""

    output: list[str] = []

    for value in values or []:
        if value is None:
            continue

        text = str(value).strip()

        if text and text not in output:
            output.append(text)

    return output


def _business_goal(business_request: Any) -> str:
    """Extract the goal from the canonical business request."""

    value = _first_nonempty(
        _get(business_request, "business_goal"),
        _get(business_request, "goal"),
        _get(business_request, "objective"),
        _get(business_request, "business_context", "business_goal"),
    )

    if value is None:
        return "Support the business decision defined by the request."

    return str(value)


def _normalize_question(question: Any) -> str:
    """Normalize a string or structured decision question."""

    if isinstance(question, str):
        return question.strip()

    value = _first_nonempty(
        _get(question, "question"),
        _get(question, "text"),
        _get(question, "description"),
    )

    return str(value).strip() if value is not None else ""


def _extract_questions(cross_signal: CrossSignalOutput) -> list[str]:
    """Extract the original decision questions from Cross-Signal output."""

    questions = _first_nonempty(
        _get(
            cross_signal,
            "decision_ready_context",
            "decision_questions",
        ),
        _get(cross_signal, "decision_questions"),
        [],
    )

    result: list[str] = []

    for item in questions:
        question = _normalize_question(item)

        if question and question not in result:
            result.append(question)

    return result


def _extract_signals(cross_signal: CrossSignalOutput) -> list[dict[str, Any]]:
    """Extract supplied findings without conducting new research."""

    signals = _first_nonempty(
        _get(cross_signal, "signals"),
        _get(cross_signal, "findings"),
        [],
    )

    result: list[dict[str, Any]] = []

    for signal in signals:
        converted = _to_dict(signal)

        if isinstance(converted, dict):
            result.append(converted)

    return result


def _signal_id(signal: dict[str, Any]) -> str | None:
    """Extract the actual ID assigned to a Cross-Signal finding."""

    identifier = _first_nonempty(
        signal.get("signal_id"),
        signal.get("id"),
        signal.get("signal_ref"),
        signal.get("cross_signal_id"),
    )

    return str(identifier) if identifier is not None else None


def _get_input(state: DecisionAnalysisState) -> DecisionAnalysisInput:
    """Resolve and validate the canonical Decision Analysis input."""

    raw_input = state.get("input")

    if raw_input is None:
        raise ValueError(
            "Decision Analysis requires state['input'] containing "
            "a DecisionAnalysisInput."
        )

    if isinstance(raw_input, DecisionAnalysisInput):
        return raw_input

    return DecisionAnalysisInput.model_validate(raw_input)


def _confidence_value(value: Any) -> str:
    """Normalize confidence to the supported string values."""

    if hasattr(value, "value"):
        value = value.value

    confidence = str(value).lower().strip()

    return confidence if confidence in {"low", "medium", "high"} else "low"


def _confidence_score(confidence: str) -> float:
    """Convert qualitative confidence to a numeric assessment score."""

    return {
        "high": 0.85,
        "medium": 0.60,
        "low": 0.30,
    }[confidence]


def _validated_signal_ids(
    identifiers: list[str],
    valid_signal_ids: set[str],
) -> list[str]:
    """Keep only IDs that actually exist in the supplied evidence."""

    return _unique_strings(
        str(identifier)
        for identifier in identifiers
        if str(identifier) in valid_signal_ids
    )


# ============================================================================
# NODE 1: PREPARE CONTEXT
# ============================================================================

def prepare_context_node(
    state: DecisionAnalysisState,
) -> dict[str, Any]:
    """Prepare the request, questions, and Cross-Signal evidence."""

    decision_input = _get_input(state)
    cross_signal = decision_input.cross_signal

    questions = _extract_questions(cross_signal)
    signals = _extract_signals(cross_signal)

    if not questions:
        raise ValueError(
            "Cross-Signal did not provide decision questions. Expected "
            "decision_ready_context.decision_questions or decision_questions."
        )

    context = {
        "research_run_id": decision_input.research_run_id,
        "business_goal": _business_goal(decision_input.business_request),
        "business_request": _to_dict(decision_input.business_request),
        "decision_questions": questions,
        "cross_signal": _to_dict(cross_signal),
        "signals": signals,
        "input_metadata": _to_dict(decision_input.metadata),
    }

    logger.info(
        "Prepared Decision Analysis context: %d questions, %d signals.",
        len(questions),
        len(signals),
    )

    return {
        "context": context,
        "warnings": list(state.get("warnings", [])),
    }


# ============================================================================
# NODE 2: GENERATE QUESTION-LEVEL RECOMMENDATIONS
# ============================================================================

def make_analyze_node():
    """Create the asynchronous recommendation-generation node."""

    async def analyze_node(
        state: DecisionAnalysisState,
    ) -> dict[str, Any]:
        context = state.get("context")

        if not context:
            raise ValueError(
                "Decision Analysis context is missing. "
                "Run prepare_context_node first."
            )

        # Built through edrak.core.llm so this stage uses the same Ollama Cloud
        # model and per-agent credential scope as every other component. It
        # previously constructed ChatOpenAI directly from OPENAI_API_KEY, which
        # contradicts the migration and only imported at all because a stale
        # langchain-openai sat in the venv: it is not in requirements.txt, so the
        # stage would have failed on a clean install.
        #
        # json_schema plus schema_instruction, measured against function_calling
        # and json_mode on the schemas this project actually sends.
        llm = get_chat_model(agent="orchestrator", temperature=0)
        structured_llm = llm.with_structured_output(
            QuestionRecommendationsDraft, method="json_schema"
        )
        # The schema instruction is not optional. This draft's single top-level
        # field is a list of objects, and json_schema alone let the model return
        # that list bare, without the {"question_recommendations": [...]} wrapper
        # pydantic then refused. Same failure shape as cross-signal.
        schema_note = schema_instruction(QuestionRecommendationsDraft)

        original_questions = context["decision_questions"]
        prompt = build_decision_analysis_user_prompt(context)

        logger.info(
            "Generating recommendations for %d questions.",
            len(original_questions),
        )

        draft = await structured_llm.ainvoke(
            [
                SystemMessage(content=DECISION_ANALYSIS_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
                HumanMessage(content=schema_note["content"]),
            ]
        )

        if isinstance(draft, QuestionRecommendationsDraft):
            parsed = draft
        else:
            parsed = QuestionRecommendationsDraft.model_validate(
                _to_dict(draft)
            )

        # Keep the first model-generated recommendation for each exact question.
        by_question: dict[str, QuestionRecommendation] = {}

        for item in parsed.question_recommendations:
            key = item.question.strip()

            if key not in by_question:
                by_question[key] = item

        warnings = list(state.get("warnings", []))
        recommendations: list[QuestionRecommendation] = []

        for question in original_questions:
            item = by_question.get(question)

            if item is None:
                warnings.append(
                    f"The model omitted a recommendation for: {question}"
                )

                # Conservative fallback: do not fabricate a decision or evidence.
                item = QuestionRecommendation(
                    question=question,
                    recommendation=RecommendationDetails(
                        decision=(
                            "Defer an irreversible decision until the "
                            "evidence needed to answer this question "
                            "has been validated."
                        ),
                        rationale=(
                            "A complete recommendation was not generated "
                            "for this question. The missing answer is not "
                            "evidence for or against the proposed decision."
                        ),
                        actions=[
                            (
                                "Identify the specific evidence required "
                                "to answer the original question."
                            ),
                            (
                                "Obtain and validate that evidence before "
                                "making an irreversible commitment."
                            ),
                        ],
                        opportunities=[],
                        risks=[
                            (
                                "Deciding without adequate evidence may "
                                "expose the business to avoidable uncertainty."
                            )
                        ],
                        success_criteria=[
                            (
                                "A validated recommendation directly answers "
                                "the original question and identifies its "
                                "supporting evidence."
                            )
                        ],
                        supporting_signal_ids=[],
                        limitations=[
                            "The model did not generate a complete recommendation."
                        ],
                        confidence="low",
                    ),
                )

            # Preserve exact canonical wording regardless of model output.
            recommendations.append(
                item.model_copy(update={"question": question})
            )

        return {
            "question_recommendations": recommendations,
            "warnings": warnings,
        }

    return analyze_node


# ============================================================================
# NODE 2b: DISTINCT STRATEGIC OPTIONS
# ============================================================================

def make_options_node():
    async def options_node(state: DecisionAnalysisState) -> dict[str, Any]:
        from .options import generate_options

        context = dict(state.get("context") or {})
        context["recommendation_summaries"] = [
            {
                "question": getattr(r, "question", None) or _to_dict(r).get("question"),
                "decision": (_to_dict(r).get("recommendation") or {}).get("decision", ""),
                "signal_ids": (_to_dict(r).get("recommendation") or {}).get("supporting_signal_ids", []),
                "opportunities": (_to_dict(r).get("recommendation") or {}).get("opportunities", []),
                "risks": (_to_dict(r).get("recommendation") or {}).get("risks", []),
            }
            for r in state.get("question_recommendations", [])
        ]
        try:
            options, warnings = await generate_options(context)
        except Exception as exc:  # noqa: BLE001 - options are an enhancement; the recommendations stand without them
            logger.warning("Options step failed: %s", exc)
            options, warnings = [], [f"Options step failed: {type(exc).__name__}"]
        return {"options": options, "warnings": list(state.get("warnings", [])) + warnings}

    return options_node


# ============================================================================
# NODE 3: VALIDATE AND ASSEMBLE STRUCTURED RESULT
# ============================================================================

def make_validate_and_assemble_node():
    """Validate evidence references and assemble all contract models."""

    def validate_and_assemble_node(
        state: DecisionAnalysisState,
    ) -> dict[str, Any]:
        decision_input = _get_input(state)
        context = state.get("context", {})

        raw_recommendations = state.get("question_recommendations", [])

        if not raw_recommendations:
            raise ValueError("No question recommendations were generated.")

        valid_signal_ids = {
            identifier
            for signal in context.get("signals", [])
            if (identifier := _signal_id(signal)) is not None
        }

        warnings = list(state.get("warnings", []))
        validated: list[QuestionRecommendation] = []

        for raw_item in raw_recommendations:
            item = (
                raw_item
                if isinstance(raw_item, QuestionRecommendation)
                else QuestionRecommendation.model_validate(_to_dict(raw_item))
            )

            details = item.recommendation
            requested_ids = [
                str(identifier)
                for identifier in details.supporting_signal_ids
            ]

            accepted_ids = _validated_signal_ids(
                requested_ids,
                valid_signal_ids,
            )

            rejected_ids = [
                identifier
                for identifier in requested_ids
                if identifier not in valid_signal_ids
            ]

            if rejected_ids:
                warnings.append(
                    f"Removed invalid signal IDs for '{item.question}': "
                    f"{rejected_ids}"
                )

            confidence = _confidence_value(details.confidence)
            rationale = details.rationale

            if not accepted_ids:
                confidence = "low"
                rationale = (
                    f"{rationale} Evidence limitation: no valid supporting "
                    "Cross-Signal IDs were supplied for this recommendation."
                )
                warnings.append(
                    f"Question '{item.question}' has no valid evidence IDs; "
                    "confidence was set to low."
                )

            validated_details = details.model_copy(
                update={
                    "supporting_signal_ids": accepted_ids,
                    "confidence": confidence,
                    "rationale": rationale,
                }
            )

            validated.append(
                item.model_copy(
                    update={"recommendation": validated_details}
                )
            )

        # Ensure every original question appears exactly once and in order.
        original_questions = context.get("decision_questions", [])
        returned_questions = [item.question for item in validated]

        if len(returned_questions) != len(original_questions):
            raise ValueError(
                "Decision Analysis must return exactly one recommendation "
                "for every original decision question."
            )

        if len(set(returned_questions)) != len(returned_questions):
            raise ValueError(
                "Decision Analysis returned duplicate decision questions."
            )

        by_question = {item.question: item for item in validated}
        missing = [
            question
            for question in original_questions
            if question not in by_question
        ]

        if missing:
            raise ValueError(
                f"Decision Analysis omitted original questions: {missing}"
            )

        validated = [by_question[q] for q in original_questions]

        # Contract-required structured collections.
        opportunities: list[OpportunityAssessment] = []
        risks: list[RiskAssessment] = []
        priorities: list[PriorityAssessment] = []
        actions: list[RecommendedAction] = []

        signals_by_id = {
            identifier: signal
            for signal in context.get("signals", [])
            if (identifier := _signal_id(signal)) is not None
        }
        seen_opportunities: dict[str, OpportunityAssessment] = {}
        seen_risks: dict[str, RiskAssessment] = {}

        for item in validated:
            details = item.recommendation
            evidence_ids = details.supporting_signal_ids
            confidence = _confidence_value(details.confidence)
            score = _confidence_score(confidence)
            fit, impact, urgency, feasibility, likelihood = _levels_for(evidence_ids, signals_by_id, confidence)
            band = band_of(fit, impact, urgency, feasibility)

            # OpportunityAssessment and RiskAssessment require at least one
            # evidence ID. Never create an assessment without valid evidence.
            if evidence_ids:
                for description in _unique_strings(details.opportunities):
                    key = _norm_key(description)
                    if key in seen_opportunities:  # the same point made under several questions is one opportunity
                        prior = seen_opportunities[key]
                        prior.supporting_signal_ids = sorted(set(prior.supporting_signal_ids) | set(evidence_ids))
                        continue
                    opportunity = OpportunityAssessment(
                        title=description[:160],
                        description=description,
                        business_rationale=details.rationale,
                        potential_impact=description,
                        strategic_fit=fit,
                        feasibility=feasibility,
                        urgency=urgency,
                        supporting_signal_ids=evidence_ids,
                        assumptions=[],
                        evidence_gaps=list(details.limitations),
                        confidence=score,
                    )
                    seen_opportunities[key] = opportunity
                    opportunities.append(opportunity)
                    priorities.append(
                        PriorityAssessment(
                            item_type="opportunity", item_id=opportunity.opportunity_id, priority=band,
                            strategic_fit=fit, impact=impact, urgency=urgency, feasibility=feasibility,
                            rationale="Scored from the supporting signals' urgency and confidence and this recommendation's evidence strength.",
                            key_uncertainties=list(details.limitations),
                        )
                    )

                for description in _unique_strings(details.risks):
                    key = _norm_key(description)
                    if key in seen_risks:
                        prior = seen_risks[key]
                        prior.supporting_signal_ids = sorted(set(prior.supporting_signal_ids) | set(evidence_ids))
                        continue
                    risk = RiskAssessment(
                        title=description[:160],
                        description=description,
                        business_rationale=details.rationale,
                        potential_consequences=[description],
                        likelihood=likelihood,
                        impact=impact,
                        urgency=urgency,
                        supporting_signal_ids=evidence_ids,
                        dependencies=[],
                        mitigation_considerations=list(details.actions),
                        evidence_gaps=list(details.limitations),
                        confidence=score,
                    )
                    seen_risks[key] = risk
                    risks.append(risk)
                    priorities.append(
                        PriorityAssessment(
                            item_type="risk", item_id=risk.risk_id, priority=band_of(impact, urgency, likelihood),
                            strategic_fit=fit, impact=impact, urgency=urgency, feasibility=feasibility,
                            rationale="Scored from likelihood, impact and urgency taken from the supporting signals.",
                            key_uncertainties=list(details.limitations),
                        )
                    )

            related_opps = [o.opportunity_id for o in opportunities if set(o.supporting_signal_ids) & set(evidence_ids)]
            related_risks = [r.risk_id for r in risks if set(r.supporting_signal_ids) & set(evidence_ids)]
            for index, description in enumerate(
                _unique_strings(details.actions),
                start=1,
            ):
                action = RecommendedAction(
                    title=description[:120],
                    description=description,
                    rationale=details.rationale,
                    intended_outcome=details.decision,
                    priority=PriorityBand.UNDETERMINED if confidence == "low" else band,
                    time_horizon=horizon_of(urgency),
                    supporting_signal_ids=evidence_ids,
                    related_opportunity_ids=related_opps,
                    related_risk_ids=related_risks,
                    prerequisites=list(details.limitations),
                    success_indicators=list(details.success_criteria),
                    confidence=score,
                    requires_human_approval=True,
                )
                actions.append(action)

                priorities.append(
                    PriorityAssessment(
                        item_type="action", item_id=action.action_id, priority=action.priority,
                        strategic_fit=fit, impact=impact, urgency=urgency, feasibility=feasibility,
                        rationale="Derived from the question-level recommendation; priority follows its evidence strength and urgency.",
                        key_uncertainties=list(details.limitations),
                    )
                )

        limitations = _unique_strings(
            limitation
            for item in validated
            for limitation in item.recommendation.limitations
        )

        additional_information_needed = _unique_strings(
            limitation
            for item in validated
            if not item.recommendation.supporting_signal_ids
            for limitation in item.recommendation.limitations
        )

        unsupported_questions = [
            item.question
            for item in validated
            if not item.recommendation.supporting_signal_ids
        ]

        insufficient_evidence = (
            not valid_signal_ids or bool(unsupported_questions)
        )

        status = (
            DecisionAnalysisStatus.INSUFFICIENT_EVIDENCE
            if insufficient_evidence
            else DecisionAnalysisStatus.COMPLETED
        )

        if unsupported_questions:
            warnings.append(
                "Recommendations lacking valid supporting evidence: "
                + "; ".join(unsupported_questions)
            )

        business_goal = context.get(
            "business_goal",
            "Support the business decision defined by the request.",
        )

        executive_summary = _executive_summary(validated, business_goal)

        metadata = _to_dict(decision_input.metadata) or {}
        if not isinstance(metadata, dict):
            metadata = {"input_metadata": metadata}

        result = DecisionAnalysisResult(
            research_run_id=decision_input.research_run_id,
            status=status,
            business_goal=business_goal,
            executive_summary=executive_summary,
            question_recommendations=validated,
            opportunities=opportunities,
            risks=risks,
            priorities=priorities,
            recommended_actions=actions,
            options=list(state.get("options") or []),
            decision_questions=original_questions,
            additional_information_needed=additional_information_needed,
            limitations=limitations,
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                **metadata,
                "recommendation_count": len(validated),
                "valid_cross_signal_ids": sorted(valid_signal_ids),
                "unsupported_questions": unsupported_questions,
                "opportunity_count": len(opportunities),
                "risk_count": len(risks),
                "priority_count": len(priorities),
                "recommended_action_count": len(actions),
            },
        )

        logger.info(
            "Decision Analysis completed: status=%s, recommendations=%d, "
            "opportunities=%d, risks=%d, priorities=%d, actions=%d.",
            result.status.value,
            len(result.question_recommendations),
            len(result.opportunities),
            len(result.risks),
            len(result.priorities),
            len(result.recommended_actions),
        )

        return {
            "result": result,
            "status": result.status,
            "warnings": result.warnings,
            "question_recommendations": result.question_recommendations,
        }

    return validate_and_assemble_node


def _norm_key(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())[:120]


_URGENCY = {"immediate": AssessmentLevel.HIGH, "high": AssessmentLevel.HIGH, "medium": AssessmentLevel.MODERATE, "low": AssessmentLevel.LOW}


def _levels_for(signal_ids: list[str], signals: dict[str, Any], confidence: str):
    """(strategic_fit, impact, urgency, feasibility, likelihood) from the supporting signals and the evidence strength."""
    sigs = [_to_dict(signals[i]) for i in signal_ids if i in signals]
    order = [AssessmentLevel.HIGH, AssessmentLevel.MODERATE, AssessmentLevel.LOW]
    urgencies = [_URGENCY[str(getattr(s.get("urgency"), "value", s.get("urgency")) or "").lower()] for s in sigs
                 if str(getattr(s.get("urgency"), "value", s.get("urgency")) or "").lower() in _URGENCY]
    urgency = min(urgencies, key=order.index) if urgencies else AssessmentLevel.UNKNOWN
    confs = [float(s["confidence"]) for s in sigs if isinstance(s.get("confidence"), (int, float))]
    top = max(confs) if confs else None
    scaled = AssessmentLevel.UNKNOWN if top is None else AssessmentLevel.HIGH if top >= 0.75 else AssessmentLevel.MODERATE if top >= 0.5 else AssessmentLevel.LOW
    relevant = any(str(getattr(s.get("decision_relevance"), "value", s.get("decision_relevance"))) != "informational" for s in sigs)
    fit = AssessmentLevel.HIGH if relevant and scaled is AssessmentLevel.HIGH else AssessmentLevel.MODERATE if relevant else (AssessmentLevel.LOW if sigs else AssessmentLevel.UNKNOWN)
    feasibility = {"high": AssessmentLevel.HIGH, "medium": AssessmentLevel.MODERATE, "low": AssessmentLevel.LOW}.get(confidence, AssessmentLevel.UNKNOWN)
    return fit, scaled, urgency, feasibility, scaled


def _executive_summary(validated: list[QuestionRecommendation], goal: str) -> str:
    """The direction the analysis points to, in the analysis's own words. Never a count of generated items."""
    directions = []
    for item in validated:
        d = item.recommendation.decision.strip().rstrip(".")
        if d and d not in directions:
            directions.append(d)
    if not directions:
        return goal
    return "; ".join(directions[:2]) + "."


_LEVEL_POINTS = {AssessmentLevel.HIGH: 3, AssessmentLevel.MODERATE: 2, AssessmentLevel.LOW: 1}


def band_of(*levels: AssessmentLevel) -> PriorityBand:
    """Priority from the dimensions the analysis already scored. Undetermined only when none is known."""
    pts = [_LEVEL_POINTS[v] for v in levels if v in _LEVEL_POINTS]
    if not pts:
        return PriorityBand.UNDETERMINED
    avg = sum(pts) / len(pts)
    return PriorityBand.HIGH if avg >= 2.5 else PriorityBand.MEDIUM if avg >= 1.75 else PriorityBand.LOW


def horizon_of(urgency: AssessmentLevel) -> ActionTimeHorizon:
    return {AssessmentLevel.HIGH: ActionTimeHorizon.IMMEDIATE, AssessmentLevel.MODERATE: ActionTimeHorizon.SHORT_TERM,
            AssessmentLevel.LOW: ActionTimeHorizon.MEDIUM_TERM}.get(urgency, ActionTimeHorizon.UNDETERMINED)


# ============================================================================
# NODE REFERENCES
# ============================================================================

analyze_node = make_analyze_node()
options_node = make_options_node()
validate_and_assemble_node = make_validate_and_assemble_node()
