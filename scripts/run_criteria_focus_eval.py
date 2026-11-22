"""Criteria-focus evaluation on personalization v1, for the accounts version 2 generated.

The account set, fixture, questions, and report layout match
scripts/run_criteria_focus_eval.py on the version 2 branch. Tags are not taken
from the strategy bank. The model assigns option and reason tags for each past
decision. Version 1 then selects the top K histories and injects one analysis
emphasis sentence into option generation.

    python scripts/run_criteria_focus_eval.py
    python scripts/run_criteria_focus_eval.py --skip-llm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))
os.chdir(REPO_ROOT)

FIXTURE_NAME = "5652337d-6c9f-4228-80e2-b43d0504cc69.preference-comparison.json"
# Accounts whose options_generated flag was true in the version 2 report
# artifacts/reports/criteria_focus_eval_2026-11-18_02-56-36.json.
SUCCESSFUL_ACCOUNTS = (
    "mock-account-01",
    "mock-account-08",
    "mock-account-09",
    "mock-account-11",
    "mock-account-14",
    "mock-account-15",
    "mock-account-16",
    "mock-account-19",
)

STRATEGY_BANK: list[dict[str, str]] = [
    {"title": "Pilot with a local partner", "thesis": "Enter through a short joint pilot before a full footprint."},
    {"title": "Build a sovereign stack in-house", "thesis": "Own the stack and the compliance path inside the company."},
    {"title": "Match competitor usage pricing", "thesis": "Compete on flexible usage-based pricing and discounts."},
    {"title": "Wait for clearer residency rules", "thesis": "Hold until data residency requirements are unambiguous."},
    {"title": "Lead on performance differentiation", "thesis": "Win on hardware leadership and distinct capability."},
    {"title": "Scale capacity with partners", "thesis": "Grow volume through alliances and shared infrastructure."},
    {"title": "Hire a local delivery team first", "thesis": "Staff expertise before committing to a large deployment."},
    {"title": "Run a reversible limited offer", "thesis": "Start with a pilot that can be undone if demand is weak."},
    {"title": "Push for earlier revenue", "thesis": "Prioritize paths that monetize sooner even if margin is thinner."},
    {"title": "Protect customer retention quality", "thesis": "Choose the path that keeps buyers through experience quality."},
    {"title": "Lower total cost of ownership", "thesis": "Optimize operating cost and ROI over peak performance."},
    {"title": "Move fast with proven kit", "thesis": "Ship with proven hardware to reduce failure risk."},
]

OFF_CONTEXT_QUESTIONS = [
    "Which grocery delivery pricing model should noon adopt in Egypt?",
    "Should GitLab enter the automotive compliance software market?",
    "How should a hospital group choose an electronic health record vendor in Germany?",
    "What packaging format should a dairy brand use for school milk in Brazil?",
    "Should a regional airline add a cargo-only route between Nairobi and Dubai?",
    "Which payroll vendor should a retail chain in Canada replace this year?",
    "How should a hotel group price weekend rooms in Barcelona?",
    "Should a textile mill in Bangladesh add a second dyeing line?",
    "What crop insurance product should a cooperative offer wheat farmers in Kansas?",
    "How should a city bus operator in Lisbon renew its diesel fleet?",
]

FALLBACK_WARNING = (
    "Distinct options could not be generated; the question-level directions are shown instead."
)

TAG_SYSTEM = """You assign preference tags for each past decision.

