"""Generate three options for 20 mock accounts against one saved cross-signal.

Relevance is the similarity of each past question to the fixture goal. The
criteria focus from the questions that pass is passed into generate_options.
Each account is one model call, the options call. The report is written for a
person to hand to Claude. This script does not call a judge.

    python scripts/run_criteria_focus_eval.py
    python scripts/run_criteria_focus_eval.py --limit-accounts 1
    python scripts/run_criteria_focus_eval.py --skip-llm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from edrak.DecisionAnalysis.mock_accounts import build_focus_eval_accounts  # noqa: E402

FIXTURE_NAME = "5652337d-6c9f-4228-80e2-b43d0504cc69.preference-comparison.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_criteria_focus_eval.py")
    parser.add_argument("--limit-accounts", type=int, default=0, help="Run only the first N accounts (0 = all 20).")
    parser.add_argument("--only", default="", help="Comma-separated account ids to run.")
    parser.add_argument("--base", default="", help="Earlier report JSON. Kept cases stay; rerun ids replace them.")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Select history and write the focus, but do not generate options.",
    )
    return parser


def _load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cross_signal = data.get("cross_signal")
    if not isinstance(cross_signal, dict) or str(cross_signal.get("status") or "").lower() != "completed":
        raise ValueError(f"{path.name}: missing completed cross_signal")
    signals = cross_signal.get("signals") or []
    if len(signals) < 5:
        raise ValueError(f"{path.name}: expected at least 5 signals, found {len(signals)}")
    goal = str(data.get("goal") or "").strip()
    if not goal:
        raise ValueError(f"{path.name}: missing goal")
    return data


def _signal_summaries(cross_signal: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for signal in cross_signal.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        out.append({
            "signal_id": signal.get("signal_id") or signal.get("id"),
            "title": signal.get("title"),
            "signal": signal.get("signal"),
            "interpretation": signal.get("interpretation"),
        })
    return out


def _question_directions(cross_signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Fixture questions the options prompt reads. No recommendation call is made."""
    questions = (
        ((cross_signal.get("decision_ready_context") or {}).get("decision_questions"))
        or cross_signal.get("decision_questions")
        or []
    )
    signals = _signal_summaries(cross_signal)
    directions = []
    for index, question in enumerate(questions):
        linked = [s["signal_id"] for s in signals[index:index + 2] if s.get("signal_id")]
        directions.append({
            "question": str(question),
            "decision": str(question),
            "signal_ids": linked,
            "opportunities": [],
            "risks": [],
        })
    return directions


def _history_view(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question": row.get("question"),
            "tags": list(row.get("tags") or []),
            "why": dict(row.get("why") or {}),
        }
        for row in decisions
    ]


