import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from edrak.agents.customer_trends import cli
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.settings import get_settings
from edrak.agents.customer_trends.store.sink import LocalSink
from tests.customer_trends.factories import BRIEFS

CI_BRIEF = BRIEFS / "competitive_intelligence.json"
RUN_ID = "run-ci-gitlab-001"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Data and artifacts in a temporary directory, the way a developer's .env would set them."""
    monkeypatch.setenv("EDRAK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTIFACTS_PATH", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    return tmp_path


def call(*args: str) -> int:
    get_settings.cache_clear()
    return cli.main(list(args))


def demo(*extra: str, brief: Path = CI_BRIEF) -> int:
    return call("run", "--brief", str(brief), "--fixture", "--fake-llm", *extra)


def test_a_demo_run_streams_one_line_per_event_and_writes_the_result(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = home / "result.json"
    assert demo("--out", str(out_path)) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert "node_started     intake" in lines[0] and lines[0][:2].isdigit()
    assert any("tool_called" in line and "social_search" in line for line in lines)
    assert any("gap_found" in line for line in lines) is False
    assert sum("finding_accepted" in line for line in lines) == 4
    finished = next(line for line in lines if "run_finished" in line)
    assert "status=complete" in finished and "findings=4" in finished
    assert f"run {RUN_ID}: complete, confidence medium" in captured.out
    assert f"result: {out_path}" in captured.out
    result = CustomerTrendsResult.model_validate_json(out_path.read_text(encoding="utf-8"))
    assert result.run_id == RUN_ID and len(result.findings) == 4
    assert "provider_call" not in captured.out and "structured_call" not in captured.err, (
        "the structured log stays out of the way unless --verbose is given"
    )


def test_verbose_shows_the_structured_log_on_stderr(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert call("--verbose", "run", "--brief", str(CI_BRIEF), "--fixture", "--fake-llm") == 0
    captured = capsys.readouterr()
    assert "provider_call" in captured.err and "provider_call" not in captured.out


def test_runs_lists_the_past_run_and_show_prints_its_summary_or_its_json(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert call("runs") == 0
    assert "no runs yet" in capsys.readouterr().out
    assert demo("--out", str(home / "r.json")) == 0
    capsys.readouterr()

    assert call("runs") == 0
    listing = capsys.readouterr().out
    assert (
        listing.startswith(RUN_ID)
        and "findings=4" in listing
        and listing.rstrip().endswith("result")
    )

    assert call("show", "--run-id", RUN_ID) == 0
    shown = capsys.readouterr().out
    assert shown.splitlines()[0] == f"run {RUN_ID}: complete, confidence medium"
    assert "  f1 [pain_point, medium]" in shown and "result: " in shown

    assert call("show", "--run-id", RUN_ID, "--json") == 0
    assert json.loads(capsys.readouterr().out) == json.loads((home / "r.json").read_text("utf-8"))


def test_an_insufficient_result_exits_with_two(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = home / "no-fixtures"
    empty.mkdir()
    monkeypatch.setenv("EDRAK_FIXTURES_DIR", str(empty))
    assert demo() == 2
    out = capsys.readouterr().out
    assert "insufficient" in out and "gap: only 0 evidence items" in out
    assert "run_finished" in out and "run_failed" not in out


def test_a_crashing_run_exits_with_one(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(self: LocalSink, result: CustomerTrendsResult) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(LocalSink, "write_result", broken)
    assert demo() == 1
    captured = capsys.readouterr()
    assert "run_failed       OSError" in captured.out
    assert "the run left no result" in captured.err


def test_a_brief_that_cannot_be_used_exits_with_one_and_says_why(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo(brief=home / "missing.json") == 1
    assert "cannot read the brief" in capsys.readouterr().err
    bad = home / "bad.json"
    bad.write_text(json.dumps({"task_id": "t", "run_id": "r", "use_case": "nope", "depth": 3}))
    assert demo(brief=bad) == 1
    err = capsys.readouterr().err
    assert f"the brief is not valid ({bad}):" in err
    assert "use_case:" in err and "entity: Field required" in err


def test_a_live_run_without_the_model_key_says_what_to_do(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert call("run", "--brief", str(CI_BRIEF), "--fixture") == 1
    err = capsys.readouterr().err
    assert "OLLAMA_API_KEY is not set" in err and "--fake-llm" in err


def test_show_for_an_unknown_run_exits_with_one(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert call("show", "--run-id", "run-nope") == 1
    assert "no result for run run-nope" in capsys.readouterr().err


def test_the_console_script_is_declared() -> None:
    pyproject = tomllib.loads((BRIEFS.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["customer-trends"] == (
        "edrak.agents.customer_trends.cli:main"
    )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {"type": "node_started", "node": "plan_queries", "ts": "2026-10-07T01:02:03.456+00:00"},
            "01:02:03.456 node_started     plan_queries",
        ),
        (
            {
                "type": "node_finished",
                "node": "join",
                "status": "ok",
                "duration_ms": 7,
                "ts": "x" * 11 + "01:02:03.456",
            },
            "01:02:03.456 node_finished    join ok 7ms",
        ),
        (
            {
                "type": "tool_called",
                "tool": "social_search",
                "branch": "social",
                "provider": "apify",
                "fallback_used": True,
                "status": "partial",
                "count": 12,
                "latency_ms": 840,
                "cost": 0.0123,
                "error_code": None,
                "ts": "x" * 11 + "00:00:00.001",
            },
            "00:00:00.001 tool_called      social_search branch=social provider=apify "
            "fallback=yes status=partial count=12 840ms cost=0.0123",
        ),
        (
            {
                "type": "tool_called",
                "tool": "evidence_query",
                "branch": None,
                "provider": None,
                "status": "error",
                "count": 0,
                "latency_ms": 1,
                "cost": 0.0,
                "error_code": "invalid_input",
                "ts": "x" * 11 + "00:00:00.002",
            },
            "00:00:00.002 tool_called      evidence_query branch=- provider=- fallback=no "
            "status=error count=0 1ms cost=0.0000 error=invalid_input",
        ),
        (
            {
                "type": "gap_found",
                "gap_id": "platforms",
                "severity": "critical",
                "description": "d",
                "ts": "x" * 11 + "00:00:00.003",
            },
            "00:00:00.003 gap_found        platforms critical d",
        ),
        (
            {
                "type": "replan",
                "replan_count": 1,
                "gap_ids": ["a", "b"],
                "ts": "x" * 11 + "00:00:00.004",
            },
            "00:00:00.004 replan           pass=1 closing: a, b",
        ),
        (
            {
                "type": "finding_rejected",
                "finding_id": "f2",
                "reasons": ["r1", "r2"],
                "ts": "x" * 11 + "00:00:00.005",
            },
            "00:00:00.005 finding_rejected f2 r1 r2",
        ),
        (
            {
                "type": "run_failed",
                "error": "OSError",
                "message": "disk full",
                "ts": "x" * 11 + "00:00:00.006",
            },
            "00:00:00.006 run_failed       OSError disk full",
        ),
        ({"type": "something_new"}, "something_new"),
    ],
)
def test_each_event_kind_has_a_readable_line(event: dict[str, Any], expected: str) -> None:
    assert cli.format_event(event) == expected
