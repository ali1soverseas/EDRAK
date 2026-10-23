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
    """Run id, directory holding observability.json, and agent name -> report path.

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


def _domain_metrics(agent: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [f.get("metrics") or {} for f in files]

    def values(name: str) -> list[float]:
        return [float(m[name]) for m in metrics if isinstance(m.get(name), (int, float))]

    def total(name: str) -> int:
        return sum(int(m.get(name) or 0) for m in metrics)

    if agent == "rag":
        retrieval = [
            (m.get("mean_retrieval_score"), m.get("retrieved_chunks") or 0)
            for m in metrics
            if isinstance(m.get("mean_retrieval_score"), (int, float))
        ]
        retrieved = sum(count for _, count in retrieval)
        answer_sources = sorted({
            str(m["answer_source"])
            for m in metrics
            if m.get("answer_source")
        })
        result = {
            "mean_retrieval_score": (
                round(sum(score * count for score, count in retrieval) / retrieved, 3)
                if retrieved else None
            ),
            "context_precision": _rag_metric(metrics, "context_precision"),
            "context_recall": _rag_metric(metrics, "context_recall"),
            "context_relevancy": _rag_metric(metrics, "context_relevancy"),
            "answer_source": ", ".join(answer_sources) if answer_sources else None,
            "queries": total("queries"),
            "retrieved_chunks": total("retrieved_chunks"),
            "empty_chunks": total("empty_chunks"),
            "duplicate_chunk_pairs": total("duplicate_chunk_pairs"),
            "retrieval_latency_ms": _weighted_mean(metrics, "retrieval_latency_ms", "retrieval_latency_calls"),
        }
        return result
    if agent == "orchestration":
        completion = values("task_completion_pct")
        statuses = sorted({str(m["execution_quality_status"]) for m in metrics if m.get("execution_quality_status")})
        return {
            "task_completion_pct": round(sum(completion) / len(completion), 1) if completion else None,
            "workflow_success_pct": _mean(metrics, "workflow_success_pct"),
            "dependency_integrity_pct": _mean(metrics, "dependency_integrity_pct"),
            "failure_rate_pct": _mean(metrics, "failure_rate_pct"),
            "retry_rate_pct": _mean(metrics, "retry_rate_pct"),
            "execution_duration_seconds": _mean(metrics, "execution_duration_seconds"),
            "planned_tasks": total("planned_tasks"),
            "failed_tasks": total("failed_tasks"),
            "dependency_edges": total("dependency_edges"),
            "dependency_violations": total("dependency_violations"),
            "missing_tasks": total("missing_tasks"),
            "missing_dependencies": total("missing_dependencies"),
            "execution_quality_status": ", ".join(statuses) if statuses else None,
        }
    return {}


def _mean(metrics: list[dict[str, Any]], name: str) -> float | None:
    values = [float(m[name]) for m in metrics if isinstance(m.get(name), (int, float))]
    return round(sum(values) / len(values), 3) if values else None


def _weighted_mean(metrics: list[dict[str, Any]], name: str, weight_name: str) -> float | None:
    pairs = [
        (float(m[name]), float(m.get(weight_name) or 0))
        for m in metrics
        if isinstance(m.get(name), (int, float)) and float(m.get(weight_name) or 0) > 0
    ]
    weight = sum(count for _, count in pairs)
    return round(sum(value * count for value, count in pairs) / weight, 3) if weight else _mean(metrics, name)


def _rag_metric(metrics: list[dict[str, Any]], name: str) -> dict[str, Any]:
    score_key = f"{name}_score"
    scores = [float(m[score_key]) for m in metrics if isinstance(m.get(score_key), (int, float))]
    errors = sorted({str(m[f"{name}_error"]) for m in metrics if m.get(f"{name}_error")})
    return {
        "score": round(sum(scores) / len(scores), 3) if scores else None,
        "reason": "; ".join(errors) if errors else None,
    }


def _safe_tool_calls(file: dict[str, Any]) -> list[dict[str, Any]]:
    execution = file.get("execution_quality") or {}
    metrics = execution.get("metrics") or {}
    safe_fields = (
        "tool", "provider", "worker", "task_id", "attempt", "status",
        "argument_names", "argument_types", "result_count", "result_summary",
        "latency_ms", "error_code", "fallback_used",
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
        calls.append(safe_call)
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
    statuses = [str(execution.get("status")) for execution in executions if execution.get("status")]
    metrics = [execution.get("metrics") or {} for execution in executions]
    return {
        "status": "not_assessable" if not executions else "partial" if any(s != "assessable" for s in statuses) else "assessable",
        "files_with_trace": sum(bool((execution.get("metrics") or {}).get("calls")) for execution in executions),
        "calls": sum(int(metric.get("calls") or 0) for metric in metrics),
        "errors": sum(int(metric.get("errors") or 0) for metric in metrics),
        "repeated_calls": sum(int(metric.get("repeated_calls") or 0) for metric in metrics),
        "call_details": call_details,
        "dimensions": dimensions,
    }


def _agent_summary(report: dict[str, Any]) -> dict[str, Any]:
    files = report.get("files") or []
    checks = [c for f in files for c in f.get("checks") or []]
    passed = [{"file": f.get("file"), "check": c.get("check"), "passed": True,
               "detail": c.get("detail"), "severity": c.get("severity")}
              for f in files for c in f.get("checks") or [] if c.get("passed") is True]
    failed = [{"file": f.get("file"), "check": c.get("check"), "passed": False,
               "detail": c.get("detail"), "severity": c.get("severity")}
              for f in files for c in f.get("checks") or [] if c.get("passed") is False]
    file_details = [
        {
            "file": f.get("file"),
            "metrics": f.get("metrics") or {},
            "checks": f.get("checks") or [],
            "flagged_units": f.get("flagged_units") or [],
            "execution_quality": _execution_quality([f]) if f.get("execution_quality") else None,
        }
        for f in files
    ]
    judged: dict[str, list[float]] = {}
    units = flagged = 0
    for f in files:
        m = f.get("metrics") or {}
        units += m.get("units") or 0
        flagged += m.get("units_flagged") or 0
        for key, value in m.items():
            if key.endswith("_avg") and isinstance(value, (int, float)):
                judged.setdefault(key[:-4], []).append(float(value))
        rag_judge_metrics = (("context_relevancy_score", "context_relevancy"),)
        if str(report.get("agent") or "").casefold() != "rag":
            rag_judge_metrics += (
                ("answer_relevancy_score", "relevancy"),
                ("faithfulness_score", "faithfulness"),
            )
        for key, metric in rag_judge_metrics:
            value = m.get(key)
            if isinstance(value, (int, float)):
                judged.setdefault(metric, []).append(float(value))
    agent = report.get("agent") or ""
    return {
        "agent": agent, "model": report.get("model"), "generated_at": report.get("generated_at_utc"),
        "dry_run": report.get("dry_run"), "thresholds": report.get("thresholds") or {},
        "files": len(files), "checks": len(checks), "checks_passed": len(checks) - len(failed),
        "passed_checks": passed, "failed_checks": failed, "units": units, "units_flagged": flagged,
        "file_details": file_details,
        "judged_avg": {k: round(sum(v) / len(v), 3) for k, v in judged.items()},
        "metrics_used": sorted({m for f in files for m in f.get("metrics_used") or []}),
        "domain_metrics": _domain_metrics(agent, files),
        "execution_quality": _execution_quality(files),
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
        obs = _read(eval_dir / "observability.json")
        judge = obs.get("judge") if isinstance(obs, dict) else None
        agents = []
        for name in sorted(paths):
            report = _read(paths[name])
            if isinstance(report, dict) and report.get("files"):
                agents.append({**_agent_summary(report), "agent": report.get("agent") or name, "judge": judge})
        if agents:
            initial_input = _read(_artifact_runs_dir() / run_id / "inputs" / "initial_input.json") or {}
            business_context = initial_input.get("business_context")
            return {
                "run_id": run_id,
                "goal": initial_input.get("goal"),
                "use_case": (
                    business_context.get("use_case")
                    if isinstance(business_context, dict)
                    else initial_input.get("use_case")
                ),
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
