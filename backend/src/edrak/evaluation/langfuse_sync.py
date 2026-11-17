"""Sync local golden evaluation datasets to Langfuse Datasets catalog.

Allows managing, updating, and viewing golden datasets directly in the Langfuse UI:
- `edrak-verification-golden`: 19 curated findings with ground truth labels.
- `edrak-combined-golden`: Full multi-worker and multi-stage evaluation benchmark.

Usage::

    # Run directly from CLI
    python -m edrak.evaluation.langfuse_sync --dataset verification
    python -m edrak.evaluation.langfuse_sync --dataset all
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from edrak.core.observability import get_langfuse_client, is_observability_enabled

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
VERIFICATION_GOLDEN_PATH = REPO_ROOT / "Evaluation" / "verification_evaluation" / "verification_golden.json"
COMBINED_GOLDEN_PATH = REPO_ROOT / "Evaluation" / "golden" / "combined_golden_dataset.json"


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Golden dataset file not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sync_verification_golden(client: Optional[Any] = None) -> int:
    """Sync `verification_golden.json` into Langfuse dataset `edrak-verification-golden`."""
    client = client or get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse client is not initialized or observability is disabled.")

    data = _load_json(VERIFICATION_GOLDEN_PATH)
    cases = data.get("test_cases") or data.get("cases", [])
    if not cases:
        logger.warning("No test cases found in verification_golden.json")
        return 0

    dataset_name = "edrak-verification-golden"
    description = (
        "Human-reviewed benchmark of 19 findings evaluated for factual accuracy, "
        "evidence grounding, and verification status (verified vs insufficient)."
    )

    try:
        client.create_dataset(
            name=dataset_name,
            description=description,
            metadata={"source": "Evaluation/verification_evaluation/verification_golden.json", "version": "1.0"},
        )
        logger.info("Created Langfuse dataset: %s", dataset_name)
    except Exception as exc:
        logger.debug("Dataset %s already exists or create returned: %s", dataset_name, exc)

    synced_count = 0
    for case in cases:
        case_id = case.get("test_case_id") or case.get("finding_id")
        finding_id = case.get("finding_id", case_id)
        inp = case.get("input", {})
        statement = inp.get("finding_statement") or case.get("statement", "")
        evidence_list = inp.get("evidence") or case.get("evidence", [])
        golden = case.get("golden_reference", {})
        expected_status = golden.get("verification_status") or case.get("expected_status") or case.get("status")

        item_input = {
            "finding_id": finding_id,
            "test_case_id": case.get("test_case_id"),
            "statement": statement,
            "category": inp.get("finding_category") or case.get("category"),
            "evidence": [
                {
                    "evidence_id": e.get("evidence_id"),
                    "title": e.get("source_title"),
                    "fact": e.get("extracted_fact"),
                    "excerpt": e.get("excerpt"),
                    "url": e.get("source_url"),
                    "relation": e.get("relation", "supports"),
                }
                for e in evidence_list
            ],
            "scope_notes": case.get("scope_notes"),
        }

        item_expected = {
            "status": expected_status,
            "supporting_evidence_ids": golden.get("supporting_evidence_ids", []),
            "reasons": golden.get("reasons", []),
            "required_evidence_quality": golden.get("required_evidence_quality", "medium"),
        }

        item_metadata = {
            "test_case_id": case.get("test_case_id"),
            "finding_id": finding_id,
            "worker": case.get("worker", "verification"),
            "actual_verification_baseline": case.get("actual_verification", {}).get("verification_status"),
        }

        try:
            client.create_dataset_item(
                dataset_name=dataset_name,
                id=case_id,
                input=item_input,
                expected_output=item_expected,
                metadata=item_metadata,
            )
            synced_count += 1
        except Exception as exc:
            logger.warning("Failed to sync item %s to dataset %s: %s", case_id, dataset_name, exc)

    logger.info("Successfully synced %d items to Langfuse dataset %s", synced_count, dataset_name)
    return synced_count


def sync_combined_golden(client: Optional[Any] = None) -> int:
    """Sync `combined_golden_dataset.json` into Langfuse dataset `edrak-combined-golden`."""
    client = client or get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse client is not initialized or observability is disabled.")

    data = _load_json(COMBINED_GOLDEN_PATH)
    dataset_name = "edrak-combined-golden"
    description = (
        "Consolidated multi-worker and multi-stage evaluation benchmark for EDRAK: "
        "Competitor Intelligence, Customer Trends, Market Intelligence, Internal Intelligence, "
        "Verification, Cross-Signal, and Decision Analysis."
    )

    try:
        client.create_dataset(
            name=dataset_name,
            description=description,
            metadata={"source": "Evaluation/golden/combined_golden_dataset.json", "version": "1.3.0"},
        )
        logger.info("Created Langfuse dataset: %s", dataset_name)
    except Exception as exc:
        logger.debug("Dataset %s already exists or create returned: %s", dataset_name, exc)

    synced_count = 0

    # 1. Worker Datasets
    worker_datasets = data.get("worker_datasets", {})
    for worker_name, worker_info in worker_datasets.items():
        test_cases = worker_info.get("test_cases", [])
        for case in test_cases:
            case_id = f"{worker_name}-{case.get('id') or case.get('test_case_id') or case.get('requirement_id')}"
            item_input = {
                "worker": worker_name,
                "requirement_id": case.get("requirement_id"),
                "competitor": case.get("competitor"),
                "category": case.get("category"),
                "task_goal": worker_info.get("worker_definition", {}).get("goal"),
                "focus": worker_info.get("worker_definition", {}).get("focus"),
                "input_context": case.get("input", {}),
            }
            item_expected = {
                "requirement": case.get("requirement"),
                "expected_findings": case.get("expected_findings", []),
                "acceptable_alternatives": case.get("acceptable_alternatives", []),
                "rubric": case.get("rubric", {}),
            }
            item_metadata = {
                "worker": worker_name,
                "dataset_version": worker_info.get("dataset_version"),
            }

            try:
                client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=case_id,
                    input=item_input,
                    expected_output=item_expected,
                    metadata=item_metadata,
                )
                synced_count += 1
            except Exception as exc:
                logger.warning("Failed to sync item %s: %s", case_id, exc)

    # 2. Stage Datasets (cross_signal, decision_analysis)
    for stage_name in ("cross_signal", "decision_analysis"):
        stage_info = data.get(stage_name, {})
        test_cases = stage_info.get("test_cases", [])
        for case in test_cases:
            case_id = f"{stage_name}-{case.get('id') or case.get('test_case_id')}"
            item_input = {
                "stage": stage_name,
                "input_findings": case.get("input_findings", []),
                "context": case.get("context", {}),
            }
            item_expected = {
                "expected_signals": case.get("expected_signals", []),
                "expected_options": case.get("expected_options", []),
                "rubric": case.get("rubric", {}),
            }
            item_metadata = {
                "stage": stage_name,
                "domain": case.get("domain", "semiconductors"),
            }

            try:
                client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=case_id,
                    input=item_input,
                    expected_output=item_expected,
                    metadata=item_metadata,
                )
                synced_count += 1
            except Exception as exc:
                logger.warning("Failed to sync item %s: %s", case_id, exc)

    logger.info("Successfully synced %d items to Langfuse dataset %s", synced_count, dataset_name)
    return synced_count


def sync_all_datasets() -> Dict[str, int]:
    """Sync all golden datasets to Langfuse."""
    client = get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse is not enabled or client could not be initialized.")

    results = {
        "verification_golden": sync_verification_golden(client),
        "combined_golden": sync_combined_golden(client),
    }
    client.flush()
    return results


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Sync local golden evaluation datasets to Langfuse.")
    parser.add_argument(
        "--dataset",
        choices=["verification", "combined", "all"],
        default="all",
        help="Which dataset to sync (default: all)",
    )
    args = parser.parse_args()

    if not is_observability_enabled():
        logger.error("Langfuse is disabled. Set LANGFUSE_ENABLED=true in .env to sync.")
        return 1

    client = get_langfuse_client()
    if client is None:
        logger.error("Could not initialize Langfuse client.")
        return 1

    if args.dataset in ("verification", "all"):
        sync_verification_golden(client)
    if args.dataset in ("combined", "all"):
        sync_combined_golden(client)

    client.flush()
    logger.info("Sync completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
