"""Drop outweighed cases from the focus report and run each newer aspect alone.

The eight aspects added after speed, cost, control, quality and risk each get
one decision-agent run. The preference is a single criterion, so the agent
receives that aspect's focus sentence. The saved cross-signal is reused.

    python scripts/run_single_aspect_report.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from run_pipeline import ARTIFACTS_DIR, force_utf8_output, resolve_api_key  # noqa: E402

RUN_ID = "65515064-949c-403d-b5be-43c71f8864b6"
SOURCE = ARTIFACTS_DIR / f"{RUN_ID}.preference-comparison.json"
REPORT_JSON = ARTIFACTS_DIR / f"{RUN_ID}.decision-focus-report.json"
REPORT_MD = ARTIFACTS_DIR / f"{RUN_ID}.decision-focus-report.md"
NEW_ASPECTS = ("scale", "differentiation", "reversibility", "compliance", "revenue", "customer", "partnership", "talent")


def _row(criterion: str, title: str) -> dict[str, Any]:
    tags = {"version": 2, "option": [criterion], "reason": []}
    return {"id": title, "title": title, "option_label": "A", "preference_tags": json.dumps(tags)}


def single_cases() -> list[dict[str, Any]]:
    from edrak.DecisionAnalysis.personalization import CRITERIA, emphasis_text, infer_preference

    cases = []
    for criterion in NEW_ASPECTS:
        preference = infer_preference([
            _row(criterion, f"{criterion} first"),
            _row(criterion, f"{criterion} second"),
        ])
        cases.append({
            "id": f"{criterion}__single",
            "relation": None,
            "label": CRITERIA[criterion]["label"],
            "pair": [criterion],
            "kind": "single",
            "emphasis": emphasis_text(preference),
            "preference": preference,
        })
    return cases


def _texts(items: list[Any]) -> list[str]:
    found = []
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else str(item or "")
        if text:
            found.append(str(text))
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


def _bullets(lines: list[Any]) -> str:
    items = [str(line).strip() for line in lines or [] if str(line).strip()]
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def _option_block(option: dict[str, Any]) -> str:
    return "\n".join([
        f"#### {option.get('key')}. {option.get('title')}",
        "",
        option.get("thesis") or "",
        "",
        "**What we would do**",
        _bullets(option.get("what_we_would_do") or []),
        "",
        "**Trade-offs**",
        _bullets(option.get("trade_offs") or []),
        "",
        "**Opportunities**",
        _bullets(option.get("opportunities") or []),
        "",
        "**Risks**",
        _bullets(option.get("risks") or []),
        "",
        f"**Cost of waiting.** {option.get('cost_of_waiting') or 'none'}",
        "",
        f"**Would change if.** {option.get('would_change_if') or 'none'}",
        "",
    ])


def _case_block(result: dict[str, Any]) -> str:
    criteria = ", ".join(result.get("pair") or [])
    relation = result.get("relation") or "none"
    lines = [
        f"### {result.get('label')} — {result.get('kind')}",
        "",
        f"- Case: `{result.get('id')}`",
        f"- Criteria: {criteria}",
        f"- Relation: {relation}",
        "",
        "**Chosen criteria focus**",
        "",
        result.get("emphasis") or "",
        "",
    ]
    if result.get("error"):
        lines.extend([f"The decision agent failed: {result['error']}", ""])
        return "\n".join(lines)
    options = result.get("options") or []
    lines.extend([f"**Options returned: {len(options)}**", ""])
    if not options:
        lines.extend(["The decision agent returned no options.", ""])
        return "\n".join(lines)
    lines.extend(_option_block(option) for option in options)
    return "\n".join(lines)


def write_report(results: list[dict[str, Any]]) -> None:
    text = REPORT_MD.read_text(encoding="utf-8")
    marker = "\n## Cases\n"
    head, _, _ = text.partition(marker)
    head = head.replace(
        "The only difference is the criteria focus sentence inserted into the decision agent's recommendation prompt and options prompt.",
        "Together and split cases keep their criteria-focus sentence. Each of the eight newer aspects is also run on its own.",
    )
    singles = [item for item in results if item.get("kind") == "single"]
    pairs = [item for item in results if item.get("kind") != "single"]
    body = ["## Cases", ""]
    body.extend(_case_block(item) for item in pairs)
    body.extend(["## Single aspects", ""])
    body.extend(_case_block(item) for item in singles)
    REPORT_MD.write_text(head + "\n" + "\n".join(body).rstrip() + "\n", encoding="utf-8")


def load_results() -> dict[str, Any]:
    payload = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    payload["results"] = [item for item in payload.get("results") or [] if item.get("kind") != "outweighed" and not str(item.get("id") or "").endswith("__outweighed")]
    return payload


def save(payload: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(payload["results"])


def build_request(source: dict[str, Any]) -> Any:
    from edrak.contracts.request import BusinessContext, BusinessRequest, CompanyProfile, UseCase

    return BusinessRequest(
        request_id=source["cross_signal"]["research_run_id"],
        goal=source["goal"],
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase(source["use_case"]),
            markets=["United Arab Emirates"],
            offering="AI compute",
        ),
    )


async def run_one(case: dict[str, Any], request: Any, cross_signal: Any) -> dict[str, Any]:
    from edrak.contracts.DecisionAnalysis import DecisionAnalysisInput
    from edrak.DecisionAnalysis.service import run_decision_analysis

    record = {key: case[key] for key in ("id", "relation", "label", "pair", "kind", "emphasis")}
    last_error = "unknown error"
    for attempt in (1, 2):
        try:
            decision_input = DecisionAnalysisInput(
                research_run_id=cross_signal.research_run_id,
                business_request=request,
                cross_signal=cross_signal,
                metadata={"source_stage": "single_aspect_report", "case_id": case["id"], "decision_preference": case["preference"]},
            )
            result = await run_decision_analysis(decision_input)
            record["options"] = compact_options(result.model_dump(mode="json"))
            return record
        except Exception as exc:  # noqa: BLE001 - one retry, then record the failure
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  attempt {attempt} failed for {case['id']}: {last_error}")
    record["error"] = last_error
    record["options"] = []
    return record


async def run(payload: dict[str, Any], pending: list[dict[str, Any]]) -> int:
    from edrak.contracts.CrossSignal import CrossSignalOutput

    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    request = build_request(source)
    cross_signal = CrossSignalOutput.model_validate(source["cross_signal"])
    failures = 0
    for index, case in enumerate(pending, start=1):
        print(f"[{index}/{len(pending)}] {case['id']}")
        print(f"  focus: {case['emphasis']}")
        record = await run_one(case, request, cross_signal)
        if record.get("error"):
            failures += 1
        else:
            print(f"  {len(record['options'])} option(s)")
        payload["results"].append(record)
        save(payload)
    return 1 if failures else 0


def main() -> int:
    force_utf8_output()
    payload = load_results()
    save(payload)
    done = {item["id"] for item in payload["results"] if item.get("options") and not item.get("error")}
    pending = [case for case in single_cases() if case["id"] not in done]
    if not pending:
        print(f"report updated: {REPORT_MD}")
        return 0
    if not resolve_api_key():
        print("OLLAMA_API_KEY is not set.", file=sys.stderr)
        return 2
    code = asyncio.run(run(payload, pending))
    print(f"report updated: {REPORT_MD}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
