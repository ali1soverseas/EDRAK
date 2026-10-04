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
    market_findings: list  # [{task, claim, evidence:[{type,source}]}]

    # Final assembled output (private worker state, not the orchestrator contract)
    final_report: dict


MAX_RETRIES = 3
MAX_TOOL_FALLBACK = 3
MAX_URLS_PER_TASK = 2
MAX_AGENT_STEPS = 5

URL_BEARING_TOOLS = {
    "tool_serper",
    "tool_web_search",
    "tool_newsapi",
    "tool_hacker_news",
    "tool_openalex",
    "tool_arxiv",
}

TOOL_FALLBACK_CHAIN: dict[str, list[str]] = {
    "tool_serper": ["tool_web_search", "tool_newsapi", "tool_hacker_news"],
    "tool_web_search": ["tool_serper", "tool_newsapi", "tool_hacker_news"],
    "tool_newsapi": ["tool_serper", "tool_web_search", "tool_hacker_news"],
    "tool_hacker_news": ["tool_newsapi", "tool_serper", "tool_web_search"],
    "tool_worldbank": ["tool_imf", "tool_fred", "tool_dbnomics"],
    "tool_imf": ["tool_worldbank", "tool_fred", "tool_dbnomics"],
    "tool_fred": ["tool_worldbank", "tool_imf", "tool_dbnomics"],
    "tool_arxiv": ["tool_openalex", "tool_hacker_news", "tool_serper"],
    "tool_openalex": ["tool_arxiv", "tool_serper", "tool_web_search"],
    "tool_alphavantage": ["tool_finnhub", "tool_sec_edgar"],
    "tool_finnhub": ["tool_alphavantage", "tool_sec_edgar"],
    "tool_sec_edgar": ["tool_alphavantage", "tool_finnhub"],
    "tool_gdelt": ["tool_newsapi", "tool_serper"],
    "tool_eurostat": ["tool_fred", "tool_worldbank"],
    "tool_dbnomics": ["tool_worldbank", "tool_imf"],
    "tool_wikidata": ["tool_serper", "tool_web_search"],
}
DEFAULT_FALLBACK = ["tool_serper", "tool_web_search", "tool_newsapi"]

MOCK_INPUT = {
    "goal": (
        "Understand the current market trends for AI-powered cybersecurity solutions "
        "and identify key growth opportunities."
    ),
    "business_context": (
        "Our company provides AI-based cybersecurity software for enterprise clients. "
        "Our products include threat detection, anomaly monitoring, and automated incident response. "
        "We target mid-to-large enterprises in financial services, healthcare, and critical infrastructure. "
        "We are currently evaluating expansion into the MENA region."
    ),
}


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
        "final_report": {},
    }


def _banner(msg: str) -> None:
    width = 72
    print(f"\n{'=' * width}")
    print(f"  {msg}")
    print(f"{'=' * width}")


def _section(msg: str) -> None:
    print(f"\n  {'-' * 60}")
    print(f"  {msg}")
    print(f"  {'-' * 60}")


def _kv(key: str, val: Any) -> None:
    print(f"  {key:<20}: {str(val)[:120]}")
