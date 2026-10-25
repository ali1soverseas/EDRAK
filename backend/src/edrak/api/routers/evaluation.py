"""Read-only evaluation results and ground-truth comparisons for the evaluation dashboard."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from edrak.api.deps import get_current_session
from edrak.api.schemas import SessionOut
from edrak.observability import redact_tool_argument_values

router = APIRouter(prefix="/evaluation", tags=["evaluation"], dependencies=[Depends(get_current_session)])

_REPO = Path(__file__).resolve().parents[5]
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _reports_root() -> Path:
    return Path(os.getenv("EDRAK_EVAL_REPORTS_DIR") or _REPO / "reports")


def _artifact_runs_dir() -> Path:
    return Path(os.getenv("EDRAK_EVAL_RUNS_DIR") or _REPO / "artifacts" / "evaluation_runs")


def _runs() -> list[tuple[str, Path, dict[str, Path]]]:
    """Run id, evaluation directory, and agent name -> report path.

    Supports artifacts/evaluation_runs/<id>/evaluation/<agent>_report.json (preferred)
    and reports/<id>/<agent>/<agent>_report.json.
    """
    found: dict[str, tuple[Path, dict[str, Path]]] = {}
    root = _reports_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name != "compare":
                agents = {a.name: a / f"{a.name}_report.json" for a in d.iterdir()
                          if a.name != "metrics" and a.is_dir() and (a / f"{a.name}_report.json").is_file()}
                if agents:
                    found[d.name] = (next(iter(agents.values())).parent, agents)
    runs_dir = _artifact_runs_dir()
    if runs_dir.is_dir():
        for d in runs_dir.iterdir():
            ev = d / "evaluation"
            agents = {p.name[: -len("_report.json")]: p for p in ev.glob("*_report.json")
                      if p.name != "metrics_report.json" and (_read(p) or {}).get("files")} if ev.is_dir() else {}
            if agents:
                found[d.name] = (ev, agents)
    return [(rid, ev, agents) for rid, (ev, agents) in found.items()]


def _compare_files() -> list[Path]:
    found = list(_REPO.glob("compare_report*.json"))
    compare_dir = _reports_root() / "compare"
    if compare_dir.is_dir():
        found += compare_dir.glob("*.json")
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe(value: str) -> str:
    if not _SAFE_ID.match(value) or value in {".", ".."}:
        raise HTTPException(status_code=404, detail="not_found")
    return value


def _safe_tool_calls(file: dict[str, Any]) -> list[dict[str, Any]]:
    execution = file.get("execution_quality") or {}
    metrics = execution.get("metrics") or {}
    safe_fields = (
        "tool", "provider", "worker", "task_id", "attempt", "status",
        "argument_names", "argument_types", "result_count", "result_summary",
        "result", "result_payload", "latency_ms", "error_code", "fallback_used",
    )
    calls = []
    for call in metrics.get("call_details") or []:
        if not isinstance(call, dict):
            continue
        safe_call = {
            "file": file.get("file"),
            **{key: call[key] for key in safe_fields if key in call},
        }
        if "argument_values" in call:
            safe_call["argument_values"] = redact_tool_argument_values(call["argument_values"])
        for key in ("result", "result_payload"):
            if key in call:
                safe_call[key] = redact_tool_argument_values(call[key])
        calls.append(safe_call)
    return calls


def _telemetry_tool_calls(run_id: str) -> list[dict[str, Any]]:
    telemetry = _read(_artifact_runs_dir() / run_id / "inputs" / "telemetry.json")
    spans = telemetry.get("spans") if isinstance(telemetry, dict) else None
    if not isinstance(spans, list):
        return []

    spans_by_id = {
        str(span["span_id"]): span
        for span in spans
        if isinstance(span, dict) and span.get("span_id")
    }
    calls = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        attributes = span.get("attributes") or {}
        tool = attributes.get("edrak.tool.name")
        if not tool:
            continue

        current = span
        visited: set[str] = set()
        worker = task_id = ""
        attempt = None
        while current:
            parent_attributes = current.get("attributes") or {}
            if parent_attributes.get("edrak.agent.type"):
                worker = str(parent_attributes["edrak.agent.type"])
                task_id = str(parent_attributes.get("edrak.task.id") or "")
                attempt = parent_attributes.get("edrak.task.attempt")
                break
            parent_id = str(current.get("parent_span_id") or "")
            if not parent_id or parent_id in visited:
                break
            visited.add(parent_id)
            current = spans_by_id.get(parent_id, {})

        call = {
            "tool": str(tool),
            "worker": worker,
            "task_id": task_id,
            "attempt": attempt,
        }
        for field, attribute in (
            ("provider", "edrak.tool.provider"),
            ("status", "edrak.tool.status"),
            ("error_code", "edrak.tool.error_code"),
            ("fallback_used", "edrak.tool.fallback"),
            ("argument_values", "edrak.tool.argument.values"),
            ("result", "edrak.tool.result.payload"),
        ):
            if attribute not in attributes:
                continue
            value = attributes[attribute]
            if field in {"argument_values", "result"} and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            call[field] = redact_tool_argument_values(value)
        calls.append(call)
    return calls


def _enrich_tool_calls(
    calls: list[dict[str, Any]], telemetry_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    consumed: set[int] = set()
    for call in calls:
        for index, telemetry_call in enumerate(telemetry_calls):
            if index in consumed or any(
                call.get(field) != telemetry_call.get(field)
                for field in ("tool", "worker", "task_id")
            ):
                continue
            consumed.add(index)
            for field in ("provider", "argument_values", "result", "error_code", "fallback_used"):
                if call.get(field) in (None, "", [], {}) and telemetry_call.get(field) not in (
                    None, "", [], {},
                ):
                    call[field] = telemetry_call[field]
            break
    return calls


def _execution_quality(files: list[dict[str, Any]]) -> dict[str, Any]:
    executions = [f.get("execution_quality") or {} for f in files if f.get("execution_quality")]
    call_details = [call for file in files for call in _safe_tool_calls(file)]
    dimension_names = (
        "tool_selection", "tool_arguments", "tool_result_use",
        "workflow_sequence", "error_handling", "efficiency",
    )
    dimensions: dict[str, Any] = {}
    for name in dimension_names:
        observed = [
            (execution.get("dimensions") or {}).get(name) or {}
            for execution in executions
            if (execution.get("dimensions") or {}).get(name)
        ]
        scores = [float(item["score"]) for item in observed if isinstance(item.get("score"), (int, float))]
        reasons = sorted({str(item["reason"]) for item in observed if item.get("reason")})
        dimensions[name] = {
            "assessable_files": sum(item.get("status") == "assessable" for item in observed),
            "partial_files": sum(item.get("status") == "partial" for item in observed),
            "pending_judge_files": sum(item.get("status") == "pending_judge" for item in observed),
            "files": len(observed),
            "score": round(sum(scores) / len(scores), 3) if scores else None,
            "reasons": reasons,
        }
    metrics = [execution.get("metrics") or {} for execution in executions]
    return {
        "calls": sum(int(metric.get("calls") or 0) for metric in metrics),
        "errors": sum(int(metric.get("errors") or 0) for metric in metrics),
        "repeated_calls": sum(int(metric.get("repeated_calls") or 0) for metric in metrics),
        "call_details": call_details,
        "dimensions": dimensions,
    }


def _agent_summary(
    report: dict[str, Any], telemetry_calls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    files = report.get("files") or []
    checks = [
        c for f in files for c in f.get("checks") or []
        if c.get("passed") is True or c.get("severity") == "error"
    ]
    failed = [{"file": f.get("file"), "check": c.get("check"), "passed": False,
               "detail": c.get("detail"), "severity": c.get("severity")}
              for f in files for c in f.get("checks") or []
              if c.get("passed") is not True and c.get("severity") == "error"]
    file_details = []
    for f in files:
        execution_quality = _execution_quality([f]) if f.get("execution_quality") else None
        if execution_quality is not None:
            execution_quality.pop("dimensions")
            execution_quality["call_details"] = _enrich_tool_calls(
                execution_quality["call_details"], telemetry_calls or []
            )
        file_details.append({
            "file": f.get("file"),
            "metrics": f.get("metrics") or {},
            "checks": [
                c for c in f.get("checks") or []
                if c.get("passed") is True or c.get("severity") == "error"
            ],
            "flagged_units": f.get("flagged_units") or [],
            "execution_quality": execution_quality,
        })
    units = flagged = 0
    for f in files:
        m = f.get("metrics") or {}
        units += m.get("units") or 0
        flagged += m.get("units_flagged") or 0
    agent = report.get("agent") or ""
    return {
        "agent": agent, "model": report.get("model"), "generated_at": report.get("generated_at_utc"),
        "dry_run": report.get("dry_run"), "thresholds": report.get("thresholds") or {},
        "files": len(files), "checks": len(checks), "checks_passed": len(checks) - len(failed),
        "failed_checks": failed, "units": units, "units_flagged": flagged,
        "file_details": file_details,
        "execution_quality": {"dimensions": _execution_quality(files)["dimensions"]},
    }


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    runs = []
    for run_id, _eval_dir, agents in _runs():
        runs.append({"run_id": run_id, "agents": sorted(agents), "updated": _eval_dir.stat().st_mtime})
    runs.sort(key=lambda r: r["updated"], reverse=True)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    _safe(run_id)
    for rid, eval_dir, paths in _runs():
        if rid != run_id:
            continue
        telemetry_calls = _telemetry_tool_calls(run_id)
        agents = []
        for name in sorted(paths):
            report = _read(paths[name])
            if isinstance(report, dict) and report.get("files"):
                agents.append({
                    **_agent_summary(report, telemetry_calls),
                    "agent": report.get("agent") or name,
                })
        if agents:
            initial_input = _read(_artifact_runs_dir() / run_id / "inputs" / "initial_input.json") or {}
            business_context = initial_input.get("business_context")
            pipeline_report = _read(eval_dir / "pipeline_report.json")
            if pipeline_report is None:
                pipeline_report = _read(eval_dir.parent / "pipeline_report.json")
            return {
                "run_id": run_id,
                "goal": initial_input.get("goal"),
                "use_case": (
                    business_context.get("use_case")
                    if isinstance(business_context, dict)
                    else initial_input.get("use_case")
                ),
                "pipeline_report": pipeline_report,
                "agents": agents,
            }
    raise HTTPException(status_code=404, detail="not_found")


@router.get("/runs/{run_id}/agents/{agent}")
async def get_agent_report(run_id: str, agent: str) -> Any:
    _safe(run_id), _safe(agent)
    for rid, _eval_dir, paths in _runs():
        if rid == run_id and agent in paths:
            report = _read(paths[agent])
            if report is not None:
                return report
    raise HTTPException(status_code=404, detail="not_found")


@router.get("/comparisons")
async def list_comparisons() -> dict[str, Any]:
    items = []
    for p in _compare_files():
        data = _read(p)
        if isinstance(data, dict) and "ground_truth" in data:
            items.append({"id": p.stem, "ground_truth": data.get("ground_truth"), "run_bundle": data.get("run_bundle"),
                          "method": data.get("method"), "overview": data.get("overview") or {}, "updated": p.stat().st_mtime})
    return {"comparisons": items}


@router.get("/comparisons/{comparison_id}")
async def get_comparison(comparison_id: str) -> Any:
    _safe(comparison_id)
    for p in _compare_files():
        if p.stem == comparison_id:
            data = _read(p)
            if data is not None:
                return data
    raise HTTPException(status_code=404, detail="not_found")
