"""Run the guardrail evaluation set and print what passed.

    .venv/bin/python scripts/run_evals.py               # deterministic checks only
    .venv/bin/python scripts/run_evals.py --with-model  # also the model-based scope check

Cases live in backend/evals/guardrails/cases.json: questions that must be allowed, blocked or
redacted; document chunks that must be flagged or excluded from retrieval; web text that must be
cleaned. Exit code 1 when any case fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

CASES = ROOT / "backend" / "evals" / "guardrails" / "cases.json"


def run(with_model: bool = False) -> list[tuple[str, str, bool, str]]:
    from edrak.guardrails.content import is_high_risk, sanitize
    from edrak.guardrails.documents import scan_chunk
    from edrak.guardrails.query import check_query

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    outcomes: list[tuple[str, str, bool, str]] = []
    for case in cases["queries"]:
        verdict = check_query(case["text"], use_model=with_model)
        ok = verdict.action == case["expect"]
        if ok and case.get("code"):
            ok = verdict.code == case["code"]
        if ok and case.get("redacted_kinds"):
            kinds = next((c.detail.get("kinds", {}) for c in verdict.checks if c.name == "pii"), {})
            ok = set(case["redacted_kinds"]) <= set(kinds)
        outcomes.append(("query", case["text"][:60], ok, f"{verdict.action} {verdict.code or ''}".strip()))
    for case in cases["chunks"]:
        flags, exclude = scan_chunk(case["text"])
        ok = exclude == case["exclude"] and bool(flags) == case["flagged"]
        outcomes.append(("document", case["text"][:60].replace("\n", " "), ok, f"exclude={exclude} flags={len(flags)}"))
    for case in cases["web"]:
        _, removed = sanitize(case["text"])
        risky = is_high_risk(case["text"])
        ok = (removed >= 1) == (case["removed_lines"] >= 1) and risky == case["high_risk"]
        outcomes.append(("web", case["text"][:60].replace("\n", " "), ok, f"removed={removed} high_risk={risky}"))
    from edrak.guardrails.chat import check_answer
    from edrak.guardrails.plan import check_free_text

    metrics = {"u1": {"uid": "u1", "value": 19.0, "unit": "USD"}}
    for case in cases.get("chat", []):
        got = check_answer(case["answer"], case["label"], evidence_nos=case["evidence"], metric_uids=case["uids"],
                           brief_evidence_nos={1, 2}, brief_metrics=metrics, evidence_text={1: "Team plan is $19 per user"},
                           web_text=case.get("web", ""))
        ok = got.label == case["expect_label"] and got.removed_numbers == case["expect_removed"]
        ok = ok and (case.get("expect_text", "") in got.answer.lower()) and (case.get("expect_absent", "\0") not in got.answer.lower())
        outcomes.append(("chat", case["name"], ok, f"label={got.label} removed={got.removed_numbers}"))
    for case in cases.get("notes", []):
        blocked = check_free_text(case["text"]) is not None
        outcomes.append(("note", case["text"][:60], blocked == case["blocked"], f"blocked={blocked}"))
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-model", action="store_true")
    args = parser.parse_args()
    outcomes = run(args.with_model)
    for kind, text, ok, detail in outcomes:
        print(f"{'PASS' if ok else 'FAIL'}  {kind:<8}  {text:<62}  {detail}")
    failed = sum(1 for o in outcomes if not o[2])
    print(f"\n{len(outcomes) - failed}/{len(outcomes)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
