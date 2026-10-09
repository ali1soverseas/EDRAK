from __future__ import annotations

import json
import re
import time
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from edrak.agents.market_intelligence.prompts import (
    analysis_prompt,
    task_planner_prompt,
    tool_arg_prompt,
    tool_catalog_lines,
    usefulness_prompt,
)
from edrak.agents.market_intelligence.schemas import (
    AnalysisClaim,
    AnalysisReply,
    SearchQuery,
    TaskPlan,
    Usefulness,
)
from edrak.agents.market_intelligence.search_scope import (
    PLACES,
    SearchScope,
    domains_in_text,
    query_spec,
    scope_from_brief,
)
from edrak.agents.market_intelligence.source_tiers import (
    DEFAULT_SOURCE_TIERS,
    SourceTier,
    source_weight,
)
from edrak.agents.market_intelligence.state import (
    DEFAULT_FALLBACK,
    MAX_RETRIES,
    MAX_TOOL_FALLBACK,
    MAX_URL_CANDIDATES,
    MAX_URLS_PER_TASK,
    SEARCH_TOOLS,
    TOOL_FALLBACK_CHAIN,
    URL_BEARING_TOOLS,
    MarketAgentState,
    _banner,
    _kv,
    _section,
)
from edrak.core.action_log import log_action
from edrak.core.config import settings
from edrak.core.llm import get_chat_model
from edrak.mcp.web_tools import (
    ALL_SCRAPER_TOOLS,
    TOOL_MAP,
    extract_urls_from_tool_result,
    scrape_url_content,
)

def log_block(agent: str, title: str, body: str) -> None:
    """Write a titled multi-line record using the shared one-line logger."""
    text = body if isinstance(body, str) else str(body)
    log_action(agent, title)
    for line in text.strip().splitlines() or [text]:
        log_action(agent, line)


def call_structured(schema, prompt: str, *, method: str = "function_calling", retries: int = 1):
    """Ask for a validated `schema` instance, or None.

    `method` defaults to tool calling, which is what constrains output against
    Ollama Cloud. A prompt that reads as a prose question can still be answered
    without a tool call, so a caller whose prompt already states the schema uses
    json_mode instead: that parses rather than enforces, and a reply that is
    not JSON fails rather than slipping through.

    Retries, which replaces the retry loop the free-text helper used to own.
    A schema call returns None rather than raising, so a caller cannot tell a
    parse failure from an empty answer without this.
    """
    last_error = "the model did not respond"
    for attempt in range(max(retries, 1)):
        try:
            return get_chat_model(agent="market", temperature=0).with_structured_output(
                schema, method=method
            ).invoke(prompt)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log_action("market_intelligence", f"structured call failed: {last_error}")
            if attempt < retries - 1:
                time.sleep(2**attempt)
    return None


_STOPWORDS = {
    "about", "after", "also", "analyze", "analyse", "and", "are", "assess", "barriers",
    "based", "compare", "conditions", "current", "evaluate", "examine", "find", "for",
    "foreign", "from", "identify", "impact", "into", "market", "research", "should",
    "software", "that", "their", "this", "with",
}
_REGULATION_MARKERS = ("regulat", "licens", "law", "residen", "compliance")
_NEWS_MARKERS = ("breaking news", "announcement", "this week", "adoption news")
_QUERY_NOISE = ("cpiaucsl", "fedfunds", "panw", "crwd", "ny.gdp", "series_ids", "tickers")


def content_terms(text: str) -> list[str]:
    found: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text.lower()):
        if word in _STOPWORDS or word in found:
            continue
        found.append(word)
    for word in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if word not in found:
            found.append(word)
    return found[:12]


def route_tool(description: str, hint: str) -> str:
    """Laws go to Tavily. News tasks may use NewsAPI. Everything else starts on Serper."""
    lowered = description.lower()
    if any(marker in lowered for marker in _REGULATION_MARKERS):
        return "tool_web_search"
    if any(marker in lowered for marker in _NEWS_MARKERS) and hint == "tool_newsapi":
        return "tool_newsapi"
    if hint in ("tool_serper", "tool_web_search"):
        return hint
    if hint in SEARCH_TOOLS:
        return "tool_serper"
    return "tool_serper"


