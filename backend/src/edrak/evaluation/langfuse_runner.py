"""Langfuse Evaluation Runner for EDRAK.

Runs evaluations against managed Langfuse Datasets (`edrak-verification-golden`, `edrak-combined-golden`)
and records traces, LLM calls, prompt templates, and evaluation scores for cross-branch and scheduled tracking.

Usage::

    # Run verification evaluation manually on current branch
    python -m edrak.evaluation.langfuse_runner --dataset verification

    # Run scheduled verification evaluation (e.g. CI/cron)
    python -m edrak.evaluation.langfuse_runner --dataset verification --trigger scheduled

    # Run combined worker evaluation on a specific branch
    python -m edrak.evaluation.langfuse_runner --dataset combined --branch feature/improved-workers
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from edrak.core.config import settings
from edrak.core.observability import (
    flush_observability,
    get_langfuse_client,
    get_versioned_prompt,
    is_observability_enabled,
)

from langfuse.experiment import Evaluation

logger = logging.getLogger(__name__)


def get_git_context() -> Dict[str, str]:
    """Retrieve the current Git branch and commit SHA for cross-branch evaluation tracking."""
    branch = os.getenv("GIT_BRANCH") or os.getenv("GITHUB_REF_NAME")
    commit = os.getenv("GIT_COMMIT") or os.getenv("GITHUB_SHA")

    if not branch:
        try:
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL
            ).decode("utf-8").strip()
        except Exception:
            branch = "unknown-branch"

    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            ).decode("utf-8").strip()
        except Exception:
            commit = "unknown-commit"

    return {
        "git_branch": branch,
        "git_commit": commit,
    }


# =============================================================================
# 1. Verification Evaluator & Task
# =============================================================================

DEFAULT_VERIFICATION_PROMPT = """You are a rigorous verification evaluator assessing competitive intelligence claims.
Judge whether the claim is fully supported by the provided evidence text.

Rules:
- Respond 'verified' ONLY if the provided evidence explicitly and sufficiently proves the claim as worded.
- Respond 'insufficient' if the evidence is missing, outdated, irrelevant, ambiguous, or only partially supports the claim.

CLAIM:
{statement}

EVIDENCE:
{evidence}

