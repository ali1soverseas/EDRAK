"""Run every worker through cross-signal once, then decision analysis once per account.

Both decision passes use the same findings. The only difference is the saved
decision history of the account, so the printed options can be compared.

History is matched by use case. Pass --use-case to stamp the request.
Haitham's history is tagged speed. Fred's is tagged cost. The seeded
competitive-intelligence rows match the default use case. Market-entry rows
with the same tags match --use-case market_entry_expansion.

Human review is not paused. After verification the script goes straight to
cross-signal.

    python scripts/compare_decision_personalization.py --query "Should we enter the UAE AI compute market?" --company "NVIDIA"
    python scripts/compare_decision_personalization.py --query "Should we enter the UAE AI compute market?" --company "NVIDIA" --use-case market_entry_expansion --market "United Arab Emirates"
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

from run_pipeline import (  # noqa: E402
    ARTIFACTS_DIR,
    RequestBuildError,
    build_default_registry,
    build_request,
    force_utf8_output,
    resolve_api_key,
)

ACCOUNTS = ("haitham@company.com", "fred@company.com")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare_decision_personalization.py",
        description="Run the workers through cross-signal, then decision analysis once for each account.",
    )
    parser.add_argument("--query", required=True, help="What you want to find out.")
    parser.add_argument("--company", help="Override the inferred company.")
    parser.add_argument("--target", action="append", help="Competitor or product to compare against. Repeatable.")
    parser.add_argument("--focus", action="append", help="Comparison dimension. Repeatable.")
    parser.add_argument("--constraint", action="append", help="Explicit limit. Repeatable.")
    parser.add_argument("--time-window", type=int, help="Research recency window in days.")
    parser.add_argument(
        "--use-case",
        choices=("competitive_intelligence", "market_entry_expansion", "product_launch"),
        default="competitive_intelligence",
        help="Use case stamped on the request. Saved decisions are matched by this value.",
    )
    parser.add_argument(
        "--market",
        action="append",
        help="Place the analysis is about. Repeatable.",
    )
    parser.add_argument(
        "--email",
        action="append",
        dest="emails",
        help="Account whose saved decisions personalize one decision pass. Repeatable. "
        "Defaults to haitham@company.com and fred@company.com.",
    )
    return parser


def build_until_cross_signal(registry: Any) -> Any:
    """The orchestrator graph, ending when cross-signal has written its output."""
    from langgraph.graph import END, StateGraph

    from edrak.orchestration.nodes import (
        cross_signal_node,
        make_dispatch_worker,
        metrics_node,
        plan_node,
        verification_gate_node,
    )
    from edrak.core.config import settings
    from edrak.orchestration.routing import route_from_plan
    from edrak.orchestration.state import OrchestrationState

    graph = StateGraph(OrchestrationState)
    graph.add_node("plan", plan_node)
    graph.add_node("dispatch", make_dispatch_worker(registry))
    graph.add_node("verify", verification_gate_node)
    graph.add_node("metrics", metrics_node)
    graph.add_node("cross_signal", cross_signal_node)
    graph.set_entry_point("plan")
    graph.add_conditional_edges(
        "plan",
        route_from_plan,
        {"dispatch": "dispatch", "finalize_failed": END},
    )
    def after_verify(_state: Any) -> str:
        return "metrics" if settings.METRICS_GATHERING_ENABLED else "cross_signal"

    graph.add_edge("dispatch", "verify")
    graph.add_conditional_edges("verify", after_verify, ["metrics", "cross_signal"])
    graph.add_edge("metrics", "cross_signal")
    graph.add_edge("cross_signal", END)
    return graph.compile()


async def preference_for(email: str, request: Any) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    """The account's workspace and the preference its relevant saved tags imply."""
    from edrak.DecisionAnalysis.personalization import HISTORY_POOL, embed_texts, infer_preference, load_history, select_relevant
    from edrak.core.config import settings
    from edrak.db.connection import get_db, init_db
    from edrak.db.util import fetch_one

    use_case = request.business_context.use_case.value
    await init_db()
    async with get_db() as db:
        account = await fetch_one(
            db,
            """SELECT u.email, w.id AS workspace_id
               FROM users u JOIN workspaces w ON w.owner_id = u.id
               WHERE lower(u.email) = lower(?)""",
            (email,),
        )
        if account is None:
            return None, {"status": "none"}, 0
        history = await load_history(
            db, account["workspace_id"], use_case, limit=max(HISTORY_POOL, settings.PERSONALIZATION_HISTORY_K),
        )
    chosen = select_relevant(
        history, request, k=settings.PERSONALIZATION_HISTORY_K, threshold=settings.PERSONALIZATION_SIMILARITY_MIN,
        aspect_bar=settings.PERSONALIZATION_ASPECT_BAR, embed=embed_texts,
    )
    if not chosen:
        return account, {"status": "none"}, 0
    return account, infer_preference(chosen, aspect_bar=settings.PERSONALIZATION_ASPECT_BAR), len(chosen)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def print_decision(email: str, preference: dict[str, Any], analysis: dict[str, Any] | None, error: str | None) -> None:
    from edrak.DecisionAnalysis.personalization import emphasis_text, note_text

    print()
    print("=" * 72)
    print(f"DECISIONS FOR {email}")
    print("=" * 72)
    print(f"  preference : {preference.get('status', 'none')}")
    if preference.get("criterion"):
        print(f"  criterion  : {preference['criterion']}")
    if preference.get("criteria"):
        print(f"  criteria   : {', '.join(preference['criteria'])}")
    emphasis = emphasis_text(preference)
    print(f"  emphasis   : {emphasis or '(none — the analysis is not personalized)'}")
    if error:
        print(f"  error      : {error}")
        return
    if not analysis:
        print("  (no decision analysis)")
        return
    print(f"  status     : {_text(analysis.get('status'))}")
    note = (analysis.get("personalization") or {}).get("status")
    shown = note_text(analysis.get("personalization")) or note_text(preference)
    print(f"  note       : {shown or '(no emphasis note)'}")
    print(f"  personalization on result: {note or 'absent'}")
    print()
    print(f"  summary: {analysis.get('executive_summary') or '(none)'}")
    options = analysis.get("options") or []
    print()
    print(f"  options ({len(options)}):")
    if not options:
        print("    (none)")
        return
    for option in options:
        print()
        print(f"    [{option.get('key')}] {option.get('title')}")
        print(f"      thesis: {option.get('thesis')}")
        for step in option.get("what_we_would_do") or []:
            print(f"      do: {step}")
        for item in option.get("opportunities") or []:
            text = item.get("text") if isinstance(item, dict) else item
            print(f"      opportunity: {text}")
        for item in option.get("risks") or []:
            text = item.get("text") if isinstance(item, dict) else item
            print(f"      risk: {text}")


