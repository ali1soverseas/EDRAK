from types import SimpleNamespace

from edrak.orchestration.artifacts import save_task_artifacts


def test_save_task_artifacts_writes_evaluator_input_output_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = SimpleNamespace(
        parent_request_id="request-1",
        worker=SimpleNamespace(value="market_intelligence"),
        task_id="task-1",
        attempt=2,
        model_dump=lambda mode: {"task_id": "task-1", "goal": "research"},
    )
    result = SimpleNamespace(
        model_dump=lambda mode: {"task_id": "task-1", "status": "completed"}
    )

    save_task_artifacts(task, result)

    task_dir = (
        tmp_path
        / "artifacts"
        / "runs"
        / "request-1"
        / "market_intelligence"
        / "task-1.a2"
    )
    assert (task_dir / "input.json").read_text(encoding="utf-8") == (
        '{\n  "task_id": "task-1",\n  "goal": "research"\n}'
    )
    assert (task_dir / "output.json").read_text(encoding="utf-8") == (
        '{\n  "task_id": "task-1",\n  "status": "completed"\n}'
    )