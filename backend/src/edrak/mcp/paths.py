from pathlib import Path
import sys


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def ensure_repo_on_path() -> Path:
    root = repo_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
