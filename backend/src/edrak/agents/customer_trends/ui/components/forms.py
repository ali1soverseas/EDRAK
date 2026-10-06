"""The task form of the developer UI: presets, parsing, validation, and the dev-only task.

Pure functions, so the form logic is tested without Streamlit.
"""

import json
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from edrak.agents.customer_trends.schemas.common import UseCase
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import find_repo_root
from edrak.contracts import BusinessContext, CompanyProfile, ResearchTask, WorkerType
from edrak.contracts import UseCase as SharedUseCase

PRESET_FILES = {
    "GitLab pilot: competitive intelligence": "competitive_intelligence.json",
    "Product launch: budgeting app for freelancers in Egypt": "product_launch.json",
    "Market entry: specialty coffee delivery in Saudi Arabia": "market_entry.json",
}
LANGUAGE_OPTIONS = ("ar", "en", "fr", "de", "es", "tr")
FOCUS_OPTIONS = ("pain_points", "demand", "sentiment", "competitor_gaps")
DEFAULT_PROBLEM = "the brief is not valid"
_SHARED_USE_CASES = {
    UseCase.COMPETITIVE_INTELLIGENCE: SharedUseCase.COMPETITIVE_INTELLIGENCE,
    UseCase.MARKET_ENTRY: SharedUseCase.MARKET_ENTRY_EXPANSION,
    UseCase.PRODUCT_LAUNCH: SharedUseCase.PRODUCT_LAUNCH,
}


def presets_dir() -> Path:
    return find_repo_root() / "backend" / "evals" / "customer_trends" / "briefs"


def load_presets(directory: Path | None = None) -> dict[str, TaskBrief]:
    """The sample briefs, the GitLab pilot first."""
    root = directory or presets_dir()
    return {
        title: TaskBrief.model_validate_json((root / name).read_text(encoding="utf-8"))
        for title, name in PRESET_FILES.items()
        if (root / name).is_file()
    }


def parse_list(text: str) -> list[str]:
    """Items separated by commas or new lines, trimmed, without blanks or repeats."""
    parts = (part.strip() for line in text.splitlines() for part in line.split(","))
    return list(dict.fromkeys(part for part in parts if part))


def problems(exc: ValidationError) -> list[str]:
    """Readable validation errors: `field: message`."""
    return [
        f"{'.'.join(str(part) for part in error['loc']) or 'brief'}: {error['msg']}"
        for error in exc.errors()
    ] or [DEFAULT_PROBLEM]


def form_values(brief: TaskBrief) -> dict[str, Any]:
    """The values of the form widgets for a brief."""
    languages = list(brief.languages)
    return {
        "use_case": brief.use_case.value,
        "entity": brief.entity,
        "question": brief.question,
        "market": brief.market or "",
        "geo": brief.geo or "",
        "languages": [code for code in languages if code in LANGUAGE_OPTIONS],
        "extra_languages": ", ".join(code for code in languages if code not in LANGUAGE_OPTIONS),
        "competitors": ", ".join(brief.competitors),
        "focus": list(brief.focus),
        "since": brief.since,
        "until": brief.until,
        "depth": brief.depth.value,
        "max_tool_calls": brief.budget.max_tool_calls,
        "max_cost_usd": brief.budget.max_cost_usd,
        "max_seconds": brief.budget.max_seconds,
        "notes": brief.notes or "",
        "task_id": brief.task_id,
        "run_id": brief.run_id,
    }


def brief_from_form(values: Mapping[str, Any]) -> tuple[TaskBrief | None, list[str]]:
    """A brief from the form values, or the readable problems with them."""
    languages = [*values.get("languages", []), *parse_list(str(values.get("extra_languages", "")))]
    since, until = values.get("since"), values.get("until")
    candidate = {
        "task_id": str(values.get("task_id", "")).strip(),
        "run_id": str(values.get("run_id", "")).strip(),
        "use_case": values.get("use_case"),
        "entity": str(values.get("entity", "")).strip(),
        "question": str(values.get("question", "")).strip(),
        "market": str(values.get("market", "")).strip() or None,
        "geo": str(values.get("geo", "")).strip() or None,
        "languages": list(dict.fromkeys(code.lower() for code in languages)),
        "competitors": parse_list(str(values.get("competitors", ""))),
        "focus": list(values.get("focus", [])),
        "since": since if isinstance(since, date) else None,
        "until": until if isinstance(until, date) else None,
        "depth": values.get("depth", "standard"),
        "budget": {
            "max_tool_calls": values.get("max_tool_calls", 60),
            "max_cost_usd": values.get("max_cost_usd", 2.0),
            "max_seconds": values.get("max_seconds", 300),
        },
        "notes": str(values.get("notes", "")).strip() or None,
    }
    try:
        return TaskBrief.model_validate(candidate), []
    except ValidationError as exc:
        return None, problems(exc)


def brief_from_json(text: str) -> tuple[TaskBrief | None, list[str]]:
    """A brief from pasted JSON, or what is wrong with it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"]
    try:
        return TaskBrief.model_validate(data), []
    except ValidationError as exc:
        return None, problems(exc)


def unique_run_id(run_id: str, existing: Collection[str]) -> str:
    """The run id, or the first `run_id-2`, `run_id-3`, ... that no run has used. A repeated run
    id would return the stored result instead of running again."""
    if run_id not in existing:
        return run_id
    number = 2
    while f"{run_id}-{number}" in existing:
        number += 1
    return f"{run_id}-{number}"


def task_from_brief(brief: TaskBrief) -> ResearchTask:
    """A shared task standing in for the orchestrator's, so the UI can show the WorkerResult."""
    return ResearchTask(
        task_id=brief.task_id,
        parent_request_id="developer-ui",
        worker=WorkerType.CUSTOMER_TRENDS,
        goal=brief.question,
        focus=", ".join(brief.focus) or "overall",
        company_profile=CompanyProfile(name=brief.entity),
        business_context=BusinessContext(
            use_case=_SHARED_USE_CASES[brief.use_case],
            targets=list(brief.competitors),
            focus_areas=list(brief.focus),
        ),
    )
