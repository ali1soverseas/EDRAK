"""Run the EDRAK orchestrator from a natural-language query.

Prints a start-of-run briefing, live [agent_name] progress, then an
end-of-run recap a technical reader can follow without project background.

Examples
--------
Plan only, no workers needed::

    python scripts/run_pipeline.py --query "Compare GitLab Duo to GitHub Copilot on pricing" --plan-only

Full run::

    python scripts/run_pipeline.py --query "Should GitLab enter automotive compliance?" --target "Azure DevOps"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
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
from pydantic import BaseModel, Field, ValidationError, field_validator

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


ParsedIntent.model_rebuild()


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

        raw = settings.LLM_API_KEY
        # Settings may declare the key as SecretStr or as a plain str; accept both.
        key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw
    except Exception as exc:  # noqa: BLE001 - configuration errors vary by type
        print(f"could not load configuration: {exc}", file=sys.stderr)
        return None
    return key if key and key.strip() else None


def parse_intent(query: str) -> ParsedIntent:
    from edrak.core.llm import get_llm_client
    from edrak.orchestration.planner import PlanningError
    from edrak.orchestration.prompts import REQUEST_BUILDER_SYSTEM_PROMPT

    llm = get_llm_client()
    content = (
        llm.chat_completion(
            [
                {"role": "system", "content": REQUEST_BUILDER_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            json_mode=True,
        )
        or ""
    ).strip()
    try:
        return ParsedIntent.model_validate_json(content)
    except ValidationError as exc:
        raise PlanningError(
            f"request builder returned unusable output: {exc.error_count()} error(s)",
            content,
        ) from exc


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
        description=(
            "Run EDRAK research: specialists gather evidence, verification "
            "checks claims against sources, then a briefing is printed."
        ),
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
        help="Print machine-readable JSON only (skips the briefing).",
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


_WIDTH = 72

_WORKER_GUIDE = {
    "internal_intelligence": (
        "Internal intelligence",
        "The company's own products, capabilities, and constraints",
    ),
    "competitor_intelligence": (
        "Competitor intelligence",
        "Rivals' products, pricing, positioning, and launches",
    ),
    "market_intelligence": (
        "Market intelligence",
        "Market size, demand, regulation, and economic signals",
    ),
    "customer_trends": (
        "Customer and trends intelligence",
        "Buyer needs, sentiment, and adoption",
    ),
}

_STATUS_GUIDE = {
    "completed": "Finished with usable findings",
    "partial": "Finished, but some questions are still open",
    "no_evidence": "Ran, but found no usable public evidence",
    "failed": "Did not complete",
}

_RUN_STATUS_GUIDE = {
    "completed": "Every assigned specialist finished without a hard failure.",
    "partial": "At least one specialist failed or left gaps. Findings from the others were still verified and linked.",
    "failed": "The run could not produce a usable result.",
}


def _rule(title: str) -> None:
    print()
    print("=" * _WIDTH)
    print(title)
    print("=" * _WIDTH)


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(
        " ".join(text.split()),
        width=_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _plain_error(error: str | None) -> str:
    if not error or error.strip().lower() in {"none", "null"}:
        return ""
    cleaned = error.strip().strip('"')
    if "no worker registered" in cleaned:
        return "This specialist is not wired into the pipeline yet."
    return cleaned


def print_run_intro() -> None:
    _rule("EDRAK  |  Evidence-backed research pipeline")
    print()
    print("  This system researches a business question with specialist agents,")
    print("  checks their claims against saved sources, then links verified")
    print("  claims across domains. It supports a human decision; it does not")
    print("  make the decision.")
    print()
    print("  Steps in this run:")
    print("    1. Turn the question into a structured research request")
    print("    2. Assign specialist agents (internal, competitor, market, customer)")
    print("    3. Research in parallel")
    print("    4. Verify each claim against the source text that was saved")
    print("    5. Link verified claims into cross-domain signals")
    print()
    print("  Progress lines are tagged so you can see who is speaking:")
    print("    [orchestrator]              control plane (plan, dispatch, finish)")
    print("    [competitor_intelligence]   rival products and positioning")
    print("    [market_intelligence]       market size, demand, regulation")
    print("    [internal_intelligence]     own-company capabilities")
    print("    [customer_trends]           buyers and adoption")
    print("    [verification]              source-check of each claim")
    print("    [cross_signal]              links among verified claims")


def render_request(request: BusinessRequest) -> None:
    context = request.business_context
    _rule("1. Research question")
    print()
    print(f"  Company     : {request.company_profile.name}")
    print(f"  Question    : {request.goal}")
    print(f"  Compared to : {', '.join(context.targets) or '(none named)'}")
    print(f"  Focus       : {', '.join(context.focus_areas) or '(none named)'}")
    if context.constraints:
        print(f"  Limits      : {'; '.join(context.constraints)}")
    if context.time_window_days:
        print(f"  Recency     : last {context.time_window_days} days")
    print(f"  Run id      : {request.request_id}")


def render_plan(plan: ResearchPlan) -> None:
    _rule("2. Specialist assignments")
    print()
    print("  A planner split the question. Each specialist receives one bounded")
    print("  task. They do not see each other's notes.")
    print()
    print(f"  Plan id: {plan.plan_id}   ({len(plan.tasks)} assignment(s))")
    for task in plan.tasks:
        name, role = _WORKER_GUIDE.get(
            task.worker.value,
            (task.worker.value, "Domain research"),
        )
        print()
        print(f"  [{task.worker.value}]  {name}")
        print(f"    Role  : {role}")
        print(_wrap(f"Goal  : {task.goal}"))
        print(_wrap(f"Focus : {task.focus}"))


def _render_worker_result(item: Any) -> None:
    name, _role = _WORKER_GUIDE.get(
        item.worker.value,
        (item.worker.value, ""),
    )
    status = item.status.value if hasattr(item.status, "value") else str(item.status)
    status_plain = _STATUS_GUIDE.get(status, status)
    n_findings = len(item.findings)
    n_evidence = len(item.evidence)
    print()
    print(f"  [{item.worker.value}]  {name}")
    print(f"    Outcome  : {status_plain} ({status})")
    print(f"    Claims   : {n_findings}   Sources: {n_evidence}")
    note = _plain_error(item.error)
    if note:
        print(_wrap(f"Note    : {note}"))
    for finding in item.findings[:3]:
        print(_wrap(f"- {finding.statement}"))
    extra = n_findings - min(n_findings, 3)
    if extra > 0:
        print(f"    … {extra} more claim(s) in the saved artifact")


def render_result(result: OrchestrationResult, artifact_path: Path | None = None) -> None:
    _rule("3. What the specialists returned")
    print()
    run_status = result.status.value
    print(f"  Overall run : {run_status}")
    meaning = _RUN_STATUS_GUIDE.get(run_status, "")
    if meaning:
        print(_wrap(meaning))
    if result.error:
        print(_wrap(f"Run error : {result.error}"))
    print()
    print("  A claim is a statement the specialist made. A source is the page")
    print("  or document saved as evidence for that claim. Verification later")
    print("  keeps only claims that match the saved text.")
    for item in result.results:
        _render_worker_result(item)
    findings = sum(len(item.findings) for item in result.results)
    evidence = sum(len(item.evidence) for item in result.results)
    print()
    print(f"  Totals: {findings} claims, {evidence} sources")

    _rule("4. Cross-domain signals")
    print()
    print("  After verification, a separate agent looks for relationships among")
    print("  claims that survived the source check (for example a market size")
    print("  figure that lines up with a competitor launch). These are observations,")
    print("  not recommendations.")
    if result.cross_signal is None:
        print()
        print("  [cross_signal] Did not run. That happens when verification does")
        print("  not finish as verified, or when no claim passed the source check.")
        _print_artifact_line(artifact_path)
        return

    signals = result.cross_signal.get("signals") or []
    cs_status = result.cross_signal.get("status", "unknown")
    print()
    print(f"  [cross_signal] {cs_status}   {len(signals)} signal(s)")
    if not signals:
        print("  No cross-domain relationships were produced from the verified claims.")
        _print_artifact_line(artifact_path)
        return
    for index, signal in enumerate(signals, start=1):
        kind = signal.get("signal_type") or ""
        title = signal.get("title") or f"Signal {index}"
        claim = signal.get("signal") or ""
        interpretation = signal.get("interpretation") or ""
        suffix = f"  [{kind}]" if kind else ""
        print()
        print(f"  [cross_signal] {index}. {title}{suffix}")
        if claim:
            print(_wrap(f"Claim: {claim}"))
        if interpretation:
            print(_wrap(f"Why it matters: {interpretation}"))
    _print_artifact_line(artifact_path)


def _print_artifact_line(
    artifact_path: Path | None,
    *,
    plan_only: bool = False,
) -> None:
    _rule("3. Full record" if plan_only else "5. Full record")
    print()
    if plan_only:
        print("  The JSON file below is the plan only. No research was run.")
    else:
        print("  The JSON file below is the complete run: plan, every claim,")
        print("  every source URL, verification grades, and cross-signal objects.")
        print("  Open it if you need IDs, excerpts, or to audit a claim.")
    print()
    if artifact_path:
        print(f"  [orchestrator] run artifact saved: {artifact_path}")
    else:
        print("  [orchestrator] No artifact was written (--no-out).")


def _render_human(
    request: BusinessRequest,
    payload: ResearchPlan | OrchestrationResult,
    artifact_path: Path | None = None,
) -> None:
    """Print a briefing a technical reader can follow without EDRAK background."""
    if isinstance(payload, OrchestrationResult):
        _rule("Run complete  |  Briefing")
        print()
        print("  The tagged lines above were live progress. The sections below")
        print("  recap the same run in order, with terms explained.")
    render_request(request)
    plan = payload if isinstance(payload, ResearchPlan) else payload.plan
    if plan is None:
        _rule("2. Specialist assignments")
        print()
        print("  (none)")
    else:
        render_plan(plan)
    if isinstance(payload, OrchestrationResult):
        render_result(payload, artifact_path=artifact_path)
    else:
        _print_artifact_line(artifact_path, plan_only=True)


def write_artifact(result: OrchestrationResult, suffix: str = "") -> Path | None:
    path = ARTIFACTS_DIR / f"{result.request_id}{suffix}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"  warning: could not write {path}: {exc}", file=sys.stderr)
        return None
    return path


def build_default_registry() -> WorkerRegistry:
    """Register the workers that are wired for this integration slice.

    Competitor intelligence and market intelligence already expose classes
    satisfying the ``Worker`` protocol (``CompetitorAgent`` and
    ``MarketIntelligence``), so both register directly.

    Internal intelligence is intentionally not registered: it needs a populated
    handbook vector store (``scripts/run_etl_pipeline.py``), and is deferred to a
    later slice. Customer trends is excluded because it carries private schemas
    that do not implement the shared contracts.

    Both imports are deferred. The competitor module raises at import time when
    OPENAI_API_KEY or TAVILY_API_KEY is absent, which would otherwise take down
    the whole run including workers that are perfectly usable.
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

    if not args.json:
        print_run_intro()

    if args.plan_only:
        from edrak.orchestration.planner import LlmPlanner

        try:
            plan = LlmPlanner().plan(request)
        except PlanningError as exc:
            print(f"planning failed: {exc.reason}", file=sys.stderr)
            if exc.raw_output:
                print(f"raw output: {exc.raw_output[:400]}", file=sys.stderr)
            return EXIT_FAILED

        out = None if args.no_out else write_artifact_plan(plan, request.request_id)
        if args.json:
            print(json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=False))
            if out:
                print(f"[orchestrator] run artifact saved: {out}", file=sys.stderr)
        else:
            _render_human(request, plan, artifact_path=out)
        return EXIT_COMPLETED

    from edrak.orchestration.graph import build_graph

    state = asyncio.run(
        build_graph(build_default_registry()).ainvoke({"request": request})
    )
    result = state["orchestration_result"]
    out = None if args.no_out else write_artifact(result)

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        if out:
            print(f"[orchestrator] run artifact saved: {out}", file=sys.stderr)
    else:
        _render_human(request, result, artifact_path=out)

    if result.status.value == "completed":
        return EXIT_COMPLETED
    if result.status.value == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())