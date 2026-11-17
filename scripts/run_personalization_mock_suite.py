"""Run 20 mock accounts against 3 saved cross-signal fixtures and write a full report.

Assumes business context matches (fixture goal + UAE AI compute context). For each
account x fixture case: infer focuses, filter strategy themes, generate options.

    python scripts/run_personalization_mock_suite.py
    python scripts/run_personalization_mock_suite.py --limit-accounts 2 --limit-fixtures 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from edrak.DecisionAnalysis.mock_accounts import SHARED_BUSINESS, build_mock_accounts  # noqa: E402

CROSS_SIGNAL_FILES = (
    "5652337d-6c9f-4228-80e2-b43d0504cc69.preference-comparison.json",
    "65515064-949c-403d-b5be-43c71f8864b6.preference-comparison.json",
    "bd94bd85-0b02-421d-ab98-790a586a0347.preference-comparison.json",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_personalization_mock_suite.py")
    parser.add_argument("--limit-accounts", type=int, default=0, help="Run only the first N accounts (0 = all 20).")
    parser.add_argument("--limit-fixtures", type=int, default=0, help="Run only the first N fixtures (0 = all 3).")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip option generation; still write focuses, themes, and signals into the report.",
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


def _recommendation_summaries(cross_signal: dict[str, Any]) -> list[dict[str, Any]]:
    questions = (
        ((cross_signal.get("decision_ready_context") or {}).get("decision_questions"))
        or cross_signal.get("decision_questions")
        or []
    )
    signals = _signal_summaries(cross_signal)
    summaries = []
    for index, question in enumerate(questions[:5]):
        linked = [s["signal_id"] for s in signals[index:index + 2] if s.get("signal_id")]
        if not linked and signals:
            linked = [signals[0]["signal_id"]]
        summaries.append({
            "question": str(question),
            "decision": f"Address the evidence around: {question}",
            "signal_ids": [sid for sid in linked if sid],
            "opportunities": [],
            "risks": [],
        })
    if not summaries and signals:
        summaries.append({
            "question": "What strategic path fits the current signals?",
            "decision": "Choose among distinct paths grounded in the listed signals.",
            "signal_ids": [s["signal_id"] for s in signals[:3] if s.get("signal_id")],
            "opportunities": [],
            "risks": [],
        })
    return summaries


def _option_dump(option: Any) -> dict[str, Any]:
    if hasattr(option, "model_dump"):
        return option.model_dump(mode="json")
    if isinstance(option, dict):
        return option
    return {"value": str(option)}


async def run_case(
    account: dict[str, Any],
    fixture_name: str,
    fixture: dict[str, Any],
    *,
    skip_llm: bool,
) -> dict[str, Any]:
    from edrak.DecisionAnalysis.options import generate_options
    from edrak.DecisionAnalysis.personalization import emphasis_text, infer_preference, note_text, option_themes

    rows = account["strategies"]
    # Business context is assumed to match: use the full account history as relevant.
    preference = infer_preference(rows)
    themes = option_themes(rows, preference)
    if themes:
        preference = {**preference, "option_themes": themes}

    cross_signal = fixture["cross_signal"]
    signals = _signal_summaries(cross_signal)
    emphasis = emphasis_text(preference)
    case: dict[str, Any] = {
        "account_id": account["id"],
        "account_name": account["name"],
        "account_email": account["email"],
        "cross_signal_fixture": fixture_name,
        "goal": fixture.get("goal"),
        "use_case": fixture.get("use_case"),
        "preference": {key: value for key, value in preference.items() if key != "option_themes"},
        "analysis_emphasis": emphasis,
        "note": note_text(preference),
        "mock_strategies_all": [
            {
                "id": row["id"],
                "title": row["title"],
                "thesis": row.get("thesis"),
                "tags": row.get("tags"),
                "option_label": row.get("option_label"),
            }
            for row in rows
        ],
        "mock_strategies_used": themes,
        "cross_signal_signals": signals,
        "options": [],
        "warnings": [],
        "error": None,
    }

    if skip_llm:
        case["warnings"].append("LLM skipped (--skip-llm); options not generated.")
        return case

    context = {
        "business_goal": fixture.get("goal") or "Decide whether to enter the UAE AI compute market.",
        "business_request": {
            "company_profile": {"name": "NVIDIA"},
            "business_context": {
                "use_case": fixture.get("use_case") or "market_entry_expansion",
                **SHARED_BUSINESS,
            },
            "goal": fixture.get("goal"),
        },
        "signals": signals,
        "recommendation_summaries": _recommendation_summaries(cross_signal),
        "analysis_emphasis": emphasis,
        "history_option_themes": themes,
        "input_metadata": {"metric_context": [], "blocking_gaps": []},
    }
    try:
        options, warnings = await generate_options(context)
        case["options"] = [_option_dump(option) for option in options]
        case["warnings"] = list(warnings or [])
    except Exception as exc:  # noqa: BLE001
        case["error"] = f"{type(exc).__name__}: {exc}"
        case["warnings"].append(case["error"])
    return case


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = [
        "# Personalization mock suite report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Accounts: {report['account_count']}",
        f"- Fixtures: {report['fixture_count']}",
        f"- Cases: {report['case_count']}",
        "",
    ]
    for case in report["cases"]:
        lines.extend([
            f"## {case['account_id']} × `{case['cross_signal_fixture']}`",
            "",
            f"- Account: {case['account_name']} (`{case['account_email']}`)",
            f"- Goal: {case.get('goal')}",
            f"- Preference: `{case.get('preference')}`",
            f"- Emphasis: {case.get('analysis_emphasis')}",
            f"- Note: {case.get('note')}",
            "",
            "### Mock strategies used",
            "",
        ])
        used = case.get("mock_strategies_used") or []
        if not used:
            lines.append("_None passed the tighter focus-tag filter._")
            lines.append("")
        for theme in used:
            lines.append(
                f"- **{theme.get('title')}** — {theme.get('thesis')} "
                f"(tags: {', '.join(theme.get('tags') or [])})"
            )
        lines.extend(["", "### Cross-signal signals", ""])
        for signal in case.get("cross_signal_signals") or []:
            lines.append(f"- `{signal.get('signal_id')}` — {signal.get('title')}")
        lines.extend(["", "### Options", ""])
        if case.get("error"):
            lines.append(f"_Error: {case['error']}_")
            lines.append("")
        options = case.get("options") or []
        if not options and not case.get("error"):
            lines.append("_No options generated._")
            lines.append("")
        for option in options:
            lines.extend([
                f"#### Option {option.get('key')}: {option.get('title')}",
                "",
                option.get("thesis") or "",
                "",
            ])
            for warning in case.get("warnings") or []:
                lines.append(f"_Warning: {warning}_")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> int:
    runs_dir = REPO_ROOT / "artifacts" / "runs"
    reports_dir = REPO_ROOT / "artifacts" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    fixture_names = list(CROSS_SIGNAL_FILES)
    if args.limit_fixtures > 0:
        fixture_names = fixture_names[: args.limit_fixtures]
    fixtures: list[tuple[str, dict[str, Any]]] = []
    for name in fixture_names:
        path = runs_dir / name
        if not path.is_file():
            print(f"missing fixture: {path}", file=sys.stderr)
            return 2
        fixtures.append((name, _load_fixture(path)))

    accounts = build_mock_accounts()
    if args.limit_accounts > 0:
        accounts = accounts[: args.limit_accounts]

    cases: list[dict[str, Any]] = []
    total = len(accounts) * len(fixtures)
    index = 0
    for account in accounts:
        for fixture_name, fixture in fixtures:
            index += 1
            print(f"[{index}/{total}] {account['id']} × {fixture_name}")
            case = await run_case(account, fixture_name, fixture, skip_llm=args.skip_llm)
            cases.append(case)
            if case.get("error"):
                print(f"  error: {case['error']}")
            else:
                print(
                    f"  preference={case['preference'].get('status')} "
                    f"themes={len(case.get('mock_strategies_used') or [])} "
                    f"options={len(case.get('options') or [])}"
                )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    report = {
        "generated_at": stamp,
        "account_count": len(accounts),
        "fixture_count": len(fixtures),
        "case_count": len(cases),
        "fixtures": fixture_names,
        "assume_business_context_matches": True,
        "cases": cases,
    }
    json_path = reports_dir / f"personalization_mock_suite_{stamp}.json"
    md_path = reports_dir / f"personalization_mock_suite_{stamp}.md"
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
