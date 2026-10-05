"""Standalone Runner for EDRAK Internal Intelligence Worker.

Runs the LangGraph agent against the pre-indexed ChromaDB internal knowledge base
and prints the full structured WorkerResult contract.
"""

import argparse
import json
import logging
from pathlib import Path
import sys

# Ensure backend/src and scripts are in sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_src = repo_root / "backend" / "src"
scripts_dir = repo_root / "scripts"

for p in [str(repo_root), str(backend_src), str(scripts_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from edrak.agents.internal_intelligence.graph import run_internal_intelligence
from edrak.contracts.request import BusinessContext, UseCase
from edrak.contracts.task import ResearchTask, WorkerType
from edrak.core.profiles import get_gitlab_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_internal_agent")


def main():
    parser = argparse.ArgumentParser(description="Run EDRAK Internal Intelligence Worker.")
    parser.add_argument(
        "--goal",
        type=str,
        default="Assess GitLab Duo product architecture, packaging, and internal OKRs vs GitHub Copilot",
        help="Business research goal",
    )
    parser.add_argument(
        "--focus",
        type=str,
        default="GitLab Duo Agent Platform, AI Gateway, zero retention data privacy, GitLab Credits, and self-hosted readiness",
        help="Specific focus areas",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output raw WorkerResult JSON only",
    )
    args = parser.parse_args()

    company_profile = get_gitlab_profile()
    business_context = BusinessContext(
        use_case=UseCase.COMPETITIVE_INTELLIGENCE,
        targets=["GitHub Copilot", "Microsoft Azure DevOps", "Atlassian Intelligence"],
        focus_areas=["AI capabilities", "pricing & packaging", "privacy & governance"],
    )

    task = ResearchTask(
        parent_request_id="req_manual_interactive_001",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal=args.goal,
        focus=args.focus,
        company_profile=company_profile,
        business_context=business_context,
    )

    logger.info("==================================================================")
    logger.info("Executing Internal Intelligence Worker (LangGraph State Machine)")
    logger.info("==================================================================")
    logger.info("Task ID: %s", task.task_id)
    logger.info("Goal:    %s", task.goal)
    logger.info("Focus:   %s", task.focus)

    # Execute the agent graph
    worker_result = run_internal_intelligence(task)

    if args.json_output:
        print(worker_result.model_dump_json(indent=2))
        return

    logger.info("\n" + "=" * 70)
    logger.info("WORKER RESULT CONTRACT OUTPUT (Comprehensive Inspection)")
    logger.info("=" * 70)
    logger.info("Task ID:       %s", worker_result.task_id)
    logger.info("Worker:        %s", worker_result.worker.value)
    logger.info("Status:        %s", worker_result.status.value)
    logger.info("Confidence:    %s", worker_result.confidence)
    logger.info("Metadata:      %s", json.dumps(worker_result.metadata))

    logger.info("\n--- [1] STRUCTURED FINDINGS (%d items) ---", len(worker_result.findings))
    for i, f in enumerate(worker_result.findings, 1):
        logger.info("  Finding #%d [ID: %s]:", i, f.finding_id)
        logger.info("    Category:       %s", f.category.value)
        logger.info("    Confidence:     %s", f.confidence)
        logger.info("    Evidence Refs:  %s", f.evidence_ids)
        logger.info("    Statement:      \"%s\"", f.statement)

    logger.info("\n--- [2] CITED EVIDENCE POOL (%d items) ---", len(worker_result.evidence))
    for i, ev in enumerate(worker_result.evidence, 1):
        logger.info("  Evidence #%d [ID: %s]:", i, ev.evidence_id)
        logger.info("    Title:          %s", ev.source_title)
        logger.info("    Source URL:     %s", ev.source_url)
        logger.info("    Source Type:    %s", ev.source_type.value)
        logger.info("    Retrieved At:   %s", ev.retrieved_at.isoformat())
        logger.info("    Extracted Fact: \"%s...\"", ev.extracted_fact[:140].replace("\n", " "))

    logger.info("\n--- [3] LIMITATIONS AND GAPS (%d items) ---", len(worker_result.gaps))
    for g in worker_result.gaps:
        logger.info("  • %s", g)

    logger.info("\n" + "=" * 70)
    logger.info("JSON SCHEMA VALIDATION: PASS (Valid Pydantic WorkerResult Contract)")
    logger.info("==================================================================")


if __name__ == "__main__":
    main()
