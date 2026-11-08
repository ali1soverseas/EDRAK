"""Run the market worker on the golden-dataset NVIDIA brief and save the WorkerResult."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

from edrak.agents.market_intelligence.graph import build_graph, format_task_context, worker_result_from_state
from edrak.agents.market_intelligence.state import empty_market_state
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    CompetitorRef,
    ResearchTask,
    UseCase,
    WorkerType,
    new_id,
    utcnow,
)
from edrak.core.config import settings

GOAL = (
    "What is the market momentum for AI computing accelerators in the data center "
    "sector across Europe and the United States?"
)
NOTES = (
    "NVIDIA develops GPUs, AI computing platforms, accelerated computing infrastructure, "
    "networking technologies, and software used in data centers, AI development, gaming, "
    "and professional visualization."
)
FOCUS = (
    "Market size, growth rates, adoption trends, regulatory influences, and technology "
    "signals affecting AI accelerator demand."
)


def main() -> None:
    task = ResearchTask(
        parent_request_id=new_id(),
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal=GOAL,
        focus=FOCUS,
        company_profile=CompanyProfile(
            name="NVIDIA Corporation",
            aliases=["NVIDIA", "Nvidia"],
            products=["AI computing", "Data center platforms"],
            notes=NOTES,
        ),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            competitors=[CompetitorRef(name="Broadcom"), CompetitorRef(name="Intel Corp")],
            targets=["Broadcom", "Intel Corp"],
            markets=["Europe", "United States"],
            offering="AI computing",
            target_customers="Cloud service providers, AI developers",
            focus_areas=[
                "market size",
                "growth rates",
                "adoption trends",
                "regulatory influences",
                "technology signals",
            ],
        ),
    )
    context_text = format_task_context(task)
    print(f"LLM: {settings.OLLAMA_MODEL}", flush=True)
    print(f"GOAL: {task.goal}", flush=True)
    print("CONTEXT:", flush=True)
    print(context_text, flush=True)

    started_at = utcnow()
    app = build_graph()
    state = app.invoke(
        empty_market_state(
            run_id=task.parent_request_id,
            goal=task.goal,
            business_context=context_text,
        )
    )
    result = worker_result_from_state(task, state, started_at=started_at)
    reports_dir = ROOT / settings.ARTIFACTS_PATH / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"market_optm_golden_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.json"
    payload = result.model_dump(mode="json")
    payload["private_report"] = state.get("final_report")
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"SAVED {out_path}", flush=True)
    print(
        f"status={result.status} findings={len(result.findings)} "
        f"evidence={len(result.evidence)} gaps={len(result.gaps)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