def keyword_queries(task: str, goal: str, attempt: int) -> list[str]:
    terms = content_terms(task)
    extras = [term for term in content_terms(goal) if term not in terms][:3]
    pools = [
        terms[:6] + extras[:2],
        terms[2:8] + extras[:2],
        extras[:3] + terms[:4],
    ]
    words = [word for word in pools[attempt % len(pools)] if word]
    if not words:
        words = content_terms(goal)[:6] or ["devops"]
    return [" ".join(words[:8])]


def _keyword_query(query: str, task: str) -> bool:
    text = query.strip()
    lowered = text.lower()
    if not text or lowered.startswith("research:"):
        return False
    if any(noise in lowered for noise in _QUERY_NOISE):
        return False
    if len(text.split()) > 12:
        return False
    if lowered in task.lower() and len(text.split()) > 8:
        return False
    return True


def _place_tokens(scope: SearchScope) -> list[str]:
    tokens: list[str] = []
    if scope.place:
        tokens.append(scope.place.lower())
        tokens.extend(part for part in scope.place.lower().split() if len(part) >= 2)
    if scope.gl:
        tokens.append(scope.gl.lower())
    if scope.gl == "us":
        tokens.extend(["united states", "u.s.", "usa", "us"])
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return sorted(ordered, key=len, reverse=True)


def _strip_place_from_query(text: str, task: str, scope: SearchScope) -> str:
    """Region is in gl. Keep it in q only when the task itself is about that place."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return cleaned
    task_l = task.lower()
    lowered = cleaned.lower()
    prefixes = list(_place_tokens(scope))
    for extra in ("united states", "u.s.", "usa", "us"):
        if extra not in prefixes:
            prefixes.append(extra)
    prefixes.sort(key=len, reverse=True)
    for token in prefixes:
        if len(token) < 2 or token in task_l:
            continue
        if lowered.startswith(token + " "):
            cleaned = cleaned[len(token):].strip(" ,:-")
            lowered = cleaned.lower()
    return cleaned or text.strip()


def _query_fields(item) -> tuple[str, str, list | None]:
    if isinstance(item, str):
        return item.strip(), "", None
    if isinstance(item, dict):
        text = str(item.get("q") or item.get("query") or "").strip()
        language = str(item.get("language") or "").strip()
        domains = item.get("include_domains")
        return text, language, domains if isinstance(domains, list) else None
    return "", "", None


def _specs_from_queries(
    raw_queries,
    task: str,
    scope: SearchScope,
    seen: set[str],
) -> list[dict]:
    specs: list[dict] = []
    if not isinstance(raw_queries, list):
        return specs
    for item in raw_queries:
        text, language, domains = _query_fields(item)
        text = _strip_place_from_query(text, task, scope)
        if not _keyword_query(text, task) or text.lower() in seen:
            continue
        specs.append(query_spec(text, scope, language, domains))
    return specs


class QueryArgs(BaseModel):
    """The query list tool_arg_prompt asks for."""

    queries: list[SearchQuery] = Field(default_factory=list)


def search_arguments(
    reply: QueryArgs | None,
    task: str,
    goal: str,
    attempt: int,
    seen: set[str],
    context: str = "",
    scope: SearchScope | None = None,
) -> dict:
    """Keep short keyword queries and stamp the brief's region, language, and recency."""
    scope = scope or scope_from_brief(goal, context)
    raw_queries = [item.model_dump() for item in reply.queries] if reply else None
    specs = _specs_from_queries(raw_queries, task, scope, seen)
    if not specs:
        fallback = [query for query in keyword_queries(task, goal, attempt) if query.lower() not in seen]
        if not fallback:
            fallback = keyword_queries(task, goal, attempt + 3)
        specs = [
            query_spec(_strip_place_from_query(query, task, scope), scope, "en")
            for query in fallback
        ]
    for spec in specs:
        seen.add(spec["q"].lower())
    limit = 4 if scope.needs_local_queries else 3
    return {"queries": specs[:limit]}


def _place_named_in(text: str) -> str:
    lowered = text.lower()
    found = ""
    for place in PLACES:
        for name in place.names:
            if len(name) >= 4 and name in lowered and len(name) > len(found):
                found = name
    return found


