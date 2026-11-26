import json

from edrak.orchestration.stage_artifacts import save_stage_artifacts


def test_stage_artifacts_are_written_inside_the_run_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_ARTIFACTS_DIR", str(tmp_path / "runs"))

    save_stage_artifacts(
        "request-123",
        "cross_signal",
        {"verified_findings": [{"finding_id": "f1"}]},
        {"status": "completed", "signals": []},
    )

    stage_dir = tmp_path / "runs" / "request-123" / "stages" / "cross_signal"
    assert json.loads((stage_dir / "input.json").read_text(encoding="utf-8")) == {
        "verified_findings": [{"finding_id": "f1"}]
    }
    assert json.loads((stage_dir / "output.json").read_text(encoding="utf-8")) == {
        "status": "completed",
        "signals": [],
    }


def test_stage_artifacts_overwrite_with_latest_stage_result(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_ARTIFACTS_DIR", str(tmp_path / "runs"))
    save_stage_artifacts("request-123", "verification", {"pass": 1}, {"status": "retry"})

    save_stage_artifacts("request-123", "verification", {"pass": 2}, {"status": "verified"})

    stage_dir = tmp_path / "runs" / "request-123" / "stages" / "verification"
    assert json.loads((stage_dir / "input.json").read_text(encoding="utf-8")) == {"pass": 2}
    assert json.loads((stage_dir / "output.json").read_text(encoding="utf-8")) == {"status": "verified"}
