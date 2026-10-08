from typing import Any

from typing_extensions import TypedDict


class MarketAgentState(TypedDict):
    run_id: str
    goal: str
    business_context: str

    # Set by task_planner
    task_list: list  # [{id, description, tool_hint, status}]

    # Current task index -- incremented by task_executor
    current_task_idx: int

    # Accumulated findings across all tasks
    market_findings: list  # [{task, claim, quote, confidence, evidence}]

    # Tasks that produced no supported fact
    market_gaps: list

    # One extra search pass has already been queued for the gaps
    gap_fill_done: bool

    # Final assembled output (private agent state, not the orchestrator contract)
    final_report: dict


MAX_RETRIES = 2
MAX_TOOL_FALLBACK = 2
# Keep this many relevant pages. Rank more candidates and stop once this many are kept.
MAX_URLS_PER_TASK = 2
MAX_URL_CANDIDATES = 5

SEARCH_TOOLS = ("tool_serper", "tool_web_search", "tool_newsapi")

URL_BEARING_TOOLS = set(SEARCH_TOOLS)

TOOL_FALLBACK_CHAIN: dict[str, list[str]] = {
    "tool_serper": ["tool_web_search"],
    "tool_web_search": ["tool_serper"],
    "tool_newsapi": ["tool_serper"],
}
DEFAULT_FALLBACK = ["tool_serper", "tool_web_search"]


def empty_market_state(
    run_id: str,
    goal: str,
    business_context: str,
) -> MarketAgentState:
    return {
        "run_id": run_id,
        "goal": goal,
        "business_context": business_context,
        "task_list": [],
        "current_task_idx": 0,
        "market_findings": [],
        "market_gaps": [],
        "gap_fill_done": False,
        "final_report": {},
    }


_AGENT = "market_intelligence"


def _banner(msg: str) -> None:
    width = 72
    print(f"\n{'=' * width}")
    print(f"  [{_AGENT}] {msg}")
    print(f"{'=' * width}")


def _section(msg: str) -> None:
    print(f"\n  {'-' * 60}")
    print(f"  [{_AGENT}] {msg}")
    print(f"  {'-' * 60}")


def _kv(key: str, val: Any) -> None:
    print(f"  [{_AGENT}] {key:<20}: {str(val)[:120]}")
