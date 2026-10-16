"""Resolve git conflict hunks in a file by choosing one side for every hunk.

Usage:  python scripts/resolve_conflicts.py <file> <ours|theirs|ours_first>

Hunks are replaced by the chosen side. With ``ours_first`` every hunk resolves
to the current branch, which is what the removed-LLMClient findings call for.
"""

import re
import sys
from pathlib import Path

MARK = re.compile(r"^(<<<<<<< |=======$|>>>>>>> )")


def resolve(path: Path, mode: str) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    ours: list[str] = []
    theirs: list[str] = []
    state = "clean"
    hunks = 0

    def keep(chunk: list[str]) -> None:
        if mode == "ours":
            out.extend(ours)
        else:
            out.extend(theirs)

    for line in lines:
        if line.startswith("<<<<<<< "):
            state = "ours"
            ours, theirs = [], []
            hunks += 1
            continue
        if line.startswith("=======") and state in ("ours", "theirs"):
            state = "theirs"
            continue
        if line.startswith(">>>>>>> ") and state in ("ours", "theirs"):
            keep([])
            state = "clean"
            continue
        if state == "ours":
            ours.append(line)
        elif state == "theirs":
            theirs.append(line)
        else:
            out.append(line)

    if state != "clean":
        raise SystemExit(f"{path}: unbalanced conflict markers, not touching it")
    path.write_text("".join(out), encoding="utf-8")
    return hunks, len(out)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    mode = sys.argv[2] if len(sys.argv) > 2 else "ours"
    if mode not in ("ours", "theirs"):
        print(f"mode must be ours or theirs, got {mode!r}")
        return 2
    for target in sys.argv[1].split(","):
        path = Path(target)
        hunks, lines = resolve(path, mode)
        print(f"  {target}: {hunks} hunk(s) -> {mode}, {lines} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())