def _injected_focus(focus: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The criterion and why lines the options prompt actually receives."""
    lines: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in focus:
        for item in entry.get("items") or []:
            criterion = str(item.get("criterion") or "").strip()
            summary = str(item.get("summary") or "").strip()
            key = (criterion, summary)
            if not criterion or not summary or key in seen:
                continue
            seen.add(key)
            lines.append({"criterion": criterion, "summary": summary})
    return lines


FALLBACK_WARNING = (
    "Distinct options could not be generated; the question-level directions are shown instead."
)


def _option_dump(option: Any) -> dict[str, Any]:
    if hasattr(option, "model_dump"):
        return option.model_dump(mode="json")
    if isinstance(option, dict):
        return option
    return {"value": str(option)}


def _safe_error(exc: BaseException) -> str:
    text = re.sub(r"https?://\S+", "", str(exc))
    text = re.sub(r"\s+", " ", text).strip()
    return f"{type(exc).__name__}: {text[:400]}"


def _request(goal: str) -> dict[str, str]:
    """Goal only. Empty business text keeps relevance on the question."""
    return {"goal": goal}


async def run_case(
    account: dict[str, Any],
    fixture: dict[str, Any],
    *,
    skip_llm: bool,
) -> dict[str, Any]:
    from edrak.core.config import settings
    from edrak.DecisionAnalysis.options import generate_options
    from edrak.DecisionAnalysis.personalization import criteria_focus, embed_texts, select_relevant

    goal = str(fixture.get("goal") or "").strip()
    decisions = account["decisions"]
    chosen = select_relevant(
        decisions,
        _request(goal),
        k=settings.PERSONALIZATION_HISTORY_K,
        threshold=settings.PERSONALIZATION_SIMILARITY_MIN,
        aspect_bar=settings.PERSONALIZATION_ASPECT_BAR,
        embed=embed_texts,
    )
    focus = criteria_focus(chosen)
    injected = _injected_focus(focus)
    cross_signal = fixture["cross_signal"]
    signals = _signal_summaries(cross_signal)
    case: dict[str, Any] = {
        "case_id": account["id"],
        "question": goal,
        "decision_history": _history_view(decisions),
        "cross_signal": signals,
        "criteria_focus": injected,
        "options": [],
        "options_generated": False,
        "warnings": [],
        "error": None,
    }
    if skip_llm:
        case["warnings"].append("LLM skipped (--skip-llm); options not generated.")
        return case

    context = {
        "business_goal": goal,
        "business_request": {
            "company_profile": {"name": "NVIDIA"},
            "business_context": {"use_case": fixture.get("use_case") or "market_entry_expansion"},
            "goal": goal,
        },
        "signals": signals,
        "recommendation_summaries": _question_directions(cross_signal),
        "criteria_focus": focus,
        "input_metadata": {"metric_context": [], "blocking_gaps": []},
    }
    try:
        options, warnings = await generate_options(context)
    except Exception as exc:  # noqa: BLE001
        case["error"] = _safe_error(exc)
        case["warnings"].append(case["error"])
        return case
    case["options"] = [_option_dump(option) for option in options]
    case["warnings"] = list(warnings or [])
    case["options_generated"] = FALLBACK_WARNING not in case["warnings"]
    return case


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = [
        "# Criteria focus evaluation",
        "",
        "Read each case against its question, decision history, and cross-signal data.",
        "Judge whether the three options suit the reasons in that history.",
        "The criteria focus is what was injected into option generation.",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Fixture: `{report['fixture']}`",
        f"- Cases: {report['case_count']}",
        "",
    ]
    for case in report["cases"]:
        lines.extend([
            f"## {case['case_id']}",
            "",
            "### Question",
            "",
            case.get("question") or "",
            "",
            "### Decision history",
            "",
        ])
        for index, row in enumerate(case.get("decision_history") or [], start=1):
            tags = ", ".join(row.get("tags") or []) or "none"
            lines.append(f"#### Decision {index}")
            lines.append("")
            lines.append(f"- Question: {row.get('question')}")
            lines.append(f"- Tags: {tags}")
            why = row.get("why") or {}
            if not why:
                lines.append("- Why: none")
            else:
                lines.append("- Why:")
                for tag, summary in why.items():
                    lines.append(f"  - {tag}: {summary}")
            lines.append("")
        lines.extend(["### Cross-signal", ""])
        for signal in case.get("cross_signal") or []:
            lines.extend([
                f"#### {signal.get('signal_id')}",
                "",
                f"- Title: {signal.get('title')}",
                f"- Signal: {signal.get('signal')}",
                f"- Interpretation: {signal.get('interpretation')}",
                "",
            ])
        lines.extend(["### Criteria focus", ""])
        focus = case.get("criteria_focus") or []
        if not focus:
            lines.append("No past question passed relevance, so no criteria focus was injected.")
            lines.append("")
        for item in focus:
            lines.append(f"- {item.get('criterion')}: {item.get('summary')}")
        if focus:
            lines.append("")
        lines.extend(["### Options", ""])
        if case.get("error"):
            lines.append(f"Error: {case['error']}")
            lines.append("")
        if case.get("options") and not case.get("options_generated"):
            lines.append(
                "Option generation did not succeed. The items below are the question-level fallback, not options shaped by the criteria focus."
            )
            lines.append("")
        options = case.get("options") or []
        if not options and not case.get("error"):
            lines.append("No options generated.")
            lines.append("")
        for option in options:
            lines.extend([
                f"#### Option {option.get('key')}: {option.get('title')}",
                "",
                option.get("thesis") or "",
                "",
            ])
            steps = option.get("what_we_would_do") or []
            if steps:
                lines.append("What we would do:")
                lines.append("")
                for step in steps:
                    lines.append(f"- {step}")
                lines.append("")
            trade_offs = option.get("trade_offs") or []
            if trade_offs:
                lines.append("Trade-offs:")
                lines.append("")
                for item in trade_offs:
                    lines.append(f"- {item}")
                lines.append("")
            opportunities = option.get("opportunities") or []
            if opportunities:
                lines.append("Opportunities:")
                lines.append("")
                for item in opportunities:
                    text = item.get("text") if isinstance(item, dict) else str(item)
                    lines.append(f"- {text}")
                lines.append("")
            risks = option.get("risks") or []
            if risks:
                lines.append("Risks:")
                lines.append("")
                for item in risks:
                    text = item.get("text") if isinstance(item, dict) else str(item)
                    lines.append(f"- {text}")
                lines.append("")
        for warning in case.get("warnings") or []:
            lines.append(f"Warning: {warning}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> int:
    runs_dir = REPO_ROOT / "artifacts" / "runs"
    reports_dir = REPO_ROOT / "artifacts" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / FIXTURE_NAME
    if not path.is_file():
        print(f"missing fixture: {path}", file=sys.stderr)
        return 2
    fixture = _load_fixture(path)
    goal = str(fixture["goal"]).strip()
    accounts = build_focus_eval_accounts(goal)
    if args.limit_accounts > 0:
        accounts = accounts[: args.limit_accounts]
    only = {item.strip() for item in args.only.split(",") if item.strip()}
    if only:
        accounts = [account for account in accounts if account["id"] in only]

    cases: list[dict[str, Any]] = []
    total = len(accounts)
    for index, account in enumerate(accounts, start=1):
        print(f"[{index}/{total}] {account['id']}")
        case = await run_case(account, fixture, skip_llm=args.skip_llm)
        cases.append(case)
        if case.get("error"):
            print(f"  error: {case['error']}")
        else:
            print(f"  focus={len(case.get('criteria_focus') or [])} options={len(case.get('options') or [])}")

    if args.base:
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        by_id = {case["case_id"]: case for case in cases}
        cases = [by_id.get(case["case_id"], case) for case in base.get("cases") or []]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    report = {
        "generated_at": stamp,
        "fixture": FIXTURE_NAME,
        "question": goal,
        "case_count": len(cases),
        "cases": cases,
    }
    json_path = reports_dir / f"criteria_focus_eval_{stamp}.json"
    md_path = reports_dir / f"criteria_focus_eval_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _write_markdown(report, md_path)
    print()
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