def source_is_relevant(task: str, goal: str, text: str) -> tuple[bool, str]:
    """A source must mention the task subject. A long page is not enough."""
    body = (text or "").strip()
    if len(body) < 80:
        return False, "too short"
    lowered = body.lower()
    if len(body) < 500 and re.search(r"\b(0 hits|no results|empty result)\b", lowered):
        return False, "empty result"
    needed = content_terms(task) or content_terms(f"{task} {goal}")
    hits = [term for term in needed if term in lowered]
    if len(hits) < 2:
        return False, "does not mention the task subject"
    place = _place_named_in(task)
    if place and place not in lowered:
        return False, "does not mention the task subject"
    return True, ", ".join(hits[:6])


def relevant_excerpt(text: str, task: str, goal: str, limit: int = 1200) -> str:
    """Keep the page region that matches the task, not the navigation header."""
    terms = content_terms(f"{task} {goal}")
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 40]
    if not lines:
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:limit]
    best_at = 0
    best_score = -1
    window = 6
    for index in range(len(lines)):
        chunk = " ".join(lines[index:index + window]).lower()
        score = sum(chunk.count(term) for term in terms)
        if score > best_score:
            best_at = index
            best_score = score
    return "\n".join(lines[best_at:best_at + window])[:limit]


def _scope_match(blob: str, scope: SearchScope) -> float:
    if not scope.place and not scope.gl:
        return 0.0
    if scope.place and scope.place.lower() in blob:
        return 1.0
    words = re.findall(r"[a-z]{4,}", scope.place.lower())
    if words and any(word in blob for word in words):
        return 1.0
    if scope.gl and re.search(rf"(?<![a-z]){re.escape(scope.gl)}(?![a-z])", blob):
        return 0.5
    return 0.0


def _result_score(node: dict) -> float:
    score = node.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    position = node.get("position")
    if isinstance(position, int) and position > 0:
        return max(0.05, 1 - (position - 1) * 0.08)
    return 0.0


def _scored_hits(
    raw: str,
    task: str,
    goal: str,
    scope: SearchScope | None = None,
    tiers: tuple[SourceTier, ...] = DEFAULT_SOURCE_TIERS,
    vendor_domains: tuple[str, ...] = (),
) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    scope = scope or scope_from_brief(goal)
    terms = content_terms(task) or content_terms(f"{task} {goal}")
    best: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            url = node.get("link") or node.get("url") or node.get("story_url")
            title = str(node.get("title") or "")
            snippet = str(node.get("snippet") or node.get("description") or node.get("content") or "")
            blob = f"{title} {snippet} {url}".lower()
            if isinstance(url, str) and url.startswith("http"):
                hits = sum(1 for term in terms if term in blob)
                keyword = hits / max(len(terms), 1)
                total = (
                    _result_score(node)
                    + keyword
                    + _scope_match(blob, scope)
                    + source_weight(url, tiers, vendor_domains)
                )
                previous = best.get(url)
                if previous is None or total > previous["score"]:
                    best[url] = {
                        "url": url,
                        "title": title,
                        "snippet": snippet,
                        "score": total,
                    }
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return sorted(best.values(), key=lambda item: item["score"], reverse=True)


def ranked_urls(
    raw: str,
    task: str,
    goal: str,
    scope: SearchScope | None = None,
    tiers: tuple[SourceTier, ...] = DEFAULT_SOURCE_TIERS,
    vendor_domains: tuple[str, ...] = (),
) -> list[str]:
    """Rank links by search score, keyword match, scope match, and source tier."""
    return [
        item["url"]
        for item in _scored_hits(raw, task, goal, scope, tiers, vendor_domains)
    ]


def search_hit_preview(
    raw: str,
    task: str,
    goal: str,
    scope: SearchScope | None = None,
    limit: int = 8,
) -> str:
    """Compact ranked titles and snippets for the usefulness check."""
    lines: list[str] = []
    for item in _scored_hits(raw, task, goal, scope)[:limit]:
        title = (item.get("title") or "").strip() or item["url"]
        snippet = re.sub(r"\s+", " ", item.get("snippet") or "").strip()[:240]
        lines.append(f"- {title}: {snippet}" if snippet else f"- {title}")
    return "\n".join(lines)


def titles_on_topic(raw: str, task: str, goal: str, scope: SearchScope | None = None) -> bool:
    """True when at least two ranked hits already name the task subject."""
    terms = content_terms(task)
    if len(terms) < 2:
        terms = content_terms(f"{task} {goal}")
    matched = 0
    for item in _scored_hits(raw, task, goal, scope)[:8]:
        blob = f"{item.get('title') or ''} {item.get('snippet') or ''}".lower()
        if sum(1 for term in terms if term.lower() in blob) >= 2:
            matched += 1
    return matched >= 2


