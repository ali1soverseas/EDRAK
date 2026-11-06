"""Run the competitor worker using the newest saved orchestrator task."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from edrak.agents.competitor_intelligence import run_research
from edrak.contracts.task import ResearchTask


def main() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    task_files = [
        repository_root / "artifacts" / "runs" / "d1bbe33c4e14297ff7d8196e7283edb3" / "competitor_intelligence_research_task.json"
    ]
    task_files = [p for p in task_files if p.exists()]
    if not task_files:
        raise FileNotFoundError(
            f"No saved competitor task found under {repository_root / 'artifacts' / 'runs'}"
        )

    task_path = max(task_files, key=lambda path: path.stat().st_mtime)
    task = ResearchTask.model_validate_json(task_path.read_text(encoding="utf-8"))
    print(f"Loaded competitor task from {task_path}")
    run_research(task, print_result=True)


if __name__ == "__main__":
    main()
