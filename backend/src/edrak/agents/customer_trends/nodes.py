"""Node functions of the worker graph (SPEC section 10).

Each node takes the graph state and the run's dependencies and returns a partial state update.
Nodes keep pointers in the state; evidence stays in the store. Code nodes call the processing
tools through the tool registry, and the model nodes (plan, branches, write) never see more than
previews and aggregates.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Overwrite

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
from edrak.agents.customer_trends.tracing import save_customer_span

log = get_logger(__name__)

Update = dict[str, Any]
NodeFn = Callable[[WorkerState, WorkerDeps], Awaitable[Update]]

ANALYSIS_ITEMS = {Depth.LIGHT: 50, Depth.STANDARD: 200, Depth.DEEP: 400}
BRANCH_PROMPTS = {"social": BRANCH_SOCIAL, "demand": BRANCH_DEMAND, "reviews": BRANCH_REVIEWS}
NOTE_CHARS = 500
BRANCH_RECURSION_PER_STEP = 6
MIN_BRANCH_S = 10  # a branch with less time than this does not start
DEFAULT_PLATFORMS = (Platform.REDDIT, Platform.X, Platform.YOUTUBE)
TOP_THEME_SAMPLES = 6
OVERALL_SAMPLES = 8
THEME_SAMPLE_ITEMS = 3
MAX_SHARE_OF_VOICE_NAMES = 10


def _budget(deps: WorkerDeps) -> dict[str, Any]:
    return deps.budget.snapshot().model_dump(mode="json")


from deepeval.tracing import observe, update_current_span


@observe(
    type="tool",
    name="worker_tool_call",
    description="Executes a registered Edrak data-collection or analysis tool."
)
async def call_tool(
    tool: StructuredTool,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    span_input = {"tool_name": tool.name, "arguments": arguments}
    update_current_span(input=span_input)

    try:
        raw = await tool.ainvoke(arguments)
        data = json.loads(raw)
        result = data if isinstance(data, dict) else {}
        span_output = {
            "tool_name": tool.name,
            "status": result.get("status", "unknown"),
            "batch_id": result.get("batch_id"),
            "item_count": len(result.get("items", [])),
            "error_code": result.get("error_code"),
            "gaps": result.get("gaps", []),
        }
        update_current_span(output=span_output)
        save_customer_span(
            "customer_trends.worker_tool_call",
            input=span_input,
            output=span_output,
            status=str(span_output["status"]),
        )
        return result

    except Exception as exc:
        span_output = {
            "tool_name": tool.name,
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
        update_current_span(output=span_output)
        save_customer_span(
            "customer_trends.worker_tool_call",
            input=span_input,
            output=span_output,
            status="error",
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        raise


def _prompt_values(brief: TaskBrief, deps: WorkerDeps) -> dict[str, Any]:
    config = deps.use_cases.for_use_case(brief.use_case)
    return {
        "entity": brief.entity,
        "question": brief.question,
        "market": brief.market,
        "geo": brief.geo,
        "languages": brief.languages,
        "competitors": brief.competitors,
        "offering": brief.offering,
        "accounts": "; ".join(f"{a.owner} on {a.platform.value}" for a in brief.accounts) or "none",
        "focus": brief.focus,
        "since": brief.since,
        "until": brief.until,
        "depth": brief.depth,
        "emphasis": config.emphasis,
        "query_hints": config.query_hints,
        "target_items": config.min_evidence_total,
    }


# intake


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


@observe(
    type="agent",
    name="customer_trends.plan_queries",
    description="Plans collection queries and generates follow-up queries to close critical evidence gaps.",
)
async def plan_queries(
    state: WorkerState,
    deps: WorkerDeps,
) -> Update:
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

    span_input = {
        "run_id": brief.run_id,
        "task_id": brief.task_id,
        "entity": brief.entity,
        "question": brief.question,
        "use_case": str(brief.use_case),
        "replanning": replanning,
        "previous_plan": queries_run(plan_of(state)) if replanning else None,
        "critical_gaps": [
            {
                "id": gap.id,
                "description": gap.description,
                "suggested_action": gap.suggested_action,
            }
            for gap in critical_gaps(gaps_of(state))
        ] if replanning else [],
    }
    span_metadata = {"worker": "customer_trends", "stage": "planning"}
    update_current_span(input=span_input, metadata=span_metadata)
    try:
        plan = (await structured_call(deps.model("planner"), PlanDraft, messages)).to_plan()
    except Exception as exc:
        log.warning("planner_failed", error=type(exc).__name__)
        warnings.append(f"the planner failed ({type(exc).__name__}); a default plan was used")
        plan = default_plan(brief)
    span_output = {
        "status": "completed",
        "used_default_plan": bool(warnings),
        "social_queries": {
            platform.value: queries
            for platform, queries in plan.social_queries.items()
        },
        "trend_keywords": plan.trend_keywords,
        "news_queries": plan.news_queries,
        "review_targets": plan.model_dump(mode="json").get("review_targets", []),
        "rationale": plan.rationale,
        "warnings": warnings,
        "replan_count": update.get("replan_count", state.get("replan_count", 0)),
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.plan_queries",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="completed",
    )
    return {
        **update,
        "plan": plan.model_dump(mode="json"),
        "warnings": warnings,
        "events": events,
        "budget": _budget(deps),
    }


# the three collection branches


def trend_timeframe(brief: TaskBrief) -> str:
    """The Google Trends period that covers the task's time window."""
    if brief.since is None:
        return "today 12-m"
    days = ((brief.until or datetime.now(UTC).date()) - brief.since).days
    if days <= 31:
        return "today 1-m"
    if days <= 93:
        return "today 3-m"
    if days <= 366:
        return "today 12-m"
    return "today 5-y"