def claims_from_analysis(
    reply: AnalysisReply | AnalysisClaim | None, source_text: str
) -> list[AnalysisClaim]:
    """The reply's claims that the source actually supports.

    The caller binds to AnalysisReply first and falls back to a bare
    AnalysisClaim, so either type can arrive here.
    """
    items = list(reply.claims) if isinstance(reply, AnalysisReply) and reply.claims else []
    if not items and isinstance(reply, AnalysisClaim):
        items = [reply]
    supported: list[AnalysisClaim] = []
    for item in items[:3]:
        if claim_is_supported(item.claim.strip(), item.quote.strip(), source_text):
            supported.append(item)
    return supported


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def claim_is_supported(claim: str, quote: str, source: str) -> bool:
    """The quote must be copied from the source and the claim must use its words."""
    statement = (claim or "").strip()
    if len(statement) < 40 or statement.lower().startswith("data retrieved for"):
        return False
    copied = _normalized(quote)
    if len(copied) < 40 or copied not in _normalized(source):
        return False
    overlap = set(content_terms(statement)) & set(content_terms(quote))
    return len(overlap) >= 2


def claim_confidence(claim: str, quote: str) -> float:
    """Score specificity. A bare page scrape does not get a high score."""
    score = 0.4
    if any(char.isdigit() for char in claim):
        score += 0.25
    if len(quote) >= 100:
        score += 0.1
    names = re.findall(r"\b[A-Z][A-Za-z]{2,}\b", claim)
    if any(name.lower() in quote.lower() for name in names):
        score += 0.15
    if not any(char.isdigit() for char in claim) and not names:
        score = min(score, 0.5)
    return round(min(score, 0.9), 2)


def tasks_from_plan_reply(plan: TaskPlan | None) -> list[dict]:
    """The task list from a validated plan, or empty when the reply did not fit.

    The reply is bound to TaskPlan by the caller, so this only flattens it.
    The hand-walked fallbacks it used to have — a bare array of strings, a dict
    keyed by task, goal or tool — are gone; those shapes no longer reach here
    because the schema rejects them first.
    """
    if plan is None:
        return []
    return [
        {
            "description": task.description,
            "tool_hint": task.tool_hint,
            "queries": [item.model_dump() for item in task.queries if item.q or item.query],
        }
        for task in plan.tasks
    ]


def _query_strings(queries) -> list[str]:
    found: list[str] = []
    if not isinstance(queries, list):
        return found
    for item in queries:
        if isinstance(item, dict):
            text = str(item.get("q") or item.get("query") or "").strip()
        else:
            text = str(item).strip()
        if text and text not in found:
            found.append(text)
    return found


def invoke_search_tool(tool_obj, tool_args: dict) -> str:
    """MCP search tools take List[str]. Flatten planner query objects to keywords."""
    strings = _query_strings(tool_args.get("queries"))
    result = tool_obj.invoke({"queries": strings})
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def task_planner(state: MarketAgentState) -> dict:
    _banner("NODE: TASK PLANNER")
    goal = state["goal"]
    context = state["business_context"]
    log_block("market_intelligence", "GOAL", goal)
    log_block("market_intelligence", "BUSINESS CONTEXT", context)

    scope = scope_from_brief(goal, context)
    search_tools = [tool for tool in ALL_SCRAPER_TOOLS if tool.name in SEARCH_TOOLS]
    prompt = task_planner_prompt(goal, context, tool_catalog_lines(search_tools), scope.note())
    print(f"  Scope: {scope.note()}")

    print("\n  Calling LLM to generate research task list...")
    raw_tasks = tasks_from_plan_reply(call_structured(TaskPlan, prompt, method="json_mode"))
    if len(raw_tasks) < 4:
        print(f"  Planner reply had {len(raw_tasks)} task(s). Asking again for 4 to 7.")
        retried = tasks_from_plan_reply(
            call_structured(
                TaskPlan,
                prompt
                + "\nThe previous reply was not a list of 4 to 7 tasks. "
                "Return only the JSON object.",
                method="json_mode",
            )
        )
        if len(retried) > len(raw_tasks):
            raw_tasks = retried

    task_list = []
    for i, item in enumerate(raw_tasks):
        description = item["description"].strip()
        tool_hint = route_tool(description, item["tool_hint"])
        queries = _specs_from_queries(item.get("queries") or [], description, scope, set())
        task_list.append({
            "id": i + 1,
            "description": description,
            "tool_hint": tool_hint,
            "status": "pending",
            "search": scope.as_dict(),
            "queries": queries,
        })

    if not task_list:
        task_list = [
            {
                "id": 1,
                "description": f"Research: {goal[:120]}",
                "tool_hint": "tool_serper",
                "status": "pending",
                "search": scope.as_dict(),
                "queries": [],
            },
        ]

    _section("RESEARCH TO-DO LIST")
    for task in task_list:
        print(f"  [{task['id']}] {task['description']}")
        print(f"       Tool hint: {task['tool_hint']}")
        if task.get("queries"):
            print(f"       Queries  : {len(task['queries'])}")
        log_action("market_intelligence", f"planned task {task['id']}: {task['description']} ({task['tool_hint']})")

    return {
        "task_list": task_list,
        "current_task_idx": 0,
        "market_findings": [],
    }