def print_comparison(rows: list[dict[str, Any]]) -> None:
    print()
    print("=" * 72)
    print("COMPARISON")
    print("=" * 72)
    for row in rows:
        preference = row["preference"]
        focus = preference.get("criterion") or ", ".join(preference.get("criteria") or []) or "none"
        print()
        print(f"  {row['email']}  ({preference.get('status')} / {focus})")
        analysis = row.get("analysis") or {}
        options = analysis.get("options") or []
        if row.get("error"):
            print(f"    error: {row['error']}")
        elif not options:
            print("    (no options)")
        for option in options:
            print(f"    [{option.get('key')}] {option.get('title')}")
            print(f"         {option.get('thesis')}")


async def run(args: argparse.Namespace) -> int:
    from edrak.orchestration.nodes import make_decision_analysis_node
    from edrak.orchestration.planner import PlanningError

    try:
        request = build_request(args.query, args)
        from edrak.contracts.request import UseCase

        context_updates: dict[str, Any] = {"use_case": UseCase(args.use_case)}
        if args.market:
            context_updates["markets"] = args.market
        request = request.model_copy(
            update={"business_context": request.business_context.model_copy(update=context_updates)}
        )
    except RequestBuildError as exc:
        print(f"request build failed: {exc}", file=sys.stderr)
        return 2
    except PlanningError as exc:
        print(f"request build failed: {exc.reason}", file=sys.stderr)
        return 2

    use_case = request.business_context.use_case.value
    emails = args.emails or list(ACCOUNTS)
    print(f"use case: {use_case}")
    print("running workers, verification, metrics, then cross-signal")

    state = await build_until_cross_signal(build_default_registry()).ainvoke({"request": request})
    cross_signal = state.get("cross_signal")
    if not cross_signal or str(cross_signal.get("status") or "").lower() != "completed":
        status = (cross_signal or {}).get("status") if isinstance(cross_signal, dict) else None
        print(
            f"cross-signal did not complete (status={status or 'missing'}). Decision analysis was not run.",
            file=sys.stderr,
        )
        return 2

    signals = cross_signal.get("signals") or []
    print(f"cross-signal completed with {len(signals)} signal(s)")

    decision_node = make_decision_analysis_node()
    compared: list[dict[str, Any]] = []
    for email in emails:
        account, preference, history_count = await preference_for(email, request)
        print()
        print("-" * 72)
        if account is None:
            print(f"{email}: no workspace, decision analysis skipped")
            compared.append({"email": email, "preference": preference, "analysis": None, "error": "no workspace"})
            continue
        print(f"{email}: {history_count} tagged decision(s), preference {preference.get('status')}")
        update = await decision_node({**state, "decision_preference": preference})
        analysis = update.get("decision_analysis")
        error = update.get("error")
        print_decision(email, preference, analysis, error)
        compared.append({"email": email, "preference": preference, "analysis": analysis, "error": error})

    print_comparison(compared)

    path = ARTIFACTS_DIR / f"{request.request_id}.preference-comparison.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "request_id": request.request_id,
                    "use_case": use_case,
                    "goal": request.goal,
                    "cross_signal": cross_signal,
                    "accounts": compared,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        print()
        print(f"comparison saved: {path}")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)

    if any(row.get("analysis") for row in compared):
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = build_parser().parse_args(argv)
    if not resolve_api_key():
        print("OLLAMA_API_KEY is not set.", file=sys.stderr)
        return 2
    if not args.query.strip():
        print("--query must not be empty", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