def trend_keywords(brief: TaskBrief, plan: QueryPlan | None) -> list[str]:
    """The planner's trend keywords, or the company, the offering and the competitors."""
    if plan and plan.trend_keywords:
        return list(plan.trend_keywords)[:5]
    names = [brief.entity, *([brief.offering] if brief.offering else []), *brief.competitors]
    return list(dict.fromkeys(n for n in names if n))[:5]


TREND_TIMEOUT_S = 75


def _search_interest_timeout(deps: WorkerDeps) -> float:
    """Bound trend collection by both its own limit and the run's collection window."""
    return max(0.0, min(TREND_TIMEOUT_S, deps.budget.collection_seconds_left()))


async def collect_search_interest(brief: TaskBrief, plan: QueryPlan | None, deps: WorkerDeps, capture: Any) -> str:
    """Search interest over time, always, through Apify Google Trends first.

    Run by the worker itself before the demand branch's model starts, so demand data never depends
    on the model choosing the tool, and with its own clock, because the browser actor is slow.
    """
    keywords = trend_keywords(brief, plan)
    if not keywords:
        return "no trend keywords"
    timeout_s = _search_interest_timeout(deps)
    if timeout_s <= 0:
        return "no collection time remained for search interest"
    tools = build_tools(replace(deps.tool_context(brief), emit=capture).for_branch("demand"))
    try:
        async with asyncio.timeout(timeout_s):
            answer = await call_tool(
                tools["search_interest"],
                {"keywords": keywords, "timeframe": trend_timeframe(brief), "geo": brief.geo},
            )
    except TimeoutError:
        return f"search interest for {', '.join(keywords)} did not arrive in {timeout_s:g} s"
    if answer.get("status") == "error":
        return f"search interest failed: {'; '.join(answer.get('gaps', [])) or answer.get('error_code', '')}"
    return (
        f"search interest for {', '.join(keywords)} is already collected via {answer.get('provider_used', 'a provider')} "
        f"(timeframe {trend_timeframe(brief)}); do not call search_interest again for these keywords"
    )


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
    if name == "demand":
        work = {**work, "already_collected": await collect_search_interest(brief, plan, deps, capture)}
    steps = deps.settings.branch_max_steps
    system = render(BRANCH_PROMPTS[name], {**_prompt_values(brief, deps), "max_steps": steps})
    request = "Collect evidence for this task, working from this plan:\n" + json.dumps(
        work, ensure_ascii=False
    )
    note, reason = "", ""
    time_left = deps.budget.collection_seconds_left()
    allowed = min(deps.settings.branch_timeout_s, time_left)
    try:
        if time_left < MIN_BRANCH_S:
            raise TimeoutError
        agent = create_agent(
            deps.model("branch"),
            tools_for_branch(name, ctx),
            system_prompt=system,
            middleware=[ModelCallLimitMiddleware(run_limit=steps, exit_behavior="end")],
            checkpointer=False,
        )
        async with asyncio.timeout(allowed) as deadline:
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
        reason = (
            f"the branch did not finish in {allowed:.3g} s"
            if time_left >= MIN_BRANCH_S
            else "no time was left for collection before the time kept for the findings"
        )
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


