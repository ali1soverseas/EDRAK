"""Run the decision agent once per criteria case and write the options report.

Workers and cross-signal are not run. Every case reuses the cross-signal in
the pair-case catalog. The criteria focus sentence is inserted by this script
into the decision agent's recommendation prompt and options prompt.

    python scripts/run_decision_focus_report.py
    python scripts/run_decision_focus_report.py --case time_to_value__together
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

DEFAULT_CATALOG = ARTIFACTS_DIR / "65515064-949c-403d-b5be-43c71f8864b6.pair-cases.json"
FOCUS_KEY = "criteria_focus"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_decision_focus_report.py")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--case", action="append", help="Case id to run. Repeatable. Default: every case in the catalog.")
    parser.add_argument("--concurrency", type=int, default=2)
    return parser


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = json.loads(Path(payload["source"]).read_text(encoding="utf-8"))
    payload["cross_signal"] = source["cross_signal"]
    return payload


def injected_focus(emphasis: str) -> str:
    return (
        "CRITERIA FOCUS:\n"
        f"{emphasis}\n"
        "Examine this focus where it is relevant. It decides what to examine, not which option to choose. "
        "Do not mention past decisions."
    )


def install_focus_injection() -> None:
    """Put the case's criteria focus into the prompts this process sends."""
    import edrak.DecisionAnalysis.nodes as nodes
    import edrak.DecisionAnalysis.options as options

    original_user = nodes.build_decision_analysis_user_prompt
    original_options = options._prompt

    def user_prompt(context: dict[str, Any]) -> str:
        focus = ((context.get("input_metadata") or {}).get(FOCUS_KEY) or "").strip()
        body = original_user(context)
        if not focus:
            return body
        return injected_focus(focus) + "\n\n" + body

    def options_prompt(context: dict[str, Any]) -> str:
        focus = ((context.get("input_metadata") or {}).get(FOCUS_KEY) or "").strip()
        body = original_options(context)
        if not focus:
            return body
        return injected_focus(focus) + "\n\n" + body

    nodes.build_decision_analysis_user_prompt = user_prompt
    options._prompt = options_prompt
    if "CRITERIA FOCUS" not in options.SYSTEM:
        options.SYSTEM = options.SYSTEM.rstrip() + (
            "\n- If CRITERIA FOCUS is given, examine every option on that focus in its thesis, "
            "trade-offs and risks. Keep options that do poorly on it as strong as the others. "
            "Do not say which option fits it best. Do not mention past decisions."
        )
    if "CRITERIA FOCUS" not in nodes.DECISION_ANALYSIS_SYSTEM_PROMPT:
        nodes.DECISION_ANALYSIS_SYSTEM_PROMPT = nodes.DECISION_ANALYSIS_SYSTEM_PROMPT.rstrip() + (
            "\n\n12. If CRITERIA FOCUS is given, examine that focus in the rationale, actions, "
            "risks and success criteria where it is relevant. It decides what to examine, not which "
            "answer to give. Do not mention past decisions.\n"
        )


def build_request(catalog: dict[str, Any]) -> Any:
    from edrak.contracts.request import BusinessContext, BusinessRequest, CompanyProfile, UseCase

    cross_signal = catalog["cross_signal"]
    return BusinessRequest(
        request_id=cross_signal["research_run_id"],
        goal=catalog["goal"],
        company_profile=CompanyProfile(name=catalog["company"]),
        business_context=BusinessContext(
            use_case=UseCase(catalog["use_case"]),
            markets=["United Arab Emirates"],
            offering="AI compute",
        ),
    )


def _texts(items: list[Any]) -> list[str]:
    found = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "")
        else:
            text = str(item or "")
        if text:
            found.append(text)
    return found


def compact_options(analysis: dict[str, Any]) -> list[dict[str, Any]]:
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
            "would_change_if": option.get("would_change_if") or "",
        })
    return options


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _bullets(lines: list[Any]) -> str:
    items = [str(line).strip() for line in lines or [] if str(line).strip()]
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items)


