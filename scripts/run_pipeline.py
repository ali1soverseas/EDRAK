"""Run the EDRAK orchestrator from a natural-language query.

Examples
--------
Plan only, no workers needed::

    python scripts/run_pipeline.py --query "Compare GitLab Duo to GitHub Copilot on pricing" --plan-only

Full run (dispatch included; every task reports FAILED until real workers land)::

    python scripts/run_pipeline.py --query "Should GitLab enter automotive compliance?" --target "Azure DevOps"
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

# Anchor artifacts to the repo root so running the CLI from any directory writes
# to the same place instead of relative to the current working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "runs"

from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    NonBlankStr,
    OrchestrationResult,
    ResearchPlan,
    UseCase,
    WorkerRegistry,
)
from pydantic import BaseModel, Field, field_validator

# The orchestration chain imports core.config, which constructs Settings at import
# time and raises when LLM_API_KEY is missing. Those imports are deliberately
# deferred into the functions that need them so a missing key produces a readable
# message instead of a traceback during module import.

EXIT_COMPLETED = 0
EXIT_PARTIAL = 1
EXIT_FAILED = 2


class RequestBuildError(RuntimeError):
    """A usable request could not be assembled from the query and flags."""


class ParsedIntent(BaseModel):
    """The narrow slice of BusinessRequest the request-builder LLM may author.

    request_id, created_at, use_case, and trigger are stamped deterministically.

    ``company_name`` is deliberately a plain string: a query such as "what is the
    market size for developer tooling" names no company, and the model is right
    to say so. Deciding what to do about that is policy, so it belongs to
    build_request rather than here.
    """

    goal: NonBlankStr
    company_name: str = ""
    targets: list[NonBlankStr] = Field(default_factory=list)
    focus_areas: list[NonBlankStr] = Field(default_factory=list)
    constraints: list[NonBlankStr] = Field(default_factory=list)

    @field_validator("company_name", mode="before")
    @classmethod
    def _coerce_null_company(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("targets", "focus_areas", "constraints", mode="before")
    @classmethod
    def _coerce_nulls(cls, value: Any) -> Any:
        """Models frequently emit null instead of an empty list."""
        if value is None:
            return []
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item.strip()]
        return value


def force_utf8_output() -> None:
    """Make stdout/stderr tolerate any character a model may emit.

    Windows consoles default to a legacy codepage (cp1256 here) that cannot
    encode characters like U+2011 NON-BREAKING HYPHEN, which models produce
    freely. Without this, printing a perfectly valid plan crashes the CLI.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def resolve_api_key() -> str | None:
    """Resolve the key through Settings so .env is honoured.

    Reading os.environ directly would miss a key stored in .env, which is where
    it is supposed to live.
    """
    try:
        from edrak.core.config import settings

        raw = settings.OLLAMA_API_KEY
        # Settings may declare the key as SecretStr or as a plain str; accept both.
        key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw
    except Exception as exc:  # noqa: BLE001 - configuration errors vary by type
        print(f"could not load configuration: {exc}", file=sys.stderr)
        return None
    return key if key and key.strip() else None


def parse_intent(query: str) -> ParsedIntent:
    from edrak.core.llm import get_chat_model
    from edrak.orchestration.planner import PlanningError
    from edrak.orchestration.prompts import REQUEST_BUILDER_SYSTEM_PROMPT

    # json_mode rather than function_calling, for the same reason as the planner:
    # the request-builder prompt spells out the JSON shape and this model answers
    # with prose instead of a tool call, so binding it to the schema yields nothing.
    intent = get_chat_model(temperature=0).with_structured_output(
        ParsedIntent, method="json_mode"
    ).invoke(REQUEST_BUILDER_SYSTEM_PROMPT + "\n\n" + query)

    if not isinstance(intent, ParsedIntent):
        raise PlanningError("request builder returned no usable intent", str(intent)[:200])
    return intent