ACCOUNT_POSTS = 15
ACCOUNT_REPLIES = 20
ACCOUNT_PARALLEL = 3


async def accounts(state: WorkerState, deps: WorkerDeps) -> Update:
    """Read the official accounts of the company and its competitors, and the replies under each
    account's most engaged post. Deterministic: every account in the brief is read."""
    brief = brief_of(state)
    if state.get("account_batches"):
        return {}  # read once per run; a replan does not read the same accounts again
    if not brief.accounts:
        return {
            "batches": {"accounts": []},
            "branch_notes": {"accounts": "no official accounts were given"},
            "branch_errors": {"accounts": ""},
        }
    capture = BranchCapture(deps)
    tools = build_tools(replace(deps.tool_context(brief), emit=capture).for_branch("accounts"))
    gate = asyncio.Semaphore(ACCOUNT_PARALLEL)
    by_owner: dict[str, list[str]] = {}
    read: list[str] = []

    async def one(account: Any) -> None:
        async with gate:
            if deps.budget.collection_seconds_left() < MIN_BRANCH_S:
                return
            answer = await call_tool(
                tools["account_posts"],
                {"platform": account.platform.value, "account_url": account.url, "owner": account.owner,
                 "max_results": ACCOUNT_POSTS},
            )
            batch = answer.get("batch_id")
            if not batch:
                return
            by_owner.setdefault(account.owner, []).append(batch)
            read.append(f"{account.owner} on {account.platform.value}")
            top = await call_tool(
                tools["evidence_query"], {"filters": {"batch_ids": [batch]}, "limit": 1, "sample": "top"}
            )
            post = next((i.get("url") for i in top.get("items", []) if i.get("url")), None)
            if post:
                replies = await call_tool(
                    tools["social_comments"],
                    {"platform": account.platform.value, "post_url": post, "sort": "top",
                     "max_results": ACCOUNT_REPLIES},
                )
                if replies.get("batch_id"):
                    by_owner[account.owner].append(replies["batch_id"])

    allowed = min(deps.settings.branch_timeout_s * 1.5, deps.budget.collection_seconds_left())
    reason = ""
    try:
        if allowed < MIN_BRANCH_S:
            raise TimeoutError
        async with asyncio.timeout(allowed):
            await asyncio.gather(*(one(a) for a in brief.accounts))
    except TimeoutError:
        reason = "the official accounts could not all be read in time"
    if not read and not reason:
        reason = capture.failure()
    return {
        "batches": {"accounts": [b for ids in by_owner.values() for b in ids]},
        "account_batches": by_owner,
        "branch_notes": {
            "accounts": f"read {len(read)} official account(s): {', '.join(read)}" if read
            else "no posts came back from the official accounts"
        },
        "branch_errors": {"accounts": reason},
    }



@observe(
    type="agent",
    name="customer_trends.social",
    description="Collects social platform evidence using the social branch plan and registered tools.",
)
async def social(state: WorkerState, deps: WorkerDeps) -> Update:
    brief = brief_of(state)
    plan = plan_of(state)

    span_input = {
        "run_id": brief.run_id,
        "entity": brief.entity,
        "question": brief.question,
        "planned_queries": (
            plan.model_dump(mode="json").get("social_queries", {}) if plan else {}
        ),
        "platforms": [platform.value for platform in plan.social_queries] if plan else [],
    }
    span_metadata = {"worker": "customer_trends", "branch": "social"}
    update_current_span(input=span_input, metadata=span_metadata)

    result = await run_branch("social", state, deps)

    span_output = {
        "branch": "social",
        "batch_ids": result.get("batches", {}).get("social", []),
        "note": result.get("branch_notes", {}).get("social"),
        "error": result.get("branch_errors", {}).get("social"),
        "gap_count": len(result.get("gaps", [])),
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.social",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="error" if span_output["error"] else "completed",
    )
    return result


