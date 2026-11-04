"""Print what EDRAK's model calls have cost and how they performed.

Reads the usage log (one row per model request, written by ``edrak.usage``) and
renders it. It changes nothing and calls no model.

    python scripts/llm_usage_report.py system                  # everything
    python scripts/llm_usage_report.py run <run_id>            # one workflow, by stage
    python scripts/llm_usage_report.py run <run_id> --json     # the raw aggregation
    python scripts/llm_usage_report.py system --html           # pages, into the log directory
    python scripts/llm_usage_report.py run <run_id> --html out.html

By default this reports the aggregated numbers; ``--json`` prints the same data
as JSON for scripting. ``--html`` writes the same numbers as self-contained pages
instead of printing them. Call ``flush`` first so calls still in flight are counted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from edrak.usage import (
    flush,
    html_report,
    log_dir,
    report,
)


def _use_utf8() -> None:
    """Box-drawing output needs a UTF-8 stream; a cp1256 console would raise."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        pass


def _html_files(args: argparse.Namespace) -> list[Path]:
    """Write the pages ``--html`` asked for. No value means the log directory.

    ``system`` writes index.html, system.html and one page per run, because the
    index links to those pages and a broken link is worse than no page. ``run``
    writes the one page, to the given file or, if given a directory, inside it.
    """
    if args.command == "run":
        if not args.html:
            return [html_report.write_run_page(log_dir(), args.run_id)]
        target = Path(args.html).expanduser()
        if target.is_dir():
            return [html_report.write_run_page(target, args.run_id)]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_report.render_run_html(args.run_id), encoding="utf-8")
        return [target]
    directory = Path(args.html).expanduser() if args.html else log_dir()
    return html_report.write_index(directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDRAK LLM usage report")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="one workflow, split by stage")
    run.add_argument("run_id")
    run.add_argument("--json", action="store_true")
    run.add_argument("--ascii", action="store_true", help="plain ASCII boxes")
    run.add_argument(
        "--html", nargs="?", const="", default=None, metavar="FILE",
        help="write this run's HTML page (default: run-<run_id>.html in the log directory)",
    )

    system = sub.add_parser("system", help="totals across every run")
    system.add_argument("--json", action="store_true")
    system.add_argument("--ascii", action="store_true", help="plain ASCII boxes")
    system.add_argument(
        "--html", nargs="?", const="", default=None, metavar="DIR",
        help="write index.html, system.html and a page per run (default: the log directory)",
    )

    args = parser.parse_args(argv)
    command = args.command or "system"
    unicode = False if getattr(args, "ascii", False) else None

    flush(timeout=5.0)

    if getattr(args, "html", None) is not None:
        for path in _html_files(args):
            print(f"wrote {path}")
        return 0

    _use_utf8()

    if command == "run":
        payload = report.workflow_summary(args.run_id)
        output = (
            json.dumps(payload, indent=2)
            if args.json
            else report.render_workflow(args.run_id, unicode=unicode)
        )
    else:
        payload = report.system_summary()
        output = json.dumps(payload, indent=2) if args.json else report.render_system(unicode=unicode)

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