def task_executor(state: MarketAgentState) -> dict:
    """
    Processes the current task:
      1. Data fetch with smart retry + tool fallback
      2. URL scraping for richer article content
      3. LLM analysis -> claim + evidence
      4. Appends finding to market_findings
      5. Advances current_task_idx
    """
    import requests as _requests

    task_list = list(state["task_list"])
    idx = state["current_task_idx"]

    if idx >= len(task_list):
        return {}

    task = dict(task_list[idx])
    task["status"] = "running"
    task_list[idx] = task

    _banner(f"TASK [{task['id']}/{len(task_list)}]: {task['description'][:80]}")
    _kv("tool_hint", task["tool_hint"])

    goal = state["goal"]
    context = state["business_context"]
    scope = scope_from_brief(goal, context)
    vendor_domains = domains_in_text(f"{goal}\n{context}")

    primary = task["tool_hint"]
    fallback_pool = TOOL_FALLBACK_CHAIN.get(primary, DEFAULT_FALLBACK)
    tools_to_try = [primary] + [name for name in fallback_pool if name != primary]
    tools_to_try = tools_to_try[:MAX_TOOL_FALLBACK]

    raw_result = ""
    used_tool = ""
    urls_found: list = []
    fetch_success = False
    seen_queries: set[str] = set()
    rejected_queries: list[str] = []
    gaps = list(state.get("market_gaps", []))

    for tool_name in tools_to_try:
        if tool_name not in TOOL_MAP:
            continue

        _section(f"DATA FETCH  --  {tool_name}")
        tool_obj = TOOL_MAP[tool_name]

        for attempt in range(MAX_RETRIES):
            print(f"  Attempt {attempt + 1}/{MAX_RETRIES}")

            if attempt == 0 and task.get("queries"):
                tool_args = {"queries": task["queries"]}
                for spec in task["queries"]:
                    if isinstance(spec, dict) and spec.get("q"):
                        seen_queries.add(str(spec["q"]).lower())
            else:
                arg_prompt = tool_arg_prompt(
                    tool_name=tool_name,
                    tool_description=(tool_obj.description or "").split(chr(10))[0].strip(),
                    task_description=task["description"],
                    context=context,
                    goal=goal,
                    attempt=attempt,
                    scope_note=scope.note(),
                    rejected_queries=rejected_queries,
                )
                tool_args = search_arguments(
                    call_structured(QueryArgs, arg_prompt, method="json_mode"),
                    task["description"],
                    goal,
                    attempt,
                    seen_queries,
                    context,
                    scope,
                )

            shown = json.dumps(tool_args, ensure_ascii=False)
            print(f"  Arguments: {shown}")
            log_action("market_intelligence", f"call {tool_name} {shown[:300]}")

            result_str = ""
            conn_ok = False
            for conn_attempt in range(MAX_RETRIES):
                try:
                    result_str = invoke_search_tool(tool_obj, tool_args)
                    conn_ok = True
                    break
                except (_requests.ConnectionError, _requests.Timeout, ConnectionError) as exc:
                    wait = 2 ** conn_attempt
                    print(f"    [CONN ERROR] {exc}. Retry in {wait}s... ({conn_attempt + 1}/{MAX_RETRIES})")
                    time.sleep(wait)
                except Exception as exc:
                    print(f"    [TOOL ERROR] {exc}")
                    result_str = ""
                    break

            if not conn_ok and not result_str:
                print(f"  {tool_name} unreachable -- trying next fallback tool")
                break

            new_urls = ranked_urls(
                result_str,
                task["description"],
                goal,
                scope=scope,
                vendor_domains=vendor_domains,
            )
            if not new_urls:
                new_urls = extract_urls_from_tool_result(tool_name, result_str)
            if new_urls:
                print(f"  URLs extracted: {len(new_urls)}")

            preview = search_hit_preview(result_str, task["description"], goal, scope)
            relevant, why = source_is_relevant(
                task["description"],
                goal,
                preview or result_str,
            )
            strong_hits = titles_on_topic(result_str, task["description"], goal, scope)
            verdict = None
            if relevant and not strong_hits:
                verdict = call_structured(
                    Usefulness,
                    usefulness_prompt(task["description"], preview or result_str),
                    method="json_mode",
                )
            model_rejects = verdict is not None and not verdict.useful
            is_useful = bool(new_urls) and (strong_hits or (relevant and not model_rejects))
            if strong_hits:
                reason = "ranked titles match the task"
            elif not relevant:
                reason = why
            else:
                reason = "" if verdict is None else verdict.reason

            if is_useful:
                print("  Evaluated: USEFUL")
                log_action("market_intelligence", f"{tool_name} useful")
                raw_result = result_str
                used_tool = tool_name
                urls_found = list(new_urls)
                fetch_success = True
                break
            print("  Not useful.")
            print(f"  Arguments used: {shown}")
            print(f"  Why: {reason or 'the model did not give a reason'}")
            log_action(
                "market_intelligence",
                f"{tool_name} not useful. arguments={shown[:240]} why={reason}",
            )
            for spec in tool_args.get("queries") or []:
                query = spec.get("q") if isinstance(spec, dict) else spec
                if query and str(query) not in rejected_queries:
                    rejected_queries.append(str(query))
            print("  Retrying with different arguments...")

        if fetch_success:
            break
        print(f"  {tool_name} exhausted. Trying next fallback tool...")

    def _gap(reason: str) -> dict:
        print(f"  Gap: {reason}")
        log_action("market_intelligence", f"gap: {reason}")
        task["status"] = "skipped"
        task_list[idx] = task
        return {
            "task_list": task_list,
            "current_task_idx": idx + 1,
            "market_gaps": gaps + [reason],
        }

    if not fetch_success:
        print(f"  All tools exhausted for task [{task['id']}] -- skipping")
        return _gap(f"No relevant source for: {task['description']}")

    evidence_items: list = []
    if used_tool in URL_BEARING_TOOLS and urls_found:
        candidates = urls_found[:MAX_URL_CANDIDATES]
        _section(f"URL SCRAPER  --  up to {MAX_URLS_PER_TASK} relevant of {len(candidates)} ranked")
        for url in candidates:
            if len(evidence_items) >= MAX_URLS_PER_TASK:
                break
            print(f"  Scraping: {url[:80]}")
            text = ""
            for scrape_attempt in range(MAX_RETRIES):
                try:
                    text = scrape_url_content(url)
                    break
                except (ConnectionError, TimeoutError) as exc:
                    if scrape_attempt < MAX_RETRIES - 1:
                        wait = 2 ** scrape_attempt
                        print(f"    Connection error: {exc}. Retry in {wait}s...")
                        time.sleep(wait)
                    else:
                        print(f"    FAILED after {MAX_RETRIES} attempts")
                except Exception as exc:
                    print(f"    FAILED: {exc}")
                    break
            if not isinstance(text, str) or len(text) < 100:
                print("    -> too short or error")
                log_block("market_intelligence", f"SCRAPED URL  {url}", "skipped: too short or error")
                continue
            excerpt = relevant_excerpt(text, task["description"], goal, settings.SCRAPE_TEXT_CHARS)
            page_ok, page_why = source_is_relevant(task["description"], goal, excerpt)
            if not page_ok:
                print(f"    -> skipped page ({page_why})")
                log_block("market_intelligence", f"SCRAPED URL  {url}", f"skipped: {page_why}")
                continue
            print(f"    -> kept {len(excerpt):,} chars")
            log_block("market_intelligence", f"SCRAPED URL  {url}", f"kept {len(excerpt)} chars")
            evidence_items.append({"type": "url", "source": url, "text": excerpt})
    else:
        excerpt = relevant_excerpt(raw_result, task["description"], goal, 2000)
        evidence_items.append({"type": "endpoint", "source": f"[{used_tool}]", "text": excerpt})

    source_text = "\n".join(item.get("text") or "" for item in evidence_items)
    if not source_is_relevant(task["description"], goal, source_text)[0]:
        return _gap(f"No page stated a relevant fact for: {task['description']}")

    _section("LLM ANALYSIS")
    prompt = analysis_prompt(
        task_description=task["description"],
        goal=goal,
        context=context,
        context_note="Source text:",
        context_block=source_text,
    )
    reply = call_structured(AnalysisReply, prompt, method="json_mode")
    if reply is None:
        # A single claim often comes back bare rather than wrapped, which the
        # wrapper schema rejects. One extra call, only on that failure.
        reply = call_structured(AnalysisClaim, prompt, method="json_mode")
    parsed_claims = claims_from_analysis(reply, source_text)
    if not parsed_claims:
        print("  Claim rejected: no supported fact")
        return _gap(f"No supported fact for: {task['description']}")

    new_findings = []
    for item in parsed_claims:
        claim = item.claim.strip()
        quote = item.quote.strip()
        cited = [
            evidence for evidence in evidence_items
            if quote and _normalized(quote) in _normalized(evidence.get("text") or "")
        ] or evidence_items
        confidence = claim_confidence(claim, quote)
        print(f"\n  Claim      : {claim[:200]}")
        print(f"  Quote      : {quote[:160]}")
        print(f"  Confidence : {confidence}")
        log_action("market_intelligence", f"claim: {claim[:240]}")
        new_findings.append({
            "task": task["description"],
            "claim": claim,
            "quote": quote,
            "confidence": confidence,
            "evidence": cited,
        })

    task["status"] = "done"
    task_list[idx] = task
    return {
        "task_list": task_list,
        "current_task_idx": idx + 1,
        "market_findings": state.get("market_findings", []) + new_findings,
        "market_gaps": gaps,
    }


