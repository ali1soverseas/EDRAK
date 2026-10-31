import json
from pathlib import Path
from runpy import run_path

_TRACE_MODULE = run_path(
    Path(__file__).parents[1]
    / "src"
    / "edrak"
    / "DecisionAnalysis"
    / "tracing.py"
)
trace_decision_analysis = _TRACE_MODULE["trace_decision_analysis"]


def test_decision_analysis_trace_uses_separate_file_in_run_folder(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EDRAK_DECISION_ANALYSIS_TRACE_JSON", raising=False)
    monkeypatch.delenv("EDRAK_ORCHESTRATOR_PLANNING_JSON", raising=False)

    run_dir = tmp_path / "artifacts" / "runs" / "research-run"
    run_dir.mkdir(parents=True)
    planning_path = run_dir / "orchestrator_planning.json"
    planning_path.write_text('{"plan": {"status": "ready"}}\n', encoding="utf-8")

    trace_decision_analysis(
        "prepare_context.complete",
        research_run_id="research-run",
        signal_count=2,
    )

    trace_path = run_dir / "decision_analysis_tracing.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["trace_events"][0]["stage"] == "prepare_context.complete"
    assert trace["trace_events"][0]["payload"] == {"signal_count": 2}
    assert trace["decision_analysis_trace"]["history"] == trace["trace_events"]
    assert json.loads(planning_path.read_text(encoding="utf-8")) == {
        "plan": {"status": "ready"}
    }


def test_decision_analysis_trace_override_writes_configured_file(
    monkeypatch, tmp_path
) -> None:
    trace_path = tmp_path / "custom" / "decision-trace.json"
    monkeypatch.setenv("EDRAK_DECISION_ANALYSIS_TRACE_JSON", str(trace_path))

    trace_decision_analysis("analyze.complete", research_run_id="research-run")

    assert json.loads(trace_path.read_text(encoding="utf-8"))["trace_events"][0][
        "stage"
    ] == "analyze.complete"