def _signal_block(signal: dict[str, Any]) -> str:
    findings = []
    for finding in signal.get("supporting_findings") or []:
        if isinstance(finding, dict):
            findings.append(f"{finding.get('finding_id')}: {finding.get('relevance')}")
        elif finding:
            findings.append(str(finding))
    parts = [
        f"### {signal.get('title')}",
        "",
        f"- Signal id: `{signal.get('signal_id')}`",
        f"- Type: {signal.get('signal_type')}",
        f"- Confidence: {signal.get('confidence')}",
        f"- Urgency: {signal.get('urgency')}",
        f"- Decision relevance: {signal.get('decision_relevance')}",
        "",
        signal.get("signal") or "",
        "",
        signal.get("interpretation") or "",
        "",
        "**Implications**",
        _bullets(signal.get("implications") or []) or "- none",
        "",
        f"**Opportunity.** {signal.get('opportunity_relevance') or 'none'}",
        "",
        f"**Risk.** {signal.get('risk_relevance') or 'none'}",
        "",
        f"**Decision question.** {signal.get('decision_question') or 'none'}",
        "",
        "**Decision areas**",
        _bullets(signal.get("decision_areas") or []) or "- none",
        "",
        "**Evidence gaps**",
        _bullets(signal.get("evidence_gaps") or []) or "- none",
        "",
        "**Supporting findings**",
        _bullets(findings) or "- none",
        "",
    ]
    return "\n".join(parts)


def _context_block(cross_signal: dict[str, Any]) -> str:
    context = cross_signal.get("decision_ready_context") or {}
    summary = cross_signal.get("summary") or {}
    sections = ["## Decision-ready context", ""]
    for label, key in (
        ("Decision questions", "decision_questions"),
        ("Decision areas", "decision_areas"),
        ("Opportunity indicators", "opportunity_indicators"),
        ("Risk indicators", "risk_indicators"),
        ("Dependencies", "dependencies"),
        ("Timing signals", "timing_signals"),
        ("Momentum signals", "momentum_signals"),
        ("Evidence gaps", "evidence_gaps"),
    ):
        sections.append(f"**{label}**")
        sections.append(_bullets(context.get(key) or []) or "- none")
        sections.append("")
    sections.append("## Cross-signal summary")
    sections.append("")
    for label, key in (
        ("Dominant patterns", "dominant_patterns"),
        ("Important dependencies", "important_dependencies"),
        ("Business impacts", "business_impacts"),
        ("Strategic themes", "strategic_themes"),
        ("Opportunity-relevant signals", "opportunity_relevant_signals"),
        ("Risk-relevant signals", "risk_relevant_signals"),
        ("Evidence gaps", "evidence_gaps"),
    ):
        sections.append(f"**{label}**")
        sections.append(_bullets(summary.get(key) or []) or "- none")
        sections.append("")
    return "\n".join(sections)


def _option_block(option: dict[str, Any]) -> str:
    lines = [
        f"#### {option.get('key')}. {option.get('title')}",
        "",
        option.get("thesis") or "",
        "",
        "**What we would do**",
        _bullets(option.get("what_we_would_do") or []) or "- none",
        "",
        "**Trade-offs**",
        _bullets(option.get("trade_offs") or []) or "- none",
        "",
        "**Opportunities**",
        _bullets(option.get("opportunities") or []) or "- none",
        "",
        "**Risks**",
        _bullets(option.get("risks") or []) or "- none",
        "",
        f"**Cost of waiting.** {option.get('cost_of_waiting') or 'none'}",
        "",
        f"**Would change if.** {option.get('would_change_if') or 'none'}",
        "",
    ]
    return "\n".join(lines)


def render_report(catalog: dict[str, Any], results: list[dict[str, Any]]) -> str:
    cross_signal = catalog["cross_signal"]
    by_id = {item["id"]: item for item in results}
    parts = [
        "# Decision agent criteria-focus test report",
        "",
        "The decision agent ran once for each case. No research worker and no cross-signal agent ran. "
        "Every case received the cross-signal below. The only difference is the criteria focus sentence "
        "inserted into the decision agent's recommendation prompt and options prompt.",
        "",
        f"- Research run: `{cross_signal.get('research_run_id')}`",
        f"- Use case: {catalog.get('use_case')}",
        f"- Goal: {catalog.get('goal')}",
        f"- Company: {catalog.get('company')}",
        f"- Cross-signal status: {cross_signal.get('status')}",
        f"- Signals: {len(cross_signal.get('signals') or [])}",
        "",
        "## Cross-signal input",
        "",
    ]
    for signal in cross_signal.get("signals") or []:
        parts.append(_signal_block(signal))
    parts.append(_context_block(cross_signal))
    parts.append("## Cases")
    parts.append("")
    for case in catalog["cases"]:
        result = by_id.get(case["id"])
        parts.append(f"### {case['label']} — {case['kind']}")
        parts.append("")
        parts.append(f"- Case: `{case['id']}`")
        parts.append(f"- Criteria: {', '.join(case['pair'])}")
        parts.append(f"- Relation: {case['relation']}")
        parts.append("")
        parts.append("**Chosen criteria focus**")
        parts.append("")
        parts.append(case.get("emphasis") or "")
        parts.append("")
        if result is None:
            parts.append("The decision agent has not been run for this case yet.")
            parts.append("")
            continue
        if result.get("error"):
            parts.append(f"The decision agent failed: {result['error']}")
            parts.append("")
            continue
        options = result.get("options") or []
        parts.append(f"**Options returned: {len(options)}**")
        parts.append("")
        if not options:
            parts.append("The decision agent returned no options.")
            parts.append("")
            continue
        for option in options:
            parts.append(_option_block(option))
    return "\n".join(parts).rstrip() + "\n"