def fill_gaps(state: MarketAgentState) -> dict:
    """Retry skipped tasks in place. Keep first-pass findings and task IDs."""
    _banner("GAP FILL  --  RETRY SKIPPED TASKS")
    task_list = [dict(task) for task in (state.get("task_list") or [])]
    for task in task_list:
        if task.get("status") != "skipped":
            continue
        task["status"] = "pending"
        task["queries"] = []
        task["retry"] = True
        print(f"  [{task.get('id')}] {task.get('description')}")
        print(f"       Tool hint: {task.get('tool_hint')}")
        log_action(
            "market_intelligence",
            f"gap retry {task.get('id')}: {task.get('description')} ({task.get('tool_hint')})",
        )

    pending_at = next(
        (index for index, task in enumerate(task_list) if task.get("status") == "pending"),
        len(task_list),
    )
    return {
        "task_list": task_list,
        "current_task_idx": pending_at,
        "market_gaps": [],
        "gap_fill_done": True,
    }


def output_node(state: MarketAgentState) -> dict:
    _banner("NODE: OUTPUT  --  MARKET FINDINGS")

    findings = state.get("market_findings", [])
    task_list = state.get("task_list", [])
    gaps = list(state.get("market_gaps", []))

    done = sum(1 for task in task_list if task["status"] == "done")
    skipped = sum(1 for task in task_list if task["status"] == "skipped")
    print(f"\n  Tasks completed : {done}")
    print(f"  Tasks skipped   : {skipped}")
    print(f"  Findings        : {len(findings)}")
    print(f"  Gaps            : {len(gaps)}")
    for gap in gaps:
        print(f"    - {gap}")

    _section("MARKET FINDINGS SUMMARY")
    for i, finding in enumerate(findings, 1):
        print(f"\n  [{i}] Task   : {finding['task'][:70]}")
        print(f"       Claim  : {finding['claim'][:200]}")
        for evidence in finding.get("evidence", []):
            print(f"       Source : [{evidence['type']}] {evidence['source'][:80]}")

    report = {
        "run_id": state["run_id"],
        "timestamp": datetime.now().strftime("%Y-%m-%d_%H-%M"),
        "goal": state["goal"],
        "business_context": state["business_context"],
        "task_summary": {
            "total": len(task_list),
            "done": done,
            "skipped": skipped,
        },
        "task_list": task_list,
        "market_findings": findings,
        "gaps": gaps,
    }

    return {"final_report": report}