def build_request(query: str, args: argparse.Namespace) -> BusinessRequest:
    """Flags win over anything the model inferred."""
    intent = parse_intent(query)

    company = (args.company or intent.company_name).strip()
    if not company:
        raise RequestBuildError(
            "could not determine which company this query is about; "
            "pass --company (for example --company GitLab)"
        )

    return BusinessRequest(
        goal=intent.goal,
        company_profile=CompanyProfile(name=company),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=args.target or intent.targets,
            focus_areas=args.focus or intent.focus_areas,
            constraints=args.constraint or intent.constraints,
            time_window_days=args.time_window,
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="Run the EDRAK orchestrator against a natural-language query.",
    )
    parser.add_argument("--query", required=True, help="What you want to find out.")
    parser.add_argument("--company", help="Override the inferred company.")
    parser.add_argument(
        "--target",
        action="append",
        help="Competitor or product to compare against. Repeatable.",
    )
    parser.add_argument(
        "--focus", action="append", help="Comparison dimension. Repeatable."
    )
    parser.add_argument(
        "--constraint", action="append", help="Explicit limit. Repeatable."
    )
    parser.add_argument(
        "--time-window", type=int, help="Research recency window in days."
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Stop after planning. Needs no workers.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON only (suppresses the human tables).",
    )
    parser.add_argument(
        "--no-out", action="store_true", help="Do not write artifacts/runs output."
    )
    return parser


def write_artifact_plan(plan: ResearchPlan, request_id: str) -> Path | None:
    path = ARTIFACTS_DIR / f"{request_id}.plan.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"  warning: could not write {path}: {exc}", file=sys.stderr)
        return None
    return path


def render_request(request: BusinessRequest) -> None:
    print()
    print("=" * 70)
    print("[orchestrator] REQUEST")
    print("=" * 70)
    context = request.business_context
    print()
    print(f"  request_id : {request.request_id}")
    print(f"  goal       : {request.goal}")
    print(f"  company    : {request.company_profile.name}")
    print(f"  use_case   : {context.use_case.value}")
    print(f"  targets    : {', '.join(context.targets) or '(none)'}")
    print(f"  focus_areas: {', '.join(context.focus_areas) or '(none)'}")
    print(f"  constraints: {'; '.join(context.constraints) or '(none)'}")


def render_plan(plan: ResearchPlan) -> None:
    print()
    print(f"  [orchestrator] plan_id: {plan.plan_id}   tasks: {len(plan.tasks)}")
    for task in plan.tasks:
        print()
        print(f"  [{task.worker.value}]  attempt={task.attempt}")
        print(f"    task_id: {task.task_id}")
        print(f"    goal   : {task.goal}")
        print(f"    focus  : {task.focus}")


def render_result(result: OrchestrationResult) -> None:
    print()
    print("=" * 70)
    print("[orchestrator] RESULT")
    print("=" * 70)
    print(f"  status : {result.status.value}")
    if result.error:
        print(f"  error  : {result.error}")
    print(f"  results: {len(result.results)}")
    for item in result.results:
        print(f"    {item.worker.value:<24} {item.status.value:<10} {item.error or ''}")
    findings = sum(len(item.findings) for item in result.results)
    evidence = sum(len(item.evidence) for item in result.results)
    print()
    print(f"  findings: {findings}   evidence: {evidence}")
    if result.cross_signal is not None:
        signals = result.cross_signal.get("signals") or []
        print(
            "  [cross_signal] "
            f"{result.cross_signal.get('status', 'unknown')} "
            f"({len(signals)} signals)"
        )
        for index, signal in enumerate(signals, start=1):
            kind = signal.get("signal_type") or ""
            title = signal.get("title") or ""
            claim = signal.get("signal") or ""
            interpretation = signal.get("interpretation") or ""
            heading = title or claim or f"signal {index}"
            suffix = f" ({kind})" if kind else ""
            print(f"  [cross_signal] {index}. {heading}{suffix}")
            if claim:
                print(f"      claim: {claim}")
            if interpretation:
                print(f"      interpretation: {interpretation}")


def write_artifact(result: OrchestrationResult, suffix: str = "") -> Path | None:
    path = ARTIFACTS_DIR / f"{result.request_id}{suffix}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"  warning: could not write {path}: {exc}", file=sys.stderr)
        return None
    return path


def _render_human(request: BusinessRequest, payload: ResearchPlan | OrchestrationResult) -> None:
    """Print the human-readable report.

    ``--json`` skips this entirely so stdout stays parseable.
    """
    render_request(request)
    print()
    print("=" * 70)
    print("[orchestrator] PLAN")
    print("=" * 70)

    plan = payload if isinstance(payload, ResearchPlan) else payload.plan
    if plan is None:
        print("  (none)")
    else:
        render_plan(plan)

    if isinstance(payload, OrchestrationResult):
        render_result(payload)


