"""Node functions of the worker graph (SPEC section 10).

Each node takes the graph state and the run's dependencies and returns a partial state update.
Nodes keep pointers in the state; evidence stays in the store. Code nodes call the processing
tools through the tool registry, and the model nodes (plan, branches, write) never see more than
previews and aggregates.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from functools import wraps
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Overwrite
from structlog.contextvars import bound_contextvars

from edrak.agents.customer_trends.assembly import build_result, writer_context
from edrak.agents.customer_trends.deps import WorkerDeps
from edrak.agents.customer_trends.gaps import (
    Gap,
    coverage_of,
    critical_gaps,
    find_gaps,
)
from edrak.agents.customer_trends.llm.client import structured_call
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.prompts import (
    BRANCH_DEMAND,
    BRANCH_REVIEWS,
    BRANCH_SOCIAL,
    PLAN_QUERIES,
    REPAIR_FINDINGS,
    REPLAN_NOTE,
    WRITE_FINDINGS,
    WRITE_HEADLINE,
    render,
)
from edrak.agents.customer_trends.schemas.common import Depth, Platform
from edrak.agents.customer_trends.schemas.findings import (
    MAX_HEADLINE_WORDS,
    Finding,
    FindingsDraft,
    HeadlineDraft,
)
from edrak.agents.customer_trends.schemas.task import PlanDraft, QueryPlan, TaskBrief
from edrak.agents.customer_trends.state import WorkerState, brief_of, gaps_of, plan_of
from edrak.agents.customer_trends.tools.registry import (
    build_tools,
    tools_for_branch,
    tools_for_stage,
)
from edrak.agents.customer_trends.tools.submit_findings import (
    check_finding,
    load_verdict_phrases,
    verdict_phrases_in,
)

log = get_logger(__name__)

Update = dict[str, Any]
NodeFn = Callable[[WorkerState, WorkerDeps], Awaitable[Update]]

ANALYSIS_ITEMS = {Depth.LIGHT: 50, Depth.STANDARD: 200, Depth.DEEP: 400}
BRANCH_PROMPTS = {"social": BRANCH_SOCIAL, "demand": BRANCH_DEMAND, "reviews": BRANCH_REVIEWS}
NOTE_CHARS = 500
BRANCH_RECURSION_PER_STEP = 6
DEFAULT_PLATFORMS = (Platform.REDDIT, Platform.X, Platform.YOUTUBE)
TOP_THEME_SAMPLES = 6
OVERALL_SAMPLES = 8
THEME_SAMPLE_ITEMS = 3
MAX_SHARE_OF_VOICE_NAMES = 10


def traced(name: str) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node so it reports `node_started` and `node_finished` and keeps both in the state."""

    def decorate(node: NodeFn) -> NodeFn:
        @wraps(node)
        async def run(state: WorkerState, deps: WorkerDeps) -> Update:
            started = time.perf_counter()
            events = [deps.bus.emit({"type": "node_started", "node": name})]
            try:
                with bound_contextvars(node=name):
                    update = await node(state, deps)
            except BaseException as exc:
                deps.bus.emit(
                    {
                        "type": "node_finished",
                        "node": name,
                        "status": "error",
                        "error": type(exc).__name__,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                raise
            events.append(
                deps.bus.emit(
                    {
                        "type": "node_finished",
                        "node": name,
                        "status": "ok",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
            )
            update["events"] = [*events, *update.get("events", [])]
            return update

        return run

    return decorate


def _budget(deps: WorkerDeps) -> dict[str, Any]:
    return deps.budget.snapshot().model_dump(mode="json")


async def call_tool(tool: StructuredTool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a registry tool and read its compact JSON answer."""
    data = json.loads(await tool.ainvoke(arguments))
    return data if isinstance(data, dict) else {}


def _prompt_values(brief: TaskBrief, deps: WorkerDeps) -> dict[str, Any]:
    config = deps.use_cases.for_use_case(brief.use_case)
    return {
        "entity": brief.entity,
        "question": brief.question,
        "market": brief.market,
        "geo": brief.geo,
        "languages": brief.languages,
        "competitors": brief.competitors,
        "focus": brief.focus,
        "since": brief.since,
        "until": brief.until,
        "depth": brief.depth,
        "emphasis": config.emphasis,
        "query_hints": config.query_hints,
        "target_items": config.min_evidence_total,
    }


# intake


@traced("intake")
async def intake(state: WorkerState, deps: WorkerDeps) -> Update:
    """Validate the brief, fill its focus from the use case and open the run in the store."""
    brief = brief_of(state)
    if not brief.focus:
        config = deps.use_cases.for_use_case(brief.use_case)
        brief = brief.model_copy(update={"focus": list(config.default_focus)})
    deps.store.create_run(brief.run_id, brief.task_id, brief)
    return {
        "brief": brief.model_dump(mode="json"),
        "replan_count": 0,
        "analysis_done": False,
        "budget": _budget(deps),
    }


# plan_queries


def default_plan(brief: TaskBrief) -> QueryPlan:
    """A plain plan from the brief, used when the planner model cannot answer."""
    names = [brief.entity, *brief.competitors]
    queries = [brief.entity, *[f"{brief.entity} vs {name}" for name in brief.competitors[:2]]]
    queries += brief.competitors[:3]
    return QueryPlan(
        social_queries={platform: queries for platform in DEFAULT_PLATFORMS},
        trend_keywords=names[:5],
        news_queries=names[:3],
        rationale="default plan built from the brief",
    )


def queries_run(plan: QueryPlan | None) -> str:
    """The queries of a plan, one line each, for the second planning pass."""
    if plan is None:
        return "none"
    lines = [
        f"- {platform.value}: {' | '.join(qs)}" for platform, qs in plan.social_queries.items()
    ]
    if plan.trend_keywords:
        lines.append(f"- search interest: {' | '.join(plan.trend_keywords)}")
    if plan.news_queries:
        lines.append(f"- news: {' | '.join(plan.news_queries)}")
    return "\n".join(lines) or "none"


@traced("plan_queries")
async def plan_queries(state: WorkerState, deps: WorkerDeps) -> Update:
    """Ask the planner model for a query plan; on a second pass, only to close the gaps."""
    brief = brief_of(state)
    replanning = bool(state.get("analysis_done"))
    values = _prompt_values(brief, deps)
    events: list[dict[str, Any]] = []
    replan = ""
    update: Update = {"replan_count": state.get("replan_count", 0)}
    if replanning:
        open_gaps = critical_gaps(gaps_of(state))
        replan = render(
            REPLAN_NOTE,
            {
                "gaps": "\n".join(f"- {g.description}: {g.suggested_action}" for g in open_gaps),
                "done": queries_run(plan_of(state)),
            },
        )
        update["replan_count"] = state.get("replan_count", 0) + 1
        events.append(
            deps.bus.emit(
                {
                    "type": "replan",
                    "replan_count": update["replan_count"],
                    "gap_ids": [gap.id for gap in open_gaps],
                }
            )
        )
    messages = [
        SystemMessage(content=render(PLAN_QUERIES, {**values, "replan": replan})),
        HumanMessage(content="Return the query plan for this task."),
    ]
    warnings: list[str] = []
    try:
        plan = (await structured_call(deps.model("planner"), PlanDraft, messages)).to_plan()
    except Exception as exc:
        log.warning("planner_failed", error=type(exc).__name__)
        warnings.append(f"the planner failed ({type(exc).__name__}); a default plan was used")
        plan = default_plan(brief)
    return {
        **update,
        "plan": plan.model_dump(mode="json"),
        "warnings": warnings,
        "events": events,
        "budget": _budget(deps),
    }


# the three collection branches


def plan_slice(branch: str, plan: QueryPlan) -> dict[str, Any]:
    """The part of the plan one branch works from; empty when it has nothing to do."""
    keys = {
        "social": {"social_queries", "hashtags", "competitor_angles"},
        "demand": {"trend_keywords", "news_queries", "competitor_angles"},
        "reviews": {"review_targets"},
    }[branch]
    data = plan.model_dump(mode="json", include=keys)
    work = {
        "social": bool(plan.social_queries),
        "demand": bool(plan.trend_keywords or plan.news_queries),
        "reviews": bool(plan.review_targets),
    }[branch]
    return data if work else {}


class BranchCapture:
    """Passes a branch's tool events on to the run and remembers what they say."""

    def __init__(self, deps: WorkerDeps) -> None:
        self._bus = deps.bus
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._bus(event)

    @property
    def batch_ids(self) -> list[str]:
        return list(dict.fromkeys(e["batch_id"] for e in self.events if e.get("batch_id")))

    def failure(self) -> str:
        """Why the branch collected nothing, or an empty string when something came back."""
        if not self.events:
            return "no tool was called"
        if any(e.get("status") in ("ok", "partial") for e in self.events):
            return ""
        codes = sorted({str(e.get("error_code")) for e in self.events})
        return f"all {len(self.events)} tool call(s) failed ({', '.join(codes)})"


def _final_note(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
            return message.content[:NOTE_CHARS]
    return "the branch gave no summary"


def gap_fields(gap: Mapping[str, Any]) -> dict[str, Any]:
    """A gap record without its id, for the `gap_found` event."""
    return {key: value for key, value in gap.items() if key != "id"}


def _branch_failed(name: str, reason: str) -> Update:
    gap = Gap(
        id=f"branch_{name}",
        severity="critical",
        description=f"the {name} branch reported an error: {reason}",
        suggested_action=f"retry the {name} collection with narrower, simpler requests",
    )
    return {"branch_errors": {name: reason}, "gaps": [gap.model_dump()]}


async def run_branch(name: str, state: WorkerState, deps: WorkerDeps) -> Update:
    """A bounded tool-using sub-agent of the worker graph. It returns a note and batch ids, and a
    failure becomes a gap, never an exception."""
    brief, plan = brief_of(state), plan_of(state)
    work = plan_slice(name, plan) if plan else {}
    if not work:
        return {
            "batches": {name: []},
            "branch_notes": {name: "nothing was planned for this branch"},
            "branch_errors": {name: ""},
        }
    capture = BranchCapture(deps)
    ctx = replace(deps.tool_context(brief), emit=capture)
    steps = deps.settings.branch_max_steps
    system = render(BRANCH_PROMPTS[name], {**_prompt_values(brief, deps), "max_steps": steps})
    request = "Collect evidence for this task, working from this plan:\n" + json.dumps(
        work, ensure_ascii=False
    )
    note, reason = "", ""
    try:
        agent = create_agent(
            deps.model("branch"),
            tools_for_branch(name, ctx),
            system_prompt=system,
            middleware=[ModelCallLimitMiddleware(run_limit=steps, exit_behavior="end")],
            checkpointer=False,
        )
        async with asyncio.timeout(deps.settings.branch_timeout_s) as deadline:
            out = await agent.ainvoke(
                {"messages": [HumanMessage(content=request)]},
                config={"recursion_limit": steps * BRANCH_RECURSION_PER_STEP + 10},
            )
        if deadline.expired():
            # the agent turned the cancellation into a normal end, so say it was the clock
            raise TimeoutError
        note = _final_note(out["messages"])
        reason = capture.failure()
    except TimeoutError:
        log.warning("branch_timed_out", branch=name)
        reason = f"the branch did not finish in {deps.settings.branch_timeout_s:g} s"
    except Exception as exc:
        log.warning("branch_failed", branch=name, error=type(exc).__name__)
        reason = f"{type(exc).__name__}: {exc}"[:NOTE_CHARS]
    update: Update = {
        "batches": {name: capture.batch_ids},
        "branch_notes": {name: note or f"stopped early: {reason}"},
        "branch_errors": {name: reason},
    }
    if reason:
        failed = _branch_failed(name, reason)
        gap = failed["gaps"][0]
        update["gaps"] = failed["gaps"]
        update["events"] = [
            deps.bus.emit({"type": "gap_found", "gap_id": gap["id"], **gap_fields(gap)})
        ]
    return update


@traced("social")
async def social(state: WorkerState, deps: WorkerDeps) -> Update:
    return await run_branch("social", state, deps)


@traced("demand")
async def demand(state: WorkerState, deps: WorkerDeps) -> Update:
    return await run_branch("demand", state, deps)


@traced("reviews")
async def reviews(state: WorkerState, deps: WorkerDeps) -> Update:
    return await run_branch("reviews", state, deps)


# join


@traced("join")
async def join(state: WorkerState, deps: WorkerDeps) -> Update:
    """The reducers have merged the branches' batch ids and notes; record where the run stands."""
    return {"budget": _budget(deps)}


# analyze


@traced("analyze")
async def analyze(state: WorkerState, deps: WorkerDeps) -> Update:
    """Theme analysis over the social and review batches, then the metrics, through the tools."""
    brief = brief_of(state)
    tools = build_tools(deps.tool_context(brief))
    batches = state.get("batches", {})
    text_batches = list(dict.fromkeys([*batches.get("social", []), *batches.get("reviews", [])]))
    if not text_batches:
        # A run started again over stored evidence collects only duplicates, so its branches
        # name no batch; the run's own batches are still there to analyze.
        text_batches = deps.store.run_summary(brief.run_id).batch_ids
    warnings: list[str] = []
    if text_batches:
        answer = await call_tool(
            tools["analyze_text"],
            {"batch_ids": text_batches, "max_items": ANALYSIS_ITEMS[brief.depth]},
        )
        if answer.get("status") == "error":
            warnings.append(f"theme analysis failed: {'; '.join(answer.get('gaps', []))}")
        else:
            warnings.extend(answer.get("warnings", []))
    else:
        warnings.append("theme analysis skipped: no social or review batch was collected")

    requests: dict[str, dict[str, Any]] = {
        name: {"metric": name}
        for name in ("volume_over_time", "platform_mix", "language_mix", "engagement_stats")
    }
    for series in deps.store.get_trend_series(brief.run_id):
        requests[f"trend_growth:{series.keyword}"] = {
            "metric": "trend_growth",
            "params": {"keyword": series.keyword},
        }
    if brief.competitors:
        requests["share_of_voice"] = {
            "metric": "share_of_voice",
            "params": {
                "entity": brief.entity,
                "competitors": brief.competitors[:MAX_SHARE_OF_VOICE_NAMES],
            },
        }
    metric_ids = dict(state.get("metric_ids", {}))
    for name, arguments in requests.items():
        answer = await call_tool(tools["compute_metrics"], arguments)
        if answer.get("metric_id"):
            metric_ids[name] = answer["metric_id"]
        elif answer.get("error_code") != "no_data":
            warnings.append(f"metric {name} failed: {'; '.join(answer.get('gaps', []))}")
    return {
        "metric_ids": metric_ids,
        "analysis_done": True,
        "warnings": warnings,
        "budget": _budget(deps),
    }


# gap_check


@traced("gap_check")
async def gap_check(state: WorkerState, deps: WorkerDeps) -> Update:
    """Apply the coverage rules; report each new gap. The gap list is rebuilt on every check."""
    brief = brief_of(state)
    plan = plan_of(state)
    gaps = find_gaps(
        brief,
        deps.use_cases.for_use_case(brief.use_case),
        coverage_of(deps.store, brief.run_id),
        review_targets_planned=bool(plan and plan.review_targets),
        branch_errors=state.get("branch_errors", {}),
    )
    known = {gap["id"] for gap in state.get("gaps", [])}
    events = [
        deps.bus.emit({"type": "gap_found", "gap_id": g.id, **gap_fields(g.model_dump())})
        for g in gaps
        if g.id not in known
    ]
    return {
        "gaps": Overwrite([gap.model_dump() for gap in gaps]),
        "events": events,
        "budget": _budget(deps),
    }


# write_findings


async def _samples(
    tools: Mapping[str, StructuredTool], deps: WorkerDeps, run_id: str
) -> dict[str, list[dict[str, Any]]]:
    """The most engaged items overall and for each of the biggest themes, through evidence_query."""
    query = tools["evidence_query"]
    samples = {
        "most engaged overall": (
            await call_tool(query, {"filters": {}, "limit": OVERALL_SAMPLES, "sample": "top"})
        ).get("items", [])
    }
    for theme in deps.store.get_aggregates(run_id)[:TOP_THEME_SAMPLES]:
        answer = await call_tool(
            query,
            {"filters": {"theme": theme.theme_label}, "limit": THEME_SAMPLE_ITEMS, "sample": "top"},
        )
        samples[f"theme: {theme.theme_label}"] = answer.get("items", [])
    return samples


async def _repair(
    deps: WorkerDeps,
    values: dict[str, Any],
    context: dict[str, Any],
    rejected: list[tuple[Finding, list[str]]],
) -> list[Finding]:
    report = [
        {"finding": finding.model_dump(mode="json"), "reasons": reasons}
        for finding, reasons in rejected
    ]
    messages = [
        SystemMessage(content=render(REPAIR_FINDINGS, values)),
        HumanMessage(
            content="CONTEXT:\n"
            + json.dumps(context, ensure_ascii=False)
            + "\n\nREJECTED:\n"
            + json.dumps(report, ensure_ascii=False)
        ),
    ]
    return (await structured_call(deps.model("writer"), FindingsDraft, messages)).findings


@traced("write_findings")
async def write_findings(state: WorkerState, deps: WorkerDeps) -> Update:
    """The writer model drafts findings from aggregates and samples; code checks every one."""
    brief = brief_of(state)
    values = _prompt_values(brief, deps)
    tools = {t.name: t for t in tools_for_stage("write", deps.tool_context(brief))}
    context = writer_context(
        deps,
        brief,
        state.get("metric_ids", {}),
        await _samples(tools, deps, brief.run_id),
        gaps_of(state),
    )
    messages = [
        SystemMessage(content=render(WRITE_FINDINGS, values)),
        HumanMessage(content="CONTEXT:\n" + json.dumps(context, ensure_ascii=False)),
    ]
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    try:
        drafted = (await structured_call(deps.model("writer"), FindingsDraft, messages)).findings
    except Exception as exc:
        log.warning("writer_failed", error=type(exc).__name__)
        return {
            "findings": [],
            "warnings": [f"the writer failed ({type(exc).__name__}); no findings were written"],
        }

    phrases = load_verdict_phrases()
    accepted: list[Finding] = []
    rejected: list[tuple[Finding, list[str]]] = []
    for finding in drafted:
        reasons = check_finding(finding, deps.store, brief.run_id, phrases)
        if reasons:
            rejected.append((finding, reasons))
        else:
            accepted.append(finding)
    if rejected:
        try:
            repaired = await _repair(deps, values, context, rejected)
        except Exception as exc:
            log.warning("repair_failed", error=type(exc).__name__)
            repaired = []
            warnings.append(f"the repair pass failed ({type(exc).__name__})")
        reasons_by_id = {finding.id: reasons for finding, reasons in rejected}
        for finding in repaired:
            if finding.id not in reasons_by_id:
                continue
            reasons = check_finding(finding, deps.store, brief.run_id, phrases)
            if reasons:
                reasons_by_id[finding.id] = reasons
            else:
                accepted.append(finding)
                del reasons_by_id[finding.id]
        for finding_id, reasons in reasons_by_id.items():
            warnings.append(f"finding {finding_id} was dropped: {'; '.join(reasons)}")
            events.append(
                deps.bus.emit(
                    {"type": "finding_rejected", "finding_id": finding_id, "reasons": reasons}
                )
            )
    findings = [
        f.model_copy(
            update={
                "id": f"f{number}",
                "use_case_relevance": f.use_case_relevance or [brief.use_case],
            }
        )
        for number, f in enumerate(accepted, start=1)
    ]
    return {
        "findings": [f.model_dump(mode="json") for f in findings],
        "warnings": warnings,
        "events": events,
    }


# submit


async def _headline(deps: WorkerDeps, brief: TaskBrief, context: dict[str, Any]) -> str | None:
    """The writer model's headline, or None when it fails or breaks the rules for one."""
    messages = [
        SystemMessage(content=render(WRITE_HEADLINE, _prompt_values(brief, deps))),
        HumanMessage(content="CONTEXT:\n" + json.dumps(context, ensure_ascii=False)),
    ]
    try:
        headline = (await structured_call(deps.model("writer"), HeadlineDraft, messages)).headline
    except Exception as exc:
        log.warning("headline_failed", error=type(exc).__name__)
        return None
    headline = headline.strip()
    too_long = len(headline.split()) > MAX_HEADLINE_WORDS
    if too_long or verdict_phrases_in(headline, load_verdict_phrases()):
        return None
    return headline


@traced("submit")
async def submit(state: WorkerState, deps: WorkerDeps) -> Update:
    """Store the findings through submit_findings, write the result through the sink."""
    brief = brief_of(state)
    tools = build_tools(deps.tool_context(brief))
    warnings = list(state.get("warnings", []))
    events: list[dict[str, Any]] = []
    if state.get("findings"):
        answer = await call_tool(tools["submit_findings"], {"findings": state["findings"]})
        if answer.get("status") == "error":
            warnings.append(f"submit_findings failed: {'; '.join(answer.get('gaps', []))}")
        for finding_id in answer.get("accepted", []):
            events.append(deps.bus.emit({"type": "finding_accepted", "finding_id": finding_id}))
        for rejection in answer.get("rejected", []):
            warnings.append(
                f"finding {rejection['id']} was rejected: {'; '.join(rejection['reasons'])}"
            )
            events.append(
                deps.bus.emit(
                    {
                        "type": "finding_rejected",
                        "finding_id": rejection["id"],
                        "reasons": rejection["reasons"],
                    }
                )
            )
    gaps = gaps_of(state)
    context = writer_context(deps, brief, state.get("metric_ids", {}), {}, gaps)
    headline = await _headline(deps, brief, context)
    result = build_result(deps, brief, gaps=gaps, warnings=warnings, headline=headline)
    deps.sink.write_result(result)
    return {
        "result": result.model_dump(mode="json"),
        "warnings": warnings,
        "events": events,
        "budget": _budget(deps),
    }
