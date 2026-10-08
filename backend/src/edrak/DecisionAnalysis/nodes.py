from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..contracts.DecisionAnalysis import (
    DecisionAnalysisInput,
    DecisionAnalysisResult,
    DecisionAnalysisStatus,
)
from ..contracts.CrossSignal import CrossSignalOutput
from .prompts import (
    DECISION_ANALYSIS_SYSTEM_PROMPT,
    build_decision_analysis_user_prompt,
)
from .schemas import DecisionAnalysisDraft
from .state import DecisionAnalysisState
from dotenv import load_dotenv

load_dotenv(".env")  # Load environment variables from .env file
llm = ChatOpenAI(
    model_name="gpt-4o",
    temperature=0.0,
)
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

    if decision_input is None:
        raise ValueError(
            "Decision Analysis requires a DecisionAnalysisInput."
        )

    if decision_input.cross_signal.status.lower() != "completed":
        raise ValueError(
            "Decision Analysis requires completed Cross-Signal output. "
            f"Received status: {decision_input.cross_signal.status!r}"
        )

    context = _prepare_context(decision_input)

    return {
        "prepared_context": context,
        "warnings": list(state.get("warnings", [])),
    }


def make_analyze_node():
    """
    Create the asynchronous analysis node.

    The LLM is injected by the caller to reuse EDRAK's configured model.
    """

    structured_llm = llm.with_structured_output(
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

        messages = [
            SystemMessage(content=DECISION_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(
                content=build_decision_analysis_user_prompt(context)
            ),
        ]

        draft = await structured_llm.ainvoke(messages)

        if isinstance(draft, dict):
            draft = DecisionAnalysisDraft.model_validate(draft)

        if not isinstance(draft, DecisionAnalysisDraft):
            draft = DecisionAnalysisDraft.model_validate(
                _model_dump(draft)
            )

        return {"draft": draft}

    return analyze_node


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

        validated_draft, validation_warnings = (
            _validate_signal_references(
                draft,
                cross_signal,
            )
        )

        warnings = [
            *cross_signal.warnings,
            *validated_draft.warnings,
            *validation_warnings,
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
                "evidence_reference_validation": "completed",
            },
        )

        return {"result": result}

    return validate_and_assemble_node