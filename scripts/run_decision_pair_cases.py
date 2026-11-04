"""Decision-agent cases for every criteria pair, on one saved cross-signal.

The cross-signal is the competitive-intelligence run already stored at
artifacts/runs/65515064-949c-403d-b5be-43c71f8864b6.preference-comparison.json.
Every case uses that output. What changes is the emphasis implied by a pair
of criteria.

Each pair has three cases:

- together: both criteria on the same two past decisions. The agent is asked
  to examine the narrower relation.
- outweighed: the same pair, with the first criterion also named in the
  typed reason so it scores higher. The narrower sentence stays.
- split: each criterion on its own decisions. The agent is asked to make
  the trade-off explicit.

Writing the catalog does not call a model. ``--run`` calls the decision
agent. ``together`` and ``outweighed`` send the same sentence, so a run
skips ``outweighed`` unless ``--kind`` includes it.

    python scripts/run_decision_pair_cases.py
    python scripts/run_decision_pair_cases.py --run --pair time_to_value
    python scripts/run_decision_pair_cases.py --run --kind together split
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from run_pipeline import ARTIFACTS_DIR, force_utf8_output, resolve_api_key  # noqa: E402

DEFAULT_SOURCE = ARTIFACTS_DIR / "65515064-949c-403d-b5be-43c71f8864b6.preference-comparison.json"
AGENT_KINDS = ("together", "split")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_decision_pair_cases.py",
        description="Prepare, and optionally run, decision-agent cases for every criteria pair.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Preference-comparison file whose cross_signal is reused for every case.",
    )
    parser.add_argument("--company", default="NVIDIA", help="Company on the business request.")
    parser.add_argument("--pair", action="append", help="Relation id or left,right. Repeatable. Default: every pair.")
    parser.add_argument(
        "--kind",
        action="append",
        choices=("together", "outweighed", "split"),
        help="Case kinds to include. Default: all three in the catalog, together and split when running.",
    )
    parser.add_argument("--run", action="store_true", help="Call the decision agent and save each output.")
    return parser


def load_source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"cross-signal source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cross_signal = payload.get("cross_signal")
    if not isinstance(cross_signal, dict) or str(cross_signal.get("status") or "").lower() != "completed":
        raise ValueError(f"{path} has no completed cross_signal")
    return payload


def select_cases(cases: list[dict[str, Any]], pairs: list[str] | None, kinds: list[str] | None) -> list[dict[str, Any]]:
    wanted_pairs = {item.strip().lower() for item in pairs or []}
    wanted_kinds = set(kinds or [])
    chosen = []
    for case in cases:
        if wanted_kinds and case["kind"] not in wanted_kinds:
            continue
        if wanted_pairs and case["relation"].lower() not in wanted_pairs and ",".join(case["pair"]) not in wanted_pairs:
            continue
        chosen.append(case)
    return chosen


def catalog(payload: dict[str, Any], source: Path, cases: list[dict[str, Any]], company: str) -> dict[str, Any]:
    signals = payload["cross_signal"].get("signals") or []
    questions = [signal.get("decision_question") for signal in signals if signal.get("decision_question")]
    return {
        "source": str(source),
        "research_run_id": payload["cross_signal"].get("research_run_id"),
        "use_case": payload.get("use_case"),
        "goal": payload.get("goal"),
        "company": company,
        "signal_count": len(signals),
        "decision_questions": questions,
        "case_count": len(cases),
        "cases": cases,
    }


def build_request(payload: dict[str, Any], company: str) -> Any:
    from edrak.contracts.request import BusinessContext, BusinessRequest, CompanyProfile, UseCase

    return BusinessRequest(
        request_id=payload["cross_signal"]["research_run_id"],
        goal=payload["goal"],
        company_profile=CompanyProfile(name=company),
        business_context=BusinessContext(
            use_case=UseCase(payload["use_case"]),
            markets=["United Arab Emirates"],
            offering="AI compute",
        ),
    )


def _texts(items: list[Any]) -> list[str]:
    found = []
    for item in items or []:
        if isinstance(item, dict):
            found.append(str(item.get("text") or ""))
        elif item:
            found.append(str(item))
    return [item for item in found if item]


def compact_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    options = []
    for option in analysis.get("options") or []:
        options.append({
            "key": option.get("key"),
            "title": option.get("title"),
            "thesis": option.get("thesis"),
            "what_we_would_do": option.get("what_we_would_do") or [],
            "trade_offs": option.get("trade_offs") or [],
            "opportunities": _texts(option.get("opportunities") or []),
            "risks": _texts(option.get("risks") or []),
            "cost_of_waiting": option.get("cost_of_waiting") or "",
        })
    recommendations = []
    for item in analysis.get("question_recommendations") or []:
        recommendation = item.get("recommendation") or {}
        recommendations.append({
            "question": item.get("question"),
            "decision": recommendation.get("decision"),
            "rationale": recommendation.get("rationale"),
            "actions": recommendation.get("actions") or [],
            "risks": recommendation.get("risks") or [],
            "success_criteria": recommendation.get("success_criteria") or [],
        })
    return {
        "status": analysis.get("status"),
        "executive_summary": analysis.get("executive_summary"),
        "options": options,
        "recommendations": recommendations,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


async def run_cases(payload: dict[str, Any], cases: list[dict[str, Any]], company: str, output_path: Path) -> int:
    from edrak.contracts.CrossSignal import CrossSignalOutput
    from edrak.contracts.DecisionAnalysis import DecisionAnalysisInput
    from edrak.DecisionAnalysis.service import run_decision_analysis

    existing: dict[str, Any] = {}
    if output_path.is_file():
        stored = json.loads(output_path.read_text(encoding="utf-8"))
        existing = {item["id"]: item for item in stored.get("results") or [] if not item.get("error")}

    request = build_request(payload, company)
    cross_signal = CrossSignalOutput.model_validate(payload["cross_signal"])
    results = []
    failures = 0
    for index, case in enumerate(cases, start=1):
        prior = existing.get(case["id"])
        if prior is not None:
            print(f"[{index}/{len(cases)}] {case['id']} already saved")
            results.append(prior)
            continue
        print(f"[{index}/{len(cases)}] {case['id']}")
        print(f"  emphasis: {case['emphasis']}")
        record = {
            "id": case["id"],
            "relation": case["relation"],
            "pair": case["pair"],
            "kind": case["kind"],
            "emphasis": case["emphasis"],
            "checks": case["checks"],
        }
        try:
            decision_input = DecisionAnalysisInput(
                research_run_id=cross_signal.research_run_id,
                business_request=request,
                cross_signal=cross_signal,
                metadata={
                    "source_stage": "pair_cases",
                    "decision_preference": case["preference"],
                    "case_id": case["id"],
                },
            )
            result = await run_decision_analysis(decision_input)
            record["analysis"] = compact_analysis(result.model_dump(mode="json"))
            titles = ", ".join(option["title"] for option in record["analysis"]["options"])
            print(f"  options: {titles or '(none)'}")
        except Exception as exc:  # noqa: BLE001 - record the failure and continue with the next case
            record["error"] = f"{type(exc).__name__}: {exc}"
            failures += 1
            print(f"  error: {record['error']}")
        results.append(record)
        write_json(output_path, {
            "source_research_run_id": cross_signal.research_run_id,
            "goal": payload.get("goal"),
            "company": company,
            "results": results,
        })
    print(f"outputs saved: {output_path}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = build_parser().parse_args(argv)
    from edrak.DecisionAnalysis.pair_cases import build_cases

    try:
        payload = load_source(args.source)
    except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cases = select_cases(build_cases(), args.pair, args.kind)
    if not cases:
        print("no cases matched --pair and --kind", file=sys.stderr)
        return 2
    run_kinds = set(args.kind or AGENT_KINDS)
    to_run = [case for case in cases if case["kind"] in run_kinds]

    written = catalog(payload, args.source, cases, args.company)
    catalog_path = ARTIFACTS_DIR / f"{payload['cross_signal']['research_run_id']}.pair-cases.json"
    try:
        write_json(catalog_path, written)
    except OSError as exc:
        print(f"could not write {catalog_path}: {exc}", file=sys.stderr)
        return 2
    print(f"{len(cases)} case(s) from {payload.get('use_case')} / {len(payload['cross_signal'].get('signals') or [])} signals")
    print(f"catalog saved: {catalog_path}")
    if not args.run:
        return 0
    if not to_run:
        print("no cases left to run", file=sys.stderr)
        return 2
    if not resolve_api_key():
        print("OLLAMA_API_KEY is not set.", file=sys.stderr)
        return 2
    output_path = ARTIFACTS_DIR / f"{payload['cross_signal']['research_run_id']}.pair-decision-outputs.json"
    return asyncio.run(run_cases(payload, to_run, args.company, output_path))


if __name__ == "__main__":
    raise SystemExit(main())
