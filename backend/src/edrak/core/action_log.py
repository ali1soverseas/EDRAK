"""Append one plain-text line per agent action."""

from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def log_action(agent: str, message: str) -> None:
    path = _ROOT / "artifacts" / "logs" / f"{agent}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        return
