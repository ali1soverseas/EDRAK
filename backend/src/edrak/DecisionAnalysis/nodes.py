from __future__ import annotations

import logging
import re
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..contracts.CrossSignal import CrossSignalOutput
from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
    DecisionAnalysisStatus,
    DecisionOption,
)
from .options import generate_options
from .prompts import (
    DECISION_ANALYSIS_SYSTEM_PROMPT,
    build_decision_analysis_user_prompt,
)
from .schemas import DecisionAnalysisDraft
from .state import DecisionAnalysisState
from .tracing import trace_decision_analysis

load_dotenv(".env")

logger = logging.getLogger(__name__)


def _model_dump(value: Any) -> Any:
    """Convert Pydantic models and nested values into JSON-safe data."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return {
            key: _model_dump(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [_model_dump(item) for item in value]

    return value


def _prepare_context(
    decision_input: DecisionAnalysisInput,
) -> dict[str, Any]:
    """
    Build a compact, explicit context for the LLM.

    The complete Cross-Signal output is retained, including its summary,
    decision-ready context, and warnings.
    """

    cross_signal = decision_input.cross_signal
    request = decision_input.business_request

    return {
        "research_run_id": decision_input.research_run_id,
        "business_request": _model_dump(request),
        "decision_ready_context": _model_dump(
            cross_signal.decision_ready_context
        ),
        "cross_signal_summary": _model_dump(cross_signal.summary),
        "signals": [
            _model_dump(signal)
            for signal in cross_signal.signals
        ],
        "cross_signal_status": cross_signal.status,
        "cross_signal_warnings": cross_signal.warnings,
        "input_metadata": decision_input.metadata,
    }


def prepare_context_node(
    state: DecisionAnalysisState,
) -> dict[str, Any]:
    """Validate and prepare the Decision Analysis input."""

    decision_input = state.get("input")
    trace_decision_analysis(
        "prepare_context.start",
        research_run_id=getattr(decision_input, "research_run_id", None),
        input_present=decision_input is not None,
    )

    if decision_input is None:
        trace_decision_analysis(
            "prepare_context.error",
            error="Decision Analysis input is missing",
        )
        raise ValueError(
            "Decision Analysis requires a DecisionAnalysisInput."
        )

    if decision_input.cross_signal.status.lower() != "completed":
        trace_decision_analysis(
            "prepare_context.error",
            research_run_id=decision_input.research_run_id,
            cross_signal_status=decision_input.cross_signal.status,
        )
        raise ValueError(
            "Decision Analysis requires completed Cross-Signal output. "
            f"Received status: {decision_input.cross_signal.status!r}"
        )

    context = _prepare_context(decision_input)
    trace_decision_analysis(
        "prepare_context.complete",
        research_run_id=decision_input.research_run_id,
        context=context,
        signal_count=len(context.get("signals", [])),
    )

    return {
        "prepared_context": context,
        "warnings": list(state.get("warnings", [])),
    }


def make_analyze_node(model: BaseChatModel | None = None):
    """
    Create the asynchronous analysis node.

    Construct the model only when building the graph, not during package import.
    """

    analysis_model = (
        model
        if model is not None
        else ChatOpenAI(
            model_name="gpt-4o",
            temperature=0.0,
        )
    )
    structured_llm = analysis_model.with_structured_output(
        DecisionAnalysisDraft
    )

    async def analyze_node(
        state: DecisionAnalysisState,
    ) -> dict[str, Any]:
        context = state.get("prepared_context")

        if not context:
            raise ValueError(
                "Decision Analysis context has not been prepared."
            )

        question_answering_instruction = """
MANDATORY DECISION-QUESTION REQUIREMENT:
- Do not leave decision_questions as unanswered questions.
- For EVERY decision question, provide a direct evidence-based assessment and a
  practical recommendation that addresses that exact question.
- Because decision_questions is a list of strings, format each item as:
  "Question: <the decision question>\\nRecommendation: <specific recommendation>\\nEvidence basis: <verified signal IDs and what they support>\\nRemaining uncertainty: <what is still unknown, or 'None identified'>".
- The recommendation must be consistent with the verified Cross-Signal context.
  Never invent facts, metrics, regulatory deadlines, capabilities, or signal IDs.
