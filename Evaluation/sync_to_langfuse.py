#!/usr/bin/env python3
"""Sync local golden evaluation datasets to Langfuse Datasets catalog.

Enables managing, tracking, and updating golden datasets in the Langfuse UI:
- `edrak-verification-golden`
- `edrak-combined-golden`

Usage:
    python Evaluation/sync_to_langfuse.py --dataset verification
    python Evaluation/sync_to_langfuse.py --dataset combined
    python Evaluation/sync_to_langfuse.py --dataset all
"""

import sys
from pathlib import Path

# Ensure backend/src is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from edrak.evaluation.langfuse_sync import main

if __name__ == "__main__":
    raise SystemExit(main())