Return one decisions entry per decision, in the same order.
option: every criterion the chosen strategy stresses.
reason: every criterion the reason names.
Use only the allowed keys. Use an empty list when none apply.
Do not invent keys and do not explain the choice."""


class TaggedDecision(BaseModel):
    option: list[str] = Field(default_factory=list)
    reason: list[str] = Field(default_factory=list)


class AccountTagAssignment(BaseModel):
    decisions: list[TaggedDecision]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_criteria_focus_eval.py")
    parser.add_argument("--skip-llm", action="store_true", help="Assign tags and write the emphasis, but do not generate options.")
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


def _history_shells(goal: str, *, count: int = 20, decisions_per_account: int = 20) -> list[dict[str, Any]]:
    """The same questions and strategy texts as the version 2 accounts. Tags are filled later."""
    half = decisions_per_account // 2
    accounts: list[dict[str, Any]] = []
    for index in range(count):
        account_id = f"mock-account-{index + 1:02d}"
        if account_id not in SUCCESSFUL_ACCOUNTS:
            continue
        decisions: list[dict[str, Any]] = []
        for offset in range(decisions_per_account):
            template = STRATEGY_BANK[(index + offset) % len(STRATEGY_BANK)]
            on_context = offset >= half
            asked = goal if on_context else OFF_CONTEXT_QUESTIONS[(index + offset) % len(OFF_CONTEXT_QUESTIONS)]
            decisions.append({
                "id": f"{account_id}-d{offset + 1:02d}",
                "question": asked,
                "title": template["title"],
                "thesis": template["thesis"],
                "recorded_at": f"2026-01-{(offset % 28) + 1:02d}T12:00:00Z",
            })
        accounts.append({"id": account_id, "decisions": decisions})
    return accounts


def _criteria_catalog() -> str:
    from edrak.DecisionAnalysis.personalization import CRITERIA

    return "\n".join(f"- {key}: {spec['label']}. {spec['focus']}" for key, spec in CRITERIA.items())


def _kept(names: list[str]) -> list[str]:
    from edrak.DecisionAnalysis.personalization import _ordered

    return _ordered([str(name).strip() for name in names if str(name).strip()])


def _apply_tags(decision: dict[str, Any], option: list[str], reason: list[str]) -> None:
    decision["option"] = option
    decision["reason"] = reason
    decision["tags"] = _kept(option + reason)
    decision["why"] = {}
    decision["preference_tags"] = json.dumps({"version": 2, "option": option, "reason": reason})


def _decision_prompt(decisions: list[dict[str, Any]], catalog: str) -> str:
    blocks = []
    for index, decision in enumerate(decisions, start=1):
        blocks.append(
            f"Decision {index}\n"
            f"Question: {decision['question']}\n"
            f"Chosen strategy: {decision['title']}\n"
            f"Reason: {decision['thesis']}"
        )
    count = len(decisions)
    return (
        f"Allowed criteria:\n{catalog}\n\n"
        f"Assign tags for exactly {count} decisions. The decisions array must contain {count} items, in this order.\n\n"
        + "\n\n".join(blocks)
    )


async def assign_account_tags(account: dict[str, Any], catalog: str) -> None:
    from edrak.core.llm import get_structured, schema_instruction

    decisions = account["decisions"]
    model = get_structured(AccountTagAssignment, agent="orchestrator", temperature=0)
    prompt = _decision_prompt(decisions, catalog)
    messages: list[tuple[str, str]] = [
        ("system", TAG_SYSTEM),
        ("user", prompt),
        schema_instruction(AccountTagAssignment),
    ]
    last_error = ""
    result: AccountTagAssignment | None = None
    for attempt in range(2):
        try:
            raw = await model.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            raw = None
        if isinstance(raw, AccountTagAssignment) and len(raw.decisions) == len(decisions):
            result = raw
            break
        got = len(raw.decisions) if isinstance(raw, AccountTagAssignment) else 0
        last_error = last_error or f"expected {len(decisions)} tag rows, got {got}"
        if attempt == 0:
            messages = [
                ("system", TAG_SYSTEM),
                ("user", prompt + f"\n\nYour previous answer had {got} decisions. Return exactly {len(decisions)}."),
                schema_instruction(AccountTagAssignment),
            ]
    if result is None:
        raise RuntimeError(last_error or "tag assignment returned no structured tags")
    for decision, tags in zip(decisions, result.decisions):
        _apply_tags(decision, _kept(tags.option), _kept(tags.reason))


def _history_view(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question": row.get("question"),
            "tags": list(row.get("tags") or []),
            "why": dict(row.get("why") or {}),
        }
        for row in decisions
    ]


def _relevance_rows(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Question only, so similarity matches the version 2 eval's goal-to-question score."""
    return [
        {
            "id": row.get("id"),
            "question": row.get("question"),
            "preference_tags": row.get("preference_tags"),
            "recorded_at": row.get("recorded_at"),
        }
        for row in decisions
    ]


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
    return {"goal": goal}


async def run_case(account: dict[str, Any], fixture: dict[str, Any], *, skip_llm: bool) -> dict[str, Any]:
    from edrak.core.config import settings
    from edrak.DecisionAnalysis.options import generate_options
    from edrak.DecisionAnalysis.personalization import embed_texts, emphasis_text, infer_preference, select_relevant

    goal = str(fixture.get("goal") or "").strip()
    decisions = account["decisions"]
    chosen = select_relevant(
        _relevance_rows(decisions),
        _request(goal),
        k=settings.PERSONALIZATION_HISTORY_K,
        threshold=settings.PERSONALIZATION_SIMILARITY_MIN,
        aspect_bar=settings.PERSONALIZATION_ASPECT_BAR,
        embed=embed_texts,
    )
    preference = infer_preference(chosen, aspect_bar=settings.PERSONALIZATION_ASPECT_BAR)
    emphasis = emphasis_text(preference)
    cross_signal = fixture["cross_signal"]
    signals = _signal_summaries(cross_signal)
    case: dict[str, Any] = {
        "case_id": account["id"],
        "question": goal,
        "decision_history": _history_view(decisions),
        "cross_signal": signals,
        "criteria_focus": [{"criterion": "analysis emphasis", "summary": emphasis}] if emphasis else [],
        "analysis_emphasis": emphasis,
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
        "analysis_emphasis": emphasis,
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
    accounts = _history_shells(goal)
    catalog = _criteria_catalog()

    print(f"Assigning tags for {sum(len(account['decisions']) for account in accounts)} decisions")
    for index, account in enumerate(accounts, start=1):
        print(f"[tags {index}/{len(accounts)}] {account['id']}")
        try:
            await assign_account_tags(account, catalog)
        except Exception as exc:  # noqa: BLE001
            print(f"  tag error: {_safe_error(exc)}", file=sys.stderr)
            return 1
        tagged = sum(1 for row in account["decisions"] if row.get("tags"))
        print(f"  tagged {tagged}/{len(account['decisions'])}")

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

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    report = {
        "generated_at": stamp,
        "fixture": FIXTURE_NAME,
        "personalization": "v1",
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