@observe(
    type="agent",
    name="customer_trends.demand",
    description="Collects search-interest and market-demand evidence using the demand branch.",
)
async def demand(state: WorkerState, deps: WorkerDeps) -> Update:
    brief = brief_of(state)
    plan = plan_of(state)

    span_input = {
        "run_id": brief.run_id,
        "entity": brief.entity,
        "question": brief.question,
        "trend_keywords": plan.trend_keywords if plan else [],
        "news_queries": plan.news_queries if plan else [],
        "timeframe": trend_timeframe(brief),
        "geo": brief.geo,
    }
    span_metadata = {"worker": "customer_trends", "branch": "demand"}
    update_current_span(input=span_input, metadata=span_metadata)

    result = await run_branch("demand", state, deps)

    span_output = {
        "branch": "demand",
        "batch_ids": result.get("batches", {}).get("demand", []),
        "note": result.get("branch_notes", {}).get("demand"),
        "error": result.get("branch_errors", {}).get("demand"),
        "gap_count": len(result.get("gaps", [])),
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.demand",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="error" if span_output["error"] else "completed",
    )
    return result


@observe(
    type="agent",
    name="customer_trends.reviews",
    description="Collects customer review evidence using the review targets from the plan.",
)
async def reviews(state: WorkerState, deps: WorkerDeps) -> Update:
    brief = brief_of(state)
    plan = plan_of(state)

    span_input = {
        "run_id": brief.run_id,
        "entity": brief.entity,
        "question": brief.question,
        "review_targets": (
            plan.model_dump(mode="json").get("review_targets", []) if plan else []
        ),
    }
    span_metadata = {"worker": "customer_trends", "branch": "reviews"}
    update_current_span(input=span_input, metadata=span_metadata)

    result = await run_branch("reviews", state, deps)

    span_output = {
        "branch": "reviews",
        "batch_ids": result.get("batches", {}).get("reviews", []),
        "note": result.get("branch_notes", {}).get("reviews"),
        "error": result.get("branch_errors", {}).get("reviews"),
        "gap_count": len(result.get("gaps", [])),
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.reviews",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="error" if span_output["error"] else "completed",
    )
    return result


async def join(state: WorkerState, deps: WorkerDeps) -> Update:
    """The reducers have merged the branches' batch ids and notes; record where the run stands."""
    return {"budget": _budget(deps)}


# analyze


@observe(
    type="agent",
    name="customer_trends.analyze",
    description="Analyzes collected evidence and computes trend, engagement, and share-of-voice metrics.",
)
async def analyze(state: WorkerState, deps: WorkerDeps) -> Update:

    """Theme analysis over the social and review batches, then the metrics, through the tools."""
    brief = brief_of(state)
    tools = build_tools(deps.tool_context(brief))
    batches = state.get("batches", {})
    text_batches = list(dict.fromkeys(
        [*batches.get("social", []), *batches.get("reviews", []), *batches.get("accounts", [])]
    ))
    if not text_batches:
        # A run started again over stored evidence collects only duplicates, so its branches
        # name no batch; the run's own batches are still there to analyze.
        text_batches = deps.store.run_summary(brief.run_id).batch_ids
    trend_series = deps.store.get_trend_series(brief.run_id)
    span_input = {
        "run_id": brief.run_id,
        "entity": brief.entity,
        "depth": str(brief.depth),
        "text_batch_ids": text_batches,
        "social_batch_count": len(batches.get("social", [])),
        "review_batch_count": len(batches.get("reviews", [])),
        "account_batch_count": len(batches.get("accounts", [])),
        "trend_series": [series.keyword for series in trend_series],
    }
    span_metadata = {"worker": "customer_trends", "stage": "analysis"}
    update_current_span(input=span_input, metadata=span_metadata)
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

    run_summary = deps.store.run_summary(brief.run_id)
    has_evidence = run_summary.evidence_count > 0
    requests: dict[str, dict[str, Any]] = {}
    if has_evidence:
        requests.update({
            name: {"metric": name}
            for name in ("volume_over_time", "platform_mix", "language_mix", "engagement_stats")
        })
    for series in trend_series:
        requests[f"trend_growth:{series.keyword}"] = {
            "metric": "trend_growth",
            "params": {"keyword": series.keyword},
        }
    if has_evidence and brief.competitors:
        requests["share_of_voice"] = {
            "metric": "share_of_voice",
            "params": {
                "entity": brief.entity,
                "competitors": brief.competitors[:MAX_SHARE_OF_VOICE_NAMES],
            },
        }
    # Engagement on each company's own accounts, so findings can compare them with numbers.
    if has_evidence:
        for owner, ids in (state.get("account_batches") or {}).items():
            if ids:
                requests[f"account_engagement:{owner}"] = {
                    "metric": "engagement_stats",
                    "params": {"filters": {"batch_ids": ids}},
                }
    if not requests:
        warnings.append(
            "metrics skipped: no collected evidence or search-interest series are available"
        )
    metric_ids = dict(state.get("metric_ids", {}))
    for name, arguments in requests.items():
        answer = await call_tool(tools["compute_metrics"], arguments)
        if answer.get("metric_id"):
            metric_ids[name] = answer["metric_id"]
        elif answer.get("error_code") != "no_data":
            warnings.append(f"metric {name} failed: {'; '.join(answer.get('gaps', []))}")

    span_output = {
        "status": "completed",
        "analyzed_batch_ids": text_batches,
        "metric_ids": metric_ids,
        "requested_metric_count": len(requests),
        "computed_metric_count": len(metric_ids),
        "metrics_requested": list(requests.keys()),
        "warnings": warnings,
        "analysis_done": True,
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.analyze",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="completed",
    )
    return {
        "metric_ids": metric_ids,
        "analysis_done": True,
        "warnings": warnings,
        "budget": _budget(deps),
    }


