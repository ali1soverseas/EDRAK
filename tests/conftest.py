from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"

for path in (SRC, ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