def build_default_registry() -> WorkerRegistry:
    """Register the workers that are wired for this integration slice.

    All four contract workers are registered. Competitor, market and customer
    trends already expose classes satisfying the ``Worker`` protocol
    (``CompetitorAgent``, ``MarketIntelligence``, ``CustomerTrendsWorker``) and
    register directly. Internal intelligence exposes a module-level function, so
    a thin adapter carries it into the protocol.

    Internal intelligence retrieves from a handbook vector store. Until
    ``scripts/run_etl_pipeline.py`` has populated it, a dispatched internal task
    finds no evidence rather than failing outright.

    Every import is deferred. The customer trends package pulls in provider
    SDKs, and a worker that cannot reach its model or its search provider must
    not take down the rest of the run.
    """
    from edrak.contracts import WorkerType

    registry = WorkerRegistry()

    try:
        from edrak.agents.competitor_intelligence import CompetitorAgent

        registry.register(WorkerType.COMPETITOR_INTELLIGENCE, CompetitorAgent())
    except Exception as exc:  # noqa: BLE001 - import-time config guards vary
        print(
            f"  competitor intelligence unavailable: {exc}",
            file=sys.stderr,
        )

    try:
        from edrak.agents.market_intelligence.graph import MarketIntelligence

        registry.register(WorkerType.MARKET_INTELLIGENCE, MarketIntelligence())
    except Exception as exc:  # noqa: BLE001 - keep one bad worker from killing all
        print(f"  market intelligence unavailable: {exc}", file=sys.stderr)

    try:
        from edrak.agents.customer_trends.worker import CustomerTrendsWorker

        registry.register(WorkerType.CUSTOMER_TRENDS, CustomerTrendsWorker())
    except Exception as exc:  # noqa: BLE001 - provider SDK imports can fail
        print(f"  customer trends unavailable: {exc}", file=sys.stderr)

    try:
        from edrak.agents.internal_intelligence.graph import run_internal_intelligence

        class InternalWorker:
            """Adapter for internal intelligence's module-level entry point."""

            worker_type = WorkerType.INTERNAL_INTELLIGENCE

            def run(self, task: Any) -> Any:
                return run_internal_intelligence(task)

        registry.register(WorkerType.INTERNAL_INTELLIGENCE, InternalWorker())
    except Exception as exc:  # noqa: BLE001 - keep one bad worker from killing all
        print(f"  internal intelligence unavailable: {exc}", file=sys.stderr)

    return registry


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    args = build_parser().parse_args(argv)

    if not resolve_api_key():
        print(
            "LLM_API_KEY is not set. Copy .env.example to .env and fill it in.\n"
            "Create a key at https://ollama.com/settings/keys",
            file=sys.stderr,
        )
        return EXIT_FAILED

    if not args.query.strip():
        print("--query must not be empty", file=sys.stderr)
        return EXIT_FAILED

    from edrak.orchestration.planner import PlanningError

    try:
        request = build_request(args.query, args)
    except RequestBuildError as exc:
        print(f"request build failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except PlanningError as exc:
        print(f"request build failed: {exc.reason}", file=sys.stderr)
        if exc.raw_output:
            print(f"raw output: {exc.raw_output[:400]}", file=sys.stderr)
        return EXIT_FAILED

    if args.plan_only:
        from edrak.orchestration.planner import LlmPlanner

        try:
            plan = LlmPlanner().plan(request)
        except PlanningError as exc:
            print(f"planning failed: {exc.reason}", file=sys.stderr)
            if exc.raw_output:
                print(f"raw output: {exc.raw_output[:400]}", file=sys.stderr)
            return EXIT_FAILED

        if args.json:
            print(json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            _render_human(request, plan)
        if not args.no_out:
            out = write_artifact_plan(plan, request.request_id)
            if out:
                print()
                print(f"  [orchestrator] run artifact saved: {out}")
        return EXIT_COMPLETED

    from edrak.orchestration.graph import build_graph

    state = asyncio.run(
        build_graph(build_default_registry()).ainvoke({"request": request})
    )
    result = state["orchestration_result"]

    if args.json:
        payload = (
            plan.model_dump(mode="json")
            if args.plan_only
            else result.model_dump(mode="json")
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_human(request, plan if args.plan_only else result)

    if not args.no_out:
        out = (
            write_artifact_plan(plan, request.request_id)
            if args.plan_only
            else write_artifact(result)
        )
        if out:
            print()
            print(f"  [orchestrator] run artifact saved: {out}")

    if args.plan_only:
        return EXIT_COMPLETED
    if result.status.value == "completed":
        return EXIT_COMPLETED
    if result.status.value == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())