# gap_check


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



@observe(
    type="agent",
    name="customer_trends.write_findings",
    description=(
        "Drafts findings from evidence and metrics, validates claims, "
        "and repairs rejected findings."
    ),
)
async def write_findings(
    state: WorkerState,
    deps: WorkerDeps,
) -> Update:
    """The writer model drafts findings from aggregates and samples; code checks every one."""

    brief = brief_of(state)
    values = _prompt_values(brief, deps)

    tools = {
        t.name: t
        for t in tools_for_stage(
            "write",
            deps.tool_context(brief),
        )
    }

    context = writer_context(
        deps,
        brief,
        state.get("metric_ids", {}),
        await _samples(tools, deps, brief.run_id),
        gaps_of(state),
    )

    messages = [
        SystemMessage(
            content=render(WRITE_FINDINGS, values)
        ),
        HumanMessage(
            content="CONTEXT:\n"
            + json.dumps(context, ensure_ascii=False)
        ),
    ]

    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    reasons_by_id: dict[str, list[str]] = {}

    # Trace the information provided to the writer.
    span_input = {
        "run_id": brief.run_id,
        "entity": brief.entity,
        "question": brief.question,
        "metric_ids": state.get("metric_ids", {}),
        "gap_count": len(gaps_of(state)),
        "evidence_sample_groups": list(context.keys()),
        "context_keys": list(context.keys()),
    }
    span_metadata = {
        "worker": "customer_trends",
        "stage": "finding_generation",
    }
    update_current_span(input=span_input, metadata=span_metadata)

    # Step 1: Generate findings using the writer model.
    try:
        draft_result = await structured_call(
            deps.model("writer"),
            FindingsDraft,
            messages,
        )
        drafted = draft_result.findings

    except Exception as exc:
        log.warning(
            "writer_failed",
            error=type(exc).__name__,
        )

        warning = (
            f"the writer failed ({type(exc).__name__}); "
            "no findings were written"
        )

        span_output = {
            "status": "writer_failed",
            "error_type": type(exc).__name__,
            "drafted_count": 0,
            "accepted_count": 0,
            "warnings": [warning],
        }
        update_current_span(output=span_output)
        save_customer_span(
            "customer_trends.write_findings",
            input=span_input,
            output=span_output,
            metadata=span_metadata,
            status="error",
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )

        return {
            "findings": [],
            "warnings": [warning],
        }

    # Step 2: Validate each drafted finding.
    phrases = load_verdict_phrases()

    accepted: list[Finding] = []
    rejected: list[tuple[Finding, list[str]]] = []

    for finding in drafted:
        reasons = check_finding(
            finding,
            deps.store,
            brief.run_id,
            phrases,
        )

        if reasons:
            rejected.append((finding, reasons))
        else:
            accepted.append(finding)

    initially_accepted_count = len(accepted)

    # Step 3: Attempt to repair rejected findings.
    if rejected:
        # Preserve the original rejection reasons for tracing and repair.
        reasons_by_id = {
            finding.id: reasons
            for finding, reasons in rejected
        }

        try:
            repaired = await _repair(
                deps,
                values,
                context,
                rejected,
            )

        except Exception as exc:
            log.warning(
                "repair_failed",
                error=type(exc).__name__,
            )

            repaired = []

            warnings.append(
                f"the repair pass failed ({type(exc).__name__})"
            )

        # Validate repaired findings again.
        for finding in repaired:
            if finding.id not in reasons_by_id:
                continue

            reasons = check_finding(
                finding,
                deps.store,
                brief.run_id,
                phrases,
            )

            if reasons:
                # Repair did not resolve the validation issues.
                reasons_by_id[finding.id] = reasons
            else:
                # The repaired finding passed validation.
                accepted.append(finding)
                del reasons_by_id[finding.id]

        # Record findings that remain rejected after repair.
        for finding_id, reasons in reasons_by_id.items():
            warnings.append(
                f"finding {finding_id} was dropped: "
                f"{'; '.join(reasons)}"
            )

            events.append(
                deps.bus.emit(
                    {
                        "type": "finding_rejected",
                        "finding_id": finding_id,
                        "reasons": reasons,
                    }
                )
            )

    # Step 4: Assign final IDs and ensure use-case relevance is populated.
    findings = [
        finding.model_copy(
            update={
                "id": f"f{number}",
                "use_case_relevance": (
                    finding.use_case_relevance or [brief.use_case]
                ),
            }
        )
        for number, finding in enumerate(accepted, start=1)
    ]

    repaired_and_accepted_count = (
        len(accepted) - initially_accepted_count
    )

    # Step 5: Record the node's final tracing output.
    span_output = {
            "status": "completed",
            "drafted_count": len(drafted),
            "initially_accepted_count": initially_accepted_count,
            "rejected_for_repair_count": len(rejected),
            "repaired_and_accepted_count": repaired_and_accepted_count,
            "final_accepted_count": len(findings),
            "finding_ids": [
                finding.id for finding in findings
            ],
            "warnings": warnings,
            "final_rejected_findings": [
                {
                    "finding_id": finding_id,
                    "reasons": reasons,
                }
                for finding_id, reasons in reasons_by_id.items()
            ],
        }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.write_findings",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="completed",
    )

    # Step 6: Return the worker state update.
    return {
        "findings": [
            finding.model_dump(mode="json")
            for finding in findings
        ],
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


@observe(
    type="agent",
    name="customer_trends.submit",
    description="Submits validated findings, records acceptance and rejection outcomes, and persists the final worker result.",
)
async def submit(state: WorkerState, deps: WorkerDeps) -> Update:
    """Store the findings through submit_findings, write the result through the sink."""
    brief = brief_of(state)
    tools = build_tools(deps.tool_context(brief))
    warnings = list(state.get("warnings", []))
    events: list[dict[str, Any]] = []
    span_input = {
        "run_id": brief.run_id,
        "task_id": brief.task_id,
        "finding_count": len(state.get("findings", [])),
        "finding_ids": [
            finding.get("id")
            for finding in state.get("findings", [])
            if isinstance(finding, dict)
        ],
        "gap_count": len(gaps_of(state)),
        "metric_ids": state.get("metric_ids", {}),
    }
    span_metadata = {"worker": "customer_trends", "stage": "submission"}
    update_current_span(input=span_input, metadata=span_metadata)
    answer: dict[str, Any] = {}
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
    span_output = {
        "status": "completed",
        "run_id": brief.run_id,
        "accepted_count": len(answer.get("accepted", []))
        if state.get("findings") and isinstance(answer, dict)
        else 0,
        "rejected_count": len(answer.get("rejected", []))
        if state.get("findings") and isinstance(answer, dict)
        else 0,
        "result_written": True,
        "warning_count": len(warnings),
        "gap_count": len(gaps),
        "headline": headline,
    }
    update_current_span(output=span_output)
    save_customer_span(
        "customer_trends.submit",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
        status="completed",
    )
    return {
        "result": result.model_dump(mode="json"),
        "warnings": warnings,
        "events": events,
        "budget": _budget(deps),
    }
