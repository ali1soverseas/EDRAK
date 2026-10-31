import json
from pathlib import Path
from runpy import run_path

_TRACE_MODULE = run_path(
    Path(__file__).parents[1]
    / "src"
    / "edrak"
    / "CrossSignal"
    / "tracing.py"
)
trace_cross_signal = _TRACE_MODULE["trace_cross_signal"]


def test_cross_signal_trace_uses_separate_file_in_run_folder(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EDRAK_CROSS_SIGNAL_TRACE_JSON", raising=False)
    monkeypatch.delenv("EDRAK_ORCHESTRATOR_PLANNING_JSON", raising=False)

    run_dir = tmp_path / "artifacts" / "runs" / "research-run"
    run_dir.mkdir(parents=True)
    planning_path = run_dir / "orchestrator_planning.json"
    planning_path.write_text('{"plan": {"status": "ready"}}\n', encoding="utf-8")

    trace_cross_signal(
        "assemble.complete",
        {"research_run_id": "research-run"},
        signal_count=2,
        output={"research_run_id": "research-run", "status": "completed"},
    )

    trace_path = run_dir / "cross_signal_tracing.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["trace_events"][0]["stage"] == "assemble.complete"
    assert trace["trace_events"][0]["payload"] == {
        "signal_count": 2,
        "output": {"research_run_id": "research-run", "status": "completed"},
    }
    assert trace["cross_signal_trace"]["history"] == trace["trace_events"]
    assert json.loads(planning_path.read_text(encoding="utf-8")) == {
        "plan": {"status": "ready"}
    }


def test_cross_signal_trace_override_writes_configured_file(
    monkeypatch, tmp_path
) -> None:
    trace_path = tmp_path / "custom" / "cross-signal.json"
    monkeypatch.setenv("EDRAK_CROSS_SIGNAL_TRACE_JSON", str(trace_path))

    trace_cross_signal("assemble.complete", {"research_run_id": "research-run"})

    assert json.loads(trace_path.read_text(encoding="utf-8"))["trace_events"][0][
        "stage"
    ] == "assemble.complete"


def test_planning_file_override_selects_sibling_cross_signal_trace(
    monkeypatch, tmp_path
) -> None:
    planning_path = tmp_path / "run-1" / "orchestrator_planning.json"
    monkeypatch.delenv("EDRAK_CROSS_SIGNAL_TRACE_JSON", raising=False)
    monkeypatch.setenv("EDRAK_ORCHESTRATOR_PLANNING_JSON", str(planning_path))

    trace_cross_signal("prepare.complete")

    trace_path = planning_path.with_name("cross_signal_tracing.json")
    assert trace_path.is_file()
    assert json.loads(trace_path.read_text(encoding="utf-8"))["trace_events"][0][
        "stage"
    ] == "prepare.complete"
