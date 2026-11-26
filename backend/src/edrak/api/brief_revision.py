"""Build a new immutable brief revision from completed synthesis-stage artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from edrak.api.brief_builder import build_brief
from edrak.briefing.build import build_signals_view
from edrak.contracts import BusinessRequest, OrchestrationResult, VerificationResult
from edrak.contracts.CrossSignal import CrossSignalOutput
from edrak.contracts.DecisionAnalysis import DecisionAnalysisResult
from edrak.core.config import settings
from edrak.db.repositories import results as results_repo
from edrak.guardrails.output import check_brief
from edrak.metrics.registry import get_registry

T = TypeVar("T", bound=BaseModel)


class BriefRevisionError(ValueError):
    """The saved synthesis artifacts cannot safely produce a brief revision."""


def _artifact_root() -> Path:
    path = settings.ARTIFACTS_PATH
    return path if path.is_absolute() else settings.BASE_DIR / path


def _load_stage_output(request_id: str, stage: str, key: str, model: type[T]) -> T:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
        raise BriefRevisionError("The analysis ID is not a valid artifact path component.")
    path = _artifact_root() / "runs" / request_id / "stages" / stage / "output.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BriefRevisionError(f"The {stage} output artifact was not found.") from exc
    except json.JSONDecodeError as exc:
        raise BriefRevisionError(f"The {stage} output artifact is not valid JSON.") from exc
    except OSError as exc:
        raise BriefRevisionError(f"The {stage} output artifact could not be read.") from exc
    if not isinstance(payload, dict):
        raise BriefRevisionError(f"The {stage} output artifact must contain an object.")
    if payload.get("status") != "completed" or not payload.get(key):
        raise BriefRevisionError(f"The {stage} stage has no completed output.")
    try:
        return model.model_validate(payload[key])
    except ValidationError as exc:
        raise BriefRevisionError(f"The {stage} output does not match its contract.") from exc


def load_completed_stage_outputs(request_id: str) -> tuple[CrossSignalOutput, DecisionAnalysisResult]:
    cross_signal = _load_stage_output(request_id, "cross_signal", "cross_signal", CrossSignalOutput)
    decision_analysis = _load_stage_output(
        request_id, "decision_analysis", "decision_analysis", DecisionAnalysisResult
    )
    if cross_signal.research_run_id != request_id or decision_analysis.research_run_id != request_id:
        raise BriefRevisionError("The stage outputs belong to a different analysis.")
    return cross_signal, decision_analysis


async def build_revision_document(
    db: Any,
    brief_row: dict[str, Any],
    cross_signal: CrossSignalOutput,
    decision_analysis: DecisionAnalysisResult,
) -> dict[str, Any]:
    original = brief_row["document"]
    try:
        request = BusinessRequest.model_validate(original["request"])
        orchestration = OrchestrationResult.model_validate(original["orchestration"])
        verification_payload = original.get("verification") or {}
        verification = VerificationResult.model_validate(verification_payload) if verification_payload else None
    except (KeyError, ValidationError) as exc:
        raise BriefRevisionError("The stored brief lacks valid source data for a revision.") from exc

    evidence_numbers = await results_repo.evidence_numbers(db, brief_row["request_id"])
    v3_keys = (
        "headline",
        "kpis",
        "coverage",
        "gaps_panel",
        "sections",
        "cards",
        "metrics",
        "conflicts",
        "signals_view",
        "unmet_dimensions",
        "template",
    )
    v3 = {key: original[key] for key in v3_keys if key in original}
    use_case_template = get_registry().template(request.business_context.use_case.value)
    v3["signals_view"] = build_signals_view(
        cross_signal.model_dump(mode="json"),
        orchestration.results,
        evidence_numbers,
        original.get("conflicts") or [],
        original.get("metrics") or {},
        use_case_template,
    )

    document = build_brief(
        brief_id=brief_row["id"],
        brief_no=brief_row["number"],
        analysis_id=brief_row["request_id"],
        analysis_title=original.get("analysis_title") or brief_row["title"],
        title=original.get("title") or brief_row["title"],
        use_case=original.get("use_case") or brief_row["use_case"],
        created_at=original["created_at"],
        requested_by=original.get("requested_by") or "",
        request=request,
        orchestration=orchestration,
        verification=verification,
        cross_signal=cross_signal.model_dump(mode="json"),
        decision_analysis=decision_analysis.model_dump(mode="json"),
        evidence_numbers=evidence_numbers,
        v3=v3,
    )
    return check_brief(document)
