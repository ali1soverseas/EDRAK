"""Command line of the worker: `customer-trends run | show | runs`.

    customer-trends run --brief evals/customer_trends/briefs/competitive_intelligence.json \\
        --fixture --fake-llm --out result.json
    customer-trends show --run-id run-ci-gitlab-001
    customer-trends runs

Exit codes of `run`: 0 for a complete or partial result, 2 for an insufficient one, 1 for a
failure (an unreadable brief, a missing key, or a run that crashed).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from pydantic import ValidationError

from edrak.agents.customer_trends.logging import configure_logging
from edrak.agents.customer_trends.runner import stream_task
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INSUFFICIENT = 2
_TIME_SLICE = slice(11, 23)
_CLAIM_CHARS = 110


def format_event(event: dict[str, Any]) -> str:
    """One line per event: the time, the kind and what matters about it."""
    kind = str(event["type"])
    stamp = str(event.get("ts", ""))[_TIME_SLICE]
    if kind in {"node_started", "node_finished"}:
        parts = [str(event["node"])]
        if kind == "node_finished":
            parts += [str(event["status"]), f"{event['duration_ms']}ms"]
    elif kind == "tool_called":
        parts = [
            str(event["tool"]),
            f"branch={event.get('branch') or '-'}",
            f"provider={event.get('provider') or '-'}",
            f"fallback={'yes' if event.get('fallback_used') else 'no'}",
            f"status={event['status']}",
            f"count={event['count']}",
            f"{event['latency_ms']}ms",
            f"cost={event['cost']:.4f}",
        ]
        if event.get("error_code"):
            parts.append(f"error={event['error_code']}")
    elif kind == "gap_found":
        parts = [str(event["gap_id"]), str(event["severity"]), str(event["description"])]
    elif kind == "replan":
        parts = [f"pass={event['replan_count']}", "closing: " + ", ".join(event["gap_ids"])]
    elif kind in {"finding_accepted", "finding_rejected"}:
        parts = [str(event["finding_id"]), *map(str, event.get("reasons", []))]
    elif kind == "run_finished":
        parts = [
            f"status={event['status']}",
            f"findings={event.get('findings', '-')}",
            f"evidence={event.get('evidence', '-')}",
        ]
    elif kind == "run_failed":
        parts = [str(event["error"]), str(event.get("message", ""))]
    else:
        parts = []
    return " ".join(part for part in [stamp, f"{kind:<16}", *parts] if part).rstrip()


def _settings(args: argparse.Namespace) -> Settings:
    updates: dict[str, Any] = {}
    if getattr(args, "fixture", False):
        updates["edrak_provider_mode"] = "fixture"
    if getattr(args, "fake_llm", False):
        updates["edrak_fake_llm"] = True
    return get_settings().model_copy(update=updates)


def _load_brief(path: Path, err: TextIO) -> TaskBrief | None:
    try:
        return TaskBrief.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"cannot read the brief: {exc}", file=err)
    except ValidationError as exc:
        print(f"the brief is not valid ({path}):", file=err)
        for problem in exc.errors():
            location = ".".join(str(part) for part in problem["loc"]) or "brief"
            print(f"  {location}: {problem['msg']}", file=err)
    return None


def _read_result(settings: Settings, run_id: str) -> tuple[CustomerTrendsResult, str | None] | None:
    """The stored result of a run and where it lives, or None when there is none."""
    with EvidenceStore.from_settings(settings) as store:
        try:
            result = LocalSink(store, settings.artifacts_dir).read_result(run_id)
        except (OSError, ValueError):
            return None
        return result, store.result_location(run_id)


def summary_lines(result: CustomerTrendsResult, location: str | None = None) -> list[str]:
    summary = result.control_summary
    lines = [
        f"run {result.run_id}: {summary.status}, confidence {summary.overall_confidence.value}",
        f"headline: {summary.headline}",
        f"findings: {summary.findings_count}, evidence items: {summary.evidence_count}",
    ]
    lines += [f"gap: {gap}" for gap in summary.gaps]
    lines += [f"warning: {warning}" for warning in summary.warnings]
    for finding in result.findings:
        claim = (
            finding.claim
            if len(finding.claim) <= _CLAIM_CHARS
            else finding.claim[:_CLAIM_CHARS] + "..."
        )
        lines.append(f"  {finding.id} [{finding.type.value}, {finding.confidence.value}] {claim}")
    if location:
        lines.append(f"result: {location}")
    return lines


async def _stream(brief: TaskBrief, settings: Settings, out: TextIO) -> tuple[bool, dict[str, Any]]:
    failed, last = False, {}
    async for event in stream_task(brief, settings=settings):
        print(format_event(event), file=out, flush=True)
        failed = failed or event["type"] == "run_failed"
        last = event
    return failed, last


def command_run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    brief = _load_brief(args.brief, err)
    if brief is None:
        return EXIT_FAILED
    settings = _settings(args)
    if not settings.edrak_fake_llm and not settings.has_key("ollama_api_key"):
        print(
            "OLLAMA_API_KEY is not set: add it to .env at the repository root, "
            "or use --fake-llm for a demo with no keys",
            file=err,
        )
        return EXIT_FAILED
    failed, _ = asyncio.run(_stream(brief, settings, out))
    stored = _read_result(settings, brief.run_id)
    if stored is None:
        print("the run left no result", file=err)
        return EXIT_FAILED
    result, location = stored
    if args.out:
        args.out.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        location = str(args.out)
    print("\n".join(summary_lines(result, location)), file=out)
    if failed:
        return EXIT_FAILED
    return EXIT_INSUFFICIENT if result.control_summary.status == "insufficient" else EXIT_OK


def command_show(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    stored = _read_result(get_settings(), args.run_id)
    if stored is None:
        print(f"no result for run {args.run_id}", file=err)
        return EXIT_FAILED
    result, location = stored
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), file=out)
    else:
        print("\n".join(summary_lines(result, location)), file=out)
    return EXIT_OK


def command_runs(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    with EvidenceStore.from_settings(get_settings()) as store:
        runs = store.list_runs()
    if not runs:
        print("no runs yet", file=out)
        return EXIT_OK
    for run in runs:
        stamp = run.created_at.strftime("%Y-%m-%d %H:%M")
        result = "result" if run.result_location else "no result"
        print(
            f"{run.run_id}  {stamp}  evidence={run.evidence_count}  "
            f"findings={run.findings_count}  {result}",
            file=out,
        )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="customer-trends", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a task brief and stream its events")
    run.add_argument("--brief", type=Path, required=True, help="path of a TaskBrief JSON file")
    run.add_argument("--fixture", action="store_true", help="serve provider data from fixtures")
    run.add_argument("--fake-llm", action="store_true", help="use the scripted demo model")
    run.add_argument("--out", type=Path, help="also write the result JSON to this path")
    show = commands.add_parser("show", help="print the summary of a stored result")
    show.add_argument("--run-id", required=True)
    show.add_argument("--json", action="store_true", help="print the whole result as JSON")
    commands.add_parser("runs", help="list past runs")
    parser.add_argument(
        "--verbose", action="store_true", help="also show the structured log on stderr"
    )
    return parser


HANDLERS = {"run": command_run, "show": command_show, "runs": command_runs}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = get_settings().edrak_log_level if args.verbose else "WARNING"
    configure_logging(get_settings().model_copy(update={"edrak_log_level": level}))
    return HANDLERS[args.command](args, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
