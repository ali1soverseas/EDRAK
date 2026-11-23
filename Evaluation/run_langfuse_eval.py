#!/usr/bin/env python3
"""Run EDRAK evaluations against Langfuse datasets with cross-branch & schedule tracking.

Runs verification or worker experiments against managed Langfuse datasets,
attaching Git branch/commit metadata, worker LLM call traces, prompts, and score evaluations.

Usage:
    # Run verification evaluation (current branch, manual trigger)
    python Evaluation/run_langfuse_eval.py --dataset verification

    # Run for scheduled / CI trigger
    python Evaluation/run_langfuse_eval.py --dataset verification --trigger scheduled

    # Run against a specific branch name
    python Evaluation/run_langfuse_eval.py --dataset verification --branch feature/improved-rag

    # Run combined worker evaluations
    python Evaluation/run_langfuse_eval.py --dataset combined --worker competitor_intelligence
"""

import sys
from pathlib import Path

# Ensure backend/src is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from edrak.evaluation.langfuse_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