async def run_one(catalog: dict[str, Any], case: dict[str, Any], request: Any, cross_signal: Any) -> dict[str, Any]:
    from edrak.contracts.DecisionAnalysis import DecisionAnalysisInput
    from edrak.DecisionAnalysis.service import run_decision_analysis

    record = {
        "id": case["id"],
        "relation": case["relation"],
        "label": case["label"],
        "pair": case["pair"],
        "kind": case["kind"],
        "emphasis": case["emphasis"],
        "injected_prompt": injected_focus(case["emphasis"]),
    }
    last_error = "unknown error"
    for attempt in (1, 2):
        try:
            decision_input = DecisionAnalysisInput(
                research_run_id=cross_signal.research_run_id,
                business_request=request,
                cross_signal=cross_signal,
                metadata={
                    "source_stage": "criteria_focus_report",
                    "case_id": case["id"],
                    FOCUS_KEY: case["emphasis"],
                },
            )
            result = await run_decision_analysis(decision_input)
            record["options"] = compact_options(result.model_dump(mode="json"))
            record.pop("error", None)
            return record
        except Exception as exc:  # noqa: BLE001 - keep the case and try once more
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  attempt {attempt} failed for {case['id']}: {last_error}")
    record["error"] = last_error
    record["options"] = []
    return record


async def run_cases(catalog: dict[str, Any], cases: list[dict[str, Any]], paths: dict[str, Path], prior: dict[str, Any]) -> int:
    from edrak.contracts.CrossSignal import CrossSignalOutput

    request = build_request(catalog)
    cross_signal = CrossSignalOutput.model_validate(catalog["cross_signal"])
    pending = [case for case in cases if case["id"] not in prior]
    print(f"{len(pending)} case(s) to run, {len(prior)} already saved")
    semaphore = asyncio.Semaphore(max(1, int(paths["concurrency"])))
    lock = asyncio.Lock()
    failures = 0

    async def one(index: int, case: dict[str, Any]) -> None:
        nonlocal failures
        async with semaphore:
            print(f"[{index}/{len(pending)}] {case['id']}")
            print(f"  focus: {case['emphasis']}")
            record = await run_one(catalog, case, request, cross_signal)
        if record.get("error"):
            failures += 1
        else:
            titles = ", ".join(option["title"] for option in record["options"])
            print(f"  {case['id']}: {len(record['options'])} option(s) — {titles}")
        async with lock:
            prior[case["id"]] = record
            ordered = [prior[item["id"]] for item in catalog["cases"] if item["id"] in prior]
            write_json(paths["json"], {
                "research_run_id": catalog["cross_signal"]["research_run_id"],
                "goal": catalog["goal"],
                "company": catalog["company"],
                "results": ordered,
            })
            paths["report"].write_text(render_report(catalog, ordered), encoding="utf-8")

    await asyncio.gather(*(one(index, case) for index, case in enumerate(pending, start=1)))
    print(f"report: {paths['report']}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = build_parser().parse_args(argv)
    if not resolve_api_key():
        print("OLLAMA_API_KEY is not set.", file=sys.stderr)
        return 2
    try:
        catalog = load_catalog(args.catalog)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"could not load catalog: {exc}", file=sys.stderr)
        return 2
    wanted = set(args.case or [])
    cases = [case for case in catalog["cases"] if not wanted or case["id"] in wanted]
    if wanted and len(cases) != len(wanted):
        found = {case["id"] for case in cases}
        print(f"unknown case id(s): {', '.join(sorted(wanted - found))}", file=sys.stderr)
        return 2
    install_focus_injection()
    run_id = catalog["cross_signal"]["research_run_id"]
    json_path = ARTIFACTS_DIR / f"{run_id}.decision-focus-report.json"
    report_path = ARTIFACTS_DIR / f"{run_id}.decision-focus-report.md"
    prior: dict[str, Any] = {}
    if json_path.is_file():
        stored = json.loads(json_path.read_text(encoding="utf-8"))
        prior = {item["id"]: item for item in stored.get("results") or [] if item.get("options") and not item.get("error")}
    paths = {"json": json_path, "report": report_path, "concurrency": args.concurrency}
    return asyncio.run(run_cases(catalog, cases, paths, prior))


if __name__ == "__main__":
    raise SystemExit(main())