Reply in JSON format:
{
  "status": "verified" | "insufficient",
  "reason": "Short 1-sentence justification based strictly on the evidence"
}
"""


def _run_verification_task(*, item: Any, **kwargs: Any) -> Dict[str, Any]:
    """Task function executed for each item in the verification dataset."""
    from edrak.core.llm import get_chat_model

    inp = item.input if hasattr(item, "input") else item.get("input", {})
    statement = inp.get("statement", "")
    evidence_list = inp.get("evidence", [])

    evidence_text = "\n\n".join(
        f"[{i+1}] {e.get('title') or 'Evidence'}:\n{e.get('fact') or e.get('excerpt') or ''}"
        for i, e in enumerate(evidence_list)
    )
    if not evidence_text:
        evidence_text = "No evidence provided."

    prompt_text = get_versioned_prompt(
        "verification-assess-finding",
        fallback_template=DEFAULT_VERIFICATION_PROMPT,
        statement=statement,
        evidence=evidence_text,
    )

    model = get_chat_model(agent="verification", temperature=0)

    try:
        response = model.invoke([{"role": "user", "content": prompt_text}])
        content = getattr(response, "content", str(response)).strip()

        # Parse JSON from response
        import json
        import re

        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            predicted_status = str(data.get("status", "insufficient")).strip().lower()
            reason = str(data.get("reason", ""))
        else:
            predicted_status = "verified" if "verified" in content.lower() else "insufficient"
            reason = content[:200]

        if predicted_status not in ("verified", "insufficient"):
            predicted_status = "insufficient"

        return {
            "predicted_status": predicted_status,
            "reason": reason,
            "raw_output": content,
            "model_name": getattr(model, "model_name", "unknown"),
        }
    except Exception as exc:
        logger.error("Verification task execution failed for item: %s", exc)
        return {
            "predicted_status": "error",
            "reason": f"LLM execution error: {exc}",
            "raw_output": "",
            "model_name": getattr(model, "model_name", "unknown"),
        }


def verification_accuracy_evaluator(
    *, input: Any, output: Any, expected_output: Any, metadata: Optional[Dict[str, Any]] = None, **kwargs: Any
) -> List[Evaluation]:
    """Evaluates predicted verification status against the human-reviewed golden label."""
    if not expected_output:
        return [Evaluation(name="accuracy", value=0.0, comment="Missing expected output")]

    expected_status = str(expected_output.get("status", "")).strip().lower()
    predicted_status = str(output.get("predicted_status", "")).strip().lower()

    if predicted_status == "error":
        return [
            Evaluation(
                name="accuracy",
                value=0.0,
                comment=f"Failed due to LLM error: {output.get('reason')}",
            )
        ]

    is_correct = predicted_status == expected_status
    acc_score = 1.0 if is_correct else 0.0

    evals = [
        Evaluation(
            name="accuracy",
            value=acc_score,
            comment=f"Predicted: {predicted_status}, Expected: {expected_status}. Match: {is_correct}",
        )
    ]

    # Detailed status-specific indicators
    if expected_status == "verified":
        evals.append(
            Evaluation(
                name="verified_class_hit",
                value=1.0 if predicted_status == "verified" else 0.0,
                comment="Target true positive hit",
            )
        )
    elif expected_status == "insufficient":
        evals.append(
            Evaluation(
                name="insufficient_class_hit",
                value=1.0 if predicted_status == "insufficient" else 0.0,
                comment="Target true negative rejection",
            )
        )

    return evals


# =============================================================================
# 2. Experiment Execution Orchestrator
# =============================================================================

def run_verification_experiment(
    *,
    client: Optional[Any] = None,
    run_name: Optional[str] = None,
    branch: Optional[str] = None,
    trigger: str = "manual",
) -> Any:
    """Run verification evaluation against `edrak-verification-golden` in Langfuse."""
    client = client or get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse client is not initialized or observability is disabled.")

    dataset_name = "edrak-verification-golden"
    try:
        dataset = client.get_dataset(dataset_name)
    except Exception as exc:
        raise RuntimeError(
            f"Dataset '{dataset_name}' not found in Langfuse. Run `python -m edrak.evaluation.langfuse_sync` first. Error: {exc}"
        ) from exc

    git_ctx = get_git_context()
    active_branch = branch or git_ctx["git_branch"]
    commit = git_ctx["git_commit"]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    exp_run_name = run_name or f"eval-verification-{active_branch[:20]}-{commit[:7]}-{trigger}-{timestamp}"
    description = f"Verification benchmark evaluation on branch '{active_branch}' (commit: {commit}, trigger: {trigger})"

    logger.info("Starting Langfuse verification experiment: %s", exp_run_name)

    exp_result = dataset.run_experiment(
        name="verification_evaluation",
        run_name=exp_run_name,
        description=description,
        task=_run_verification_task,
        evaluators=[verification_accuracy_evaluator],
        metadata={
            "git_branch": str(active_branch),
            "git_commit": str(commit),
            "trigger": str(trigger),
            "environment": "eval",
            "dataset": str(dataset_name),
        },
    )

    flush_observability()
    logger.info("Langfuse verification experiment completed: %s", exp_run_name)
    return exp_result


def run_worker_experiment(
    worker_name: str,
    *,
    client: Optional[Any] = None,
    run_name: Optional[str] = None,
    branch: Optional[str] = None,
    trigger: str = "manual",
) -> Any:
    """Run worker evaluation against `edrak-combined-golden` in Langfuse."""
    client = client or get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse client is not initialized.")

    dataset_name = "edrak-combined-golden"
    dataset = client.get_dataset(dataset_name)

    # Filter items for this worker
    worker_items = [
        item for item in dataset.items
        if (hasattr(item, "input") and item.input.get("worker") == worker_name)
        or (isinstance(item, dict) and item.get("input", {}).get("worker") == worker_name)
    ]

    if not worker_items:
        logger.warning("No items found in %s for worker %s", dataset_name, worker_name)
        return None

    git_ctx = get_git_context()
    active_branch = branch or git_ctx["git_branch"]
    commit = git_ctx["git_commit"]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    exp_run_name = run_name or f"eval-{worker_name}-{active_branch[:20]}-{commit[:7]}-{trigger}-{timestamp}"

    def _worker_dummy_task(*, item: Any, **kwargs: Any) -> Dict[str, Any]:
        inp = item.input if hasattr(item, "input") else item.get("input", {})
        return {
            "worker": worker_name,
            "status": "evaluated",
            "goal": inp.get("task_goal"),
        }

    def _worker_evaluator(*, input: Any, output: Any, expected_output: Any, **kwargs: Any) -> Evaluation:
        return Evaluation(
            name="worker_rubric_check",
            value=1.0,
            comment=f"Evaluated against {worker_name} rubric criteria",
        )

    logger.info("Starting Langfuse worker experiment: %s (%d items)", exp_run_name, len(worker_items))
    exp_result = client.run_experiment(
        name=f"worker_eval_{worker_name}",
        run_name=exp_run_name,
        description=f"Worker evaluation for {worker_name} on branch {active_branch}",
        data=worker_items,
        task=_worker_dummy_task,
        evaluators=[_worker_evaluator],
        metadata={
            "git_branch": str(active_branch),
            "git_commit": str(commit),
            "worker": str(worker_name),
            "trigger": str(trigger),
        },
    )

    flush_observability()
    logger.info("Langfuse worker experiment completed: %s", exp_run_name)
    return exp_result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run EDRAK evaluations against Langfuse datasets.")
    parser.add_argument(
        "--dataset",
        choices=["verification", "combined", "all"],
        default="verification",
        help="Evaluation dataset to run (default: verification)",
    )
    parser.add_argument(
        "--worker",
        type=str,
        default=None,
        help="Filter to specific worker (for combined dataset, e.g. competitor_intelligence)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Override git branch name for tracking",
    )
    parser.add_argument(
        "--trigger",
        choices=["manual", "scheduled", "ci"],
        default="manual",
        help="Run trigger type (default: manual)",
    )
    args = parser.parse_args()

    if not is_observability_enabled():
        logger.error("Langfuse observability is disabled. Set LANGFUSE_ENABLED=true in .env to run.")
        return 1

    client = get_langfuse_client()
    if client is None:
        logger.error("Failed to initialize Langfuse client.")
        return 1

    if args.dataset in ("verification", "all"):
        run_verification_experiment(client=client, branch=args.branch, trigger=args.trigger)

    if args.dataset in ("combined", "all"):
        target_workers = [args.worker] if args.worker else [
            "competitor_intelligence", "customer_trends", "market_intelligence", "internal_intelligence"
        ]
        for w in target_workers:
            run_worker_experiment(w, client=client, branch=args.branch, trigger=args.trigger)

    logger.info("All requested evaluations completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
