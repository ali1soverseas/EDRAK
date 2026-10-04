"""Delegates to backend/tests/test_internal_flow.py."""

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent.parent
test_dir = repo_root / "backend" / "tests"

if str(test_dir) not in sys.path:
    sys.path.insert(0, str(test_dir))

from test_internal_flow import test_full_internal_flow

if __name__ == "__main__":
    test_full_internal_flow()
