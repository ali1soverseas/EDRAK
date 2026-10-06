"""Wiring of the worker graph (SPEC section 10): which node runs after which.

START -> intake -> plan_queries -> [social, demand, reviews] -> join -> analyze -> gap_check
gap_check -> plan_queries   (a critical gap, no replan yet, budget left)
gap_check -> write_findings -> submit -> END
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.deps import WorkerDeps
from edrak.agents.customer_trends.gaps import critical_gaps
from edrak.agents.customer_trends.providers.budget import BudgetTracker
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.state import WorkerState, gaps_of

CHECKPOINTS_FILE = "checkpoints.db"
MAX_REPLANS = 1

WorkerGraph = CompiledStateGraph[WorkerState, None, WorkerState, WorkerState]
NODES: dict[str, nodes.NodeFn] = {
    "intake": nodes.intake,
    "plan_queries": nodes.plan_queries,
    "social": nodes.social,
    "demand": nodes.demand,
    "reviews": nodes.reviews,
    "join": nodes.join,
    "analyze": nodes.analyze,
    "gap_check": nodes.gap_check,
    "write_findings": nodes.write_findings,
    "submit": nodes.submit,
}
COLLECTION_BRANCHES = ("social", "demand", "reviews")


def budget_remains(budget: BudgetTracker) -> bool:
    """True while the run may still make tool calls, spend money and use time."""
    snapshot = budget.snapshot()
    limits = snapshot.limits
    return (
        snapshot.tool_calls < limits.max_tool_calls
        and snapshot.cost_usd < limits.max_cost_usd
        and snapshot.seconds < limits.max_seconds
    )


def route_after_gap_check(
    state: WorkerState, budget: BudgetTracker
) -> Literal["plan_queries", "write_findings"]:
    """Replan once, and only for a critical gap while the budget allows it."""
    replan = (
        bool(critical_gaps(gaps_of(state)))
        and state.get("replan_count", 0) < MAX_REPLANS
        and budget_remains(budget)
    )
    return "plan_queries" if replan else "write_findings"


def _bind(node: nodes.NodeFn, deps: WorkerDeps) -> Callable[[WorkerState], Awaitable[Any]]:
    async def run(state: WorkerState) -> nodes.Update:
        return await node(state, deps)

    return run


def build_graph(
    deps: WorkerDeps, checkpointer: BaseCheckpointSaver[str] | None = None
) -> WorkerGraph:
    """Build and compile the graph. With a checkpointer a run can resume by its `run_id`."""
    builder = StateGraph(WorkerState)
    for name, node in NODES.items():
        builder.add_node(name, _bind(node, deps))  # type: ignore[call-overload]  # a Callable-typed closure is rejected by the node protocol
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "plan_queries")
    for branch in COLLECTION_BRANCHES:
        builder.add_edge("plan_queries", branch)
    builder.add_edge(list(COLLECTION_BRANCHES), "join")
    builder.add_edge("join", "analyze")
    builder.add_edge("analyze", "gap_check")
    builder.add_conditional_edges(
        "gap_check",
        lambda state: route_after_gap_check(state, deps.budget),
        ["plan_queries", "write_findings"],
    )
    builder.add_edge("write_findings", "submit")
    builder.add_edge("submit", END)
    return builder.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncSqliteSaver]:
    """The SQLite checkpointer at `<data_dir>/checkpoints.db`; graphs use `thread_id = run_id`."""
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.data_dir / CHECKPOINTS_FILE)
    ) as saver:
        yield saver
