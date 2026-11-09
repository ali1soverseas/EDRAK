"""The evaluation trace: written only on request, and readable by the evaluation suite."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from edrak.agents.customer_trends import tracing
from edrak.agents.customer_trends.runner import run_task
from edrak.agents.customer_trends.tracing import trace_path
from tests.customer_trends.factories import load_brief
from tests.customer_trends.graph_helpers import (
    PLAN,
    models,
    scenario_settings,
    world_registry,
)

EVALUATOR = Path(__file__).resolve().parents[4] / "Evaluation" / "generic" / "evaluator.py"


def load_evaluator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("edrak_evaluator", EVALUATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_run_writes_no_trace_unless_asked(tmp_path: Path) -> None:
    brief = load_brief("competitive_intelligence")
    settings = scenario_settings(tmp_path)
    scripted = models(plans=[PLAN])
    await run_task(
        brief, providers=world_registry(None, None), llm=scripted.factory, settings=settings
    )
    assert not trace_path(settings.artifacts_dir, brief.run_id).exists()


async def test_the_trace_is_what_the_evaluator_reads(tmp_path: Path) -> None:
    brief = load_brief("competitive_intelligence")
    settings = scenario_settings(tmp_path).model_copy(update={"eval_trace_enabled": True})
    scripted = models(plans=[PLAN])
    result = await run_task(
        brief, providers=world_registry(None, None), llm=scripted.factory, settings=settings
    )

    path = trace_path(settings.artifacts_dir, brief.run_id)
    trace = json.loads(path.read_text(encoding="utf-8"))
    assert trace["worker"] == "customer_trends" and trace["task_goal"] == brief.question

    evaluator = load_evaluator()
    assert evaluator.detect_worker(trace) == "customer_trends"
    report, evidence = evaluator.extract_report_and_evidence(trace)
    assert report["task_goal"] == brief.question
    assert [f["claim"] for f in report["findings"]] == [f.claim for f in result.findings]
    assert report["summary"] == result.control_summary.headline
    assert report["status"] == result.control_summary.status
    stored = [e for e in evidence if e["source"].split(" | ")[0] != "computed_metric"]
    computed = [e for e in evidence if e["source"].split(" | ")[0] == "computed_metric"]
    assert len(stored) == result.control_summary.evidence_count
    # the computed metrics the findings cite are evidence too, and the judge reads them first
    assert computed and evidence[: len(computed)] == computed
    assert "themes" in {e["id"] for e in computed}
    # then the stored evidence the findings cite, because the judge reads a bounded amount
    cited = list(dict.fromkeys(i for f in result.findings for i in f.evidence_ids))
    assert [e["id"] for e in stored[: len(cited)]] == cited


async def test_a_trace_that_cannot_be_built_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> dict[str, object]:
        raise RuntimeError("no trace today")

    monkeypatch.setattr(tracing, "build_trace", broken)
    brief = load_brief("competitive_intelligence")
    settings = scenario_settings(tmp_path).model_copy(update={"eval_trace_enabled": True})
    scripted = models(plans=[PLAN])
    result = await run_task(
        brief, providers=world_registry(None, None), llm=scripted.factory, settings=settings
    )
    assert result.findings
    assert not trace_path(settings.artifacts_dir, brief.run_id).exists()