- If the evidence cannot support a substantive answer, say so explicitly and
  recommend the specific research or validation step needed to answer it.
- Keep recommended_actions as a separate, prioritized action list. Its actions
  should be consistent with and, where appropriate, operationalize the
  recommendations for the decision questions.
- Do not output a question without its corresponding Recommendation.
"""

        trace_decision_analysis(
            "analyze.start",
            research_run_id=context.get("research_run_id"),
            context=context,
            model_type=type(analysis_model).__name__,
        )
        messages = [
            SystemMessage(
                content=(
                    DECISION_ANALYSIS_SYSTEM_PROMPT
                    + "\n\n"
                    + question_answering_instruction
                )
            ),
            HumanMessage(
                content=build_decision_analysis_user_prompt(context)
            ),
        ]

        try:
            draft = await structured_llm.ainvoke(messages)
        except Exception as exc:
            trace_decision_analysis(
                "analyze.error",
                research_run_id=context.get("research_run_id"),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        trace_decision_analysis(
            "analyze.llm_response",
            research_run_id=context.get("research_run_id"),
            raw_draft=draft,
        )

        if isinstance(draft, dict):
            draft = DecisionAnalysisDraft.model_validate(draft)

        if not isinstance(draft, DecisionAnalysisDraft):
            draft = DecisionAnalysisDraft.model_validate(
                _model_dump(draft)
            )

        trace_decision_analysis(
            "analyze.complete",
            research_run_id=context.get("research_run_id"),
            draft=draft,
        )
        return {"draft": draft}

    return analyze_node


def _ensure_question_recommendations(
    draft: DecisionAnalysisDraft,
) -> tuple[DecisionAnalysisDraft, list[str]]:
    """
    Ensure every decision question is paired with a recommendation.

    The existing schema stores decision_questions as strings, so each entry is
    rendered as a self-contained question/recommendation assessment. If the LLM
    omits a recommendation, add a transparent evidence-gap next step rather than
    inventing an answer.
    """
    questions = list(draft.decision_questions or [])
    updated_questions: list[str] = []
    warnings: list[str] = []

    for index, raw_question in enumerate(questions, start=1):
        question = str(raw_question).strip()
        if not question:
            continue

        if "recommendation:" in question.lower():
            updated_questions.append(question)
            continue

        updated_questions.append(
            f"Question: {question}\n"
            "Recommendation: The available analysis did not provide a "
            "specific evidence-backed answer to this question. Conduct "
            "targeted research to resolve it before making the business "
            "decision.\n"
            "Evidence basis: No question-specific recommendation was supplied "
            "in the generated analysis.\n"
            "Remaining uncertainty: This question remains unresolved."
        )
        warnings.append(
            f"Decision question {index} was missing a recommendation; "
            "an explicit evidence-gap follow-up was added."
        )

    return (
        draft.model_copy(update={"decision_questions": updated_questions}),
        warnings,
    )


def _recommendation_summaries(
    draft: DecisionAnalysisDraft,
    valid_signal_ids: set[str],
) -> list[dict[str, Any]]:
    """Extract the structured question directions for the options prompt/fallback."""
    summaries: list[dict[str, Any]] = []
    for item in draft.decision_questions:
        fields: dict[str, str] = {}
        for line in str(item).splitlines():
            label, separator, value = line.partition(":")
            if separator:
                fields[label.strip().lower()] = value.strip()
        question = fields.get("question", "")
        decision = fields.get("recommendation", "")
        if not question or not decision:
            continue
        evidence_basis = fields.get("evidence basis", "")
        signal_ids = [
            signal_id
            for signal_id in sorted(valid_signal_ids)
            if re.search(
                rf"(?<![\w-]){re.escape(signal_id)}(?![\w-])",
                evidence_basis,
            )
        ]
        summaries.append({
            "question": question,
            "decision": decision,
            "signal_ids": signal_ids,
        })
    return summaries


def make_draft_options_node():
    """Generate distinct strategic options from verified Cross-Signal context."""

    async def draft_options_node(
        state: DecisionAnalysisState,
    ) -> dict[str, Any]:
        decision_input = state.get("input")
        prepared_context = state.get("prepared_context")
        draft = state.get("draft")
        if decision_input is None or prepared_context is None or draft is None:
            raise ValueError(
                "Option drafting requires the input, prepared context, and analysis draft."
            )

        cross_signal = decision_input.cross_signal
        ready_context = cross_signal.decision_ready_context
        metadata = dict(decision_input.metadata)
        metadata["blocking_gaps"] = list(dict.fromkeys([
            *metadata.get("blocking_gaps", []),
            *cross_signal.summary.evidence_gaps,
            *ready_context.evidence_gaps,
        ]))
        options_context = {
            **prepared_context,
            "business_goal": ready_context.business_goal,
            "input_metadata": metadata,
            "recommendation_summaries": _recommendation_summaries(
                draft,
                {signal.signal_id for signal in cross_signal.signals},
            ),
        }

        trace_decision_analysis(
            "options.start",
            research_run_id=decision_input.research_run_id,
            signal_count=len(cross_signal.signals),
            business_goal=ready_context.business_goal,
        )
        if cross_signal.signals:
            options, option_warnings = await generate_options(options_context)
        else:
            options = []
            option_warnings = [
                (
                    "Distinct strategic options were not generated because "
                    "Cross-Signal provided no verified signals."
                )
            ]
        trace_decision_analysis(
            "options.complete",
            research_run_id=decision_input.research_run_id,
            option_count=len(options),
            warnings=option_warnings,
        )
        return {
            "options": [
                option.model_dump(mode="json")
                for option in options
            ],
            "options_warnings": option_warnings,
        }

    return draft_options_node


def _validate_signal_references(
    draft: DecisionAnalysisDraft,
    cross_signal: CrossSignalOutput,
) -> tuple[DecisionAnalysisDraft, list[str]]:
    """
    Ensure every cited signal ID exists in the input.

    Invalid references are removed and recorded as warnings rather than
    silently accepted as evidence.
    """

    valid_ids = {
        signal.signal_id
        for signal in cross_signal.signals
    }

    warnings: list[str] = []

    def filter_ids(
        values: list[str],
        location: str,
    ) -> list[str]:
        valid: list[str] = []

        for signal_id in values:
            if signal_id in valid_ids:
                valid.append(signal_id)
            else:
                warnings.append(
                    f"{location} referenced unknown signal ID "
                    f"{signal_id!r}; the reference was removed."
                )

        # Preserve order while removing duplicates.
        return list(dict.fromkeys(valid))

    opportunities = []

    for item in draft.opportunities:
        signal_ids = filter_ids(
            item.supporting_signal_ids,
            f"Opportunity {item.opportunity_id}",
        )

        if not signal_ids:
            warnings.append(
                f"Opportunity {item.opportunity_id!r} was removed "
                "because it had no valid supporting signal references."
            )
            continue

        opportunities.append(
            item.model_copy(
                update={"supporting_signal_ids": signal_ids}
            )
        )

    risks = []

    for item in draft.risks:
        signal_ids = filter_ids(
            item.supporting_signal_ids,
            f"Risk {item.risk_id}",
        )

        if not signal_ids:
            warnings.append(
                f"Risk {item.risk_id!r} was removed because it had "
                "no valid supporting signal references."
            )
            continue

        risks.append(
            item.model_copy(
                update={"supporting_signal_ids": signal_ids}
            )
        )

    actions = []

    valid_opportunity_ids = {
        item.opportunity_id for item in opportunities
    }
    valid_risk_ids = {
        item.risk_id for item in risks
    }

    for item in draft.recommended_actions:
        signal_ids = filter_ids(
            item.supporting_signal_ids,
            f"Action {item.action_id}",
        )

        opportunity_ids = [
            item_id
            for item_id in item.related_opportunity_ids
            if item_id in valid_opportunity_ids
        ]

        risk_ids = [
            item_id
            for item_id in item.related_risk_ids
            if item_id in valid_risk_ids
        ]

        if item.supporting_signal_ids and not signal_ids:
            warnings.append(
                f"Action {item.action_id!r} had no valid supporting "
                "signals after validation and was removed."
            )
            continue

        actions.append(
            item.model_copy(
                update={
                    "supporting_signal_ids": signal_ids,
                    "related_opportunity_ids": opportunity_ids,
                    "related_risk_ids": risk_ids,
                }
            )
        )

    valid_priority_ids = (
        valid_opportunity_ids
        | valid_risk_ids
        | {item.action_id for item in actions}
    )

    priorities = []

    for item in draft.priorities:
        if item.item_id not in valid_priority_ids:
            warnings.append(
                f"Priority entry for {item.item_id!r} was removed "
                "because the referenced item does not exist in the "
                "validated analysis."
            )
            continue

        priorities.append(item)

    validated_draft = draft.model_copy(
        update={
            "opportunities": opportunities,
            "risks": risks,
            "recommended_actions": actions,
            "priorities": priorities,
        }
    )

    return validated_draft, warnings


def make_validate_and_assemble_node():
    """Validate references and build the final public result."""

    def validate_and_assemble_node(
        state: DecisionAnalysisState,
    ) -> dict[str, Any]:
        decision_input = state.get("input")
        draft = state.get("draft")

        if decision_input is None:
            raise ValueError("Missing Decision Analysis input.")

        if draft is None:
            raise ValueError("Missing Decision Analysis draft.")

        cross_signal = decision_input.cross_signal
        trace_decision_analysis(
            "validate_assemble.start",
            research_run_id=decision_input.research_run_id,
            draft=draft,
            cross_signal=cross_signal,
        )

        draft, question_warnings = _ensure_question_recommendations(draft)

        validated_draft, validation_warnings = (
            _validate_signal_references(
                draft,
                cross_signal,
            )
        )

        warnings = [
            *cross_signal.warnings,
            *validated_draft.warnings,
            *question_warnings,
            *validation_warnings,
            *state.get("options_warnings", []),
        ]
        raw_options = state.get("options", [])
        options = [
            option
            if isinstance(option, DecisionOption)
            else DecisionOption.model_validate(option)
            for option in raw_options
        ]

        has_usable_signals = bool(cross_signal.signals)

        status = (
            DecisionAnalysisStatus.COMPLETED
            if has_usable_signals
            else DecisionAnalysisStatus.INSUFFICIENT_EVIDENCE
        )

        limitations = list(validated_draft.limitations)

        if not has_usable_signals:
            limitations.append(
                "Cross-Signal supplied no signals. "
                "The analysis cannot establish evidence-backed "
                "opportunities or risks from this input."
            )

        # Do not imply that an empty list means that no business risks or
        # opportunities exist. It means none were supported by this input.
        if not validated_draft.opportunities:
            limitations.append(
                "No opportunity assessment survived evidence-reference "
                "validation. This does not prove that no opportunities exist."
            )

        if not validated_draft.risks:
            limitations.append(
                "No risk assessment survived evidence-reference validation. "
                "This does not prove that the business has no risks."
            )

        result = DecisionAnalysisResult(
            research_run_id=decision_input.research_run_id,
            status=status,
            business_goal=(
                cross_signal.decision_ready_context.business_goal
            ),
            executive_summary=validated_draft.executive_summary,
            opportunities=validated_draft.opportunities,
            risks=validated_draft.risks,
            priorities=validated_draft.priorities,
            recommended_actions=validated_draft.recommended_actions,
            options=options,
            decision_questions=(
                validated_draft.decision_questions
                or cross_signal.decision_ready_context.decision_questions
            ),
            additional_information_needed=(
                validated_draft.additional_information_needed
                or cross_signal.decision_ready_context.evidence_gaps
            ),
            limitations=list(dict.fromkeys(limitations)),
            warnings=list(dict.fromkeys(warnings)),
            metadata={
                "source_stage": "cross_signal",
                "cross_signal_count": len(cross_signal.signals),
                "validated_opportunity_count": len(
                    validated_draft.opportunities
                ),
                "validated_risk_count": len(
                    validated_draft.risks
                ),
                "validated_action_count": len(
                    validated_draft.recommended_actions
                ),
                "validated_option_count": len(options),
                "evidence_reference_validation": "completed",
            },
        )

        return {"result": result}

    return validate_and_assemble_node