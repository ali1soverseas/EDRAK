from __future__ import annotations

import json
import time
from datetime import datetime

from langchain_core.messages import HumanMessage

from edrak.agents.market_intelligence.prompts import (
    analysis_prompt,
    task_planner_prompt,
    tool_arg_prompt,
    tool_catalog_lines,
    usefulness_prompt,
)
from edrak.agents.market_intelligence.state import (
    DEFAULT_FALLBACK,
    MAX_RETRIES,
    MAX_TOOL_FALLBACK,
    MAX_URLS_PER_TASK,
    TOOL_FALLBACK_CHAIN,
    URL_BEARING_TOOLS,
    MarketAgentState,
    _banner,
    _kv,
    _section,
)
from edrak.config import settings
from edrak.mcp.web_tools import (
    ALL_SCRAPER_TOOLS,
    TOOL_MAP,
    extract_urls_from_tool_result,
    scrape_url_content,
)

_llm = None


def get_llm():
    global _llm
    if _llm is None:
        from langchain_ollama import ChatOllama

        _llm = ChatOllama(model="llama3.2", temperature=0)
    return _llm


def call_llm(prompt: str) -> str:
    """Simple text-only LLM call with retries."""
    for attempt in range(3):
        try:
            resp = get_llm().invoke([HumanMessage(content=prompt)])
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return f"[LLM ERROR: {exc}]"


def clean_json(raw: str) -> str:
    """Strip markdown fences from an LLM response to get raw JSON."""
    raw = raw.strip()
    for fence in ("```json", "```JSON", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
            break
    if raw.endswith("```"):
        raw = raw[:-3]
    start = min(
        (raw.find(c) for c in "{[" if c in raw),
        default=0,
    )
    end_brace = raw.rfind("}")
    end_bracket = raw.rfind("]")
    end = max(end_brace, end_bracket) + 1
    return raw[start:end].strip() if end > start else raw.strip()


def task_planner(state: MarketAgentState) -> dict:
    _banner("NODE: TASK PLANNER")
    goal = state["goal"]
    context = state["business_context"]

    prompt = task_planner_prompt(goal, context, tool_catalog_lines(ALL_SCRAPER_TOOLS))

    print("\n  Calling LLM to generate research task list...")
    raw = call_llm(prompt)
    cleaned = clean_json(raw)

    try:
        parsed = json.loads(cleaned)
        raw_tasks = parsed.get("tasks", [])
    except json.JSONDecodeError:
        raw_tasks = []

    task_list = []
    for i, task in enumerate(raw_tasks):
        desc = task.get("description", "").strip()
        if not desc:
            continue
        tool_hint = task.get("tool_hint", "tool_serper")
        if tool_hint not in TOOL_MAP:
            tool_hint = "tool_serper"
        task_list.append({
            "id": i + 1,
            "description": desc,
            "tool_hint": tool_hint,
            "status": "pending",
        })

    if not task_list:
        task_list = [
            {
                "id": 1,
                "description": f"Research: {goal[:120]}",
                "tool_hint": "tool_serper",
                "status": "pending",
            },
        ]

    _section("RESEARCH TO-DO LIST")
    for task in task_list:
        print(f"  [{task['id']}] {task['description']}")
        print(f"       Tool hint: {task['tool_hint']}")

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

    primary = task["tool_hint"]
    fallback_pool = TOOL_FALLBACK_CHAIN.get(primary, DEFAULT_FALLBACK)
    tools_to_try = [primary] + [name for name in fallback_pool if name != primary]
    tools_to_try = tools_to_try[:MAX_TOOL_FALLBACK]

    raw_result = ""
    used_tool = ""
    urls_found: list = []
    fetch_success = False

    for tool_name in tools_to_try:
        if tool_name not in TOOL_MAP:
            continue

        _section(f"DATA FETCH  --  {tool_name}")
        tool_obj = TOOL_MAP[tool_name]

        for attempt in range(MAX_RETRIES):
            print(f"  Attempt {attempt + 1}/{MAX_RETRIES}")

            arg_prompt = tool_arg_prompt(
                tool_name=tool_name,
                tool_description=(tool_obj.description or "").split(chr(10))[0].strip(),
                task_description=task["description"],
                context=context,
                goal=goal,
                attempt=attempt,
            )

            raw_args = call_llm(arg_prompt)
            try:
                tool_args = json.loads(clean_json(raw_args))
            except json.JSONDecodeError:
                tool_args = {"queries": [task["description"][:80]]}

            result_str = ""
            conn_ok = False
            for conn_attempt in range(MAX_RETRIES):
                try:
                    result_str = tool_obj.invoke(tool_args)
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

            new_urls = extract_urls_from_tool_result(tool_name, result_str)
            for url in new_urls:
                if url not in urls_found:
                    urls_found.append(url)
            if new_urls:
                print(f"  URLs extracted: {len(new_urls)}")

            if result_str and len(result_str) > 50:
                eval_raw = call_llm(usefulness_prompt(task["description"], result_str))
                try:
                    ev = json.loads(clean_json(eval_raw))
                    is_useful = ev.get("useful", True)
                    reason = ev.get("reason", "")
                except json.JSONDecodeError:
                    is_useful = True
                    reason = ""

                if is_useful:
                    print("  Evaluated: USEFUL")
                    raw_result = result_str
                    used_tool = tool_name
                    fetch_success = True
                    break
                print(f"  Evaluated: NOT USEFUL -- {reason}")
                print("  Retrying with different query...")
            else:
                print("  Empty result -- retrying")

        if fetch_success:
            break
        print(f"  {tool_name} exhausted. Trying next fallback tool...")

    if not fetch_success:
        print(f"  All tools exhausted for task [{task['id']}] -- skipping")
        task["status"] = "skipped"
        task_list[idx] = task
        return {
            "task_list": task_list,
            "current_task_idx": idx + 1,
        }

    scraped_texts: list = []
    evidence_items: list = []

    if used_tool in URL_BEARING_TOOLS and urls_found:
        _section(f"URL SCRAPER  --  {min(len(urls_found), MAX_URLS_PER_TASK)} URL(s)")
        for url in urls_found[:MAX_URLS_PER_TASK]:
            print(f"  Scraping: {url[:80]}")
            for scrape_attempt in range(MAX_RETRIES):
                try:
                    text = scrape_url_content(url)
                    if isinstance(text, str) and len(text) > 100:
                        scraped_texts.append(f"[URL: {url}]\n{text[:settings.scrape_text_chars]}")
                        print(f"    -> {len(text):,} chars fetched")
                        evidence_items.append({"type": "url", "source": url})
                    else:
                        print("    -> too short or error")
                    break
                except (ConnectionError, TimeoutError) as exc:
                    if scrape_attempt < MAX_RETRIES - 1:
                        wait = 2 ** scrape_attempt
                        print(f"    Connection error: {exc}. Retry in {wait}s...")
                        time.sleep(wait)
                    else:
                        print(f"    FAILED after {MAX_RETRIES} attempts")
                        break
                except Exception as exc:
                    print(f"    FAILED: {exc}")
                    break
    else:
        evidence_items.append({"type": "endpoint", "source": f"[{used_tool}]"})

    _section("LLM ANALYSIS")

    if scraped_texts:
        context_block = "\n\n---\n".join(scraped_texts)
        context_note = "Full article content scraped from source URLs:"
    else:
        context_block = raw_result[:4000]
        context_note = "Structured data returned by API tool:"

    raw_analysis = call_llm(
        analysis_prompt(
            task_description=task["description"],
            goal=goal,
            context=context,
            context_note=context_note,
            context_block=context_block,
        )
    )
    try:
        parsed_analysis = json.loads(clean_json(raw_analysis))
        claim = parsed_analysis.get("claim", "").strip()
    except json.JSONDecodeError:
        claim = raw_analysis.strip()[:400]

    if not claim:
        claim = f"Data retrieved for: {task['description']}"

    print(f"\n  Claim   : {claim[:200]}")
    print(f"  Evidence: {evidence_items}")

    finding = {
        "task": task["description"],
        "claim": claim,
        "evidence": evidence_items,
    }

    task["status"] = "done"
    task_list[idx] = task

    return {
        "task_list": task_list,
        "current_task_idx": idx + 1,
        "market_findings": state.get("market_findings", []) + [finding],
    }


def output_node(state: MarketAgentState) -> dict:
    _banner("NODE: OUTPUT  --  MARKET FINDINGS")

    findings = state.get("market_findings", [])
    task_list = state.get("task_list", [])

    done = sum(1 for task in task_list if task["status"] == "done")
    skipped = sum(1 for task in task_list if task["status"] == "skipped")
    print(f"\n  Tasks completed : {done}")
    print(f"  Tasks skipped   : {skipped}")
    print(f"  Findings        : {len(findings)}")

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
    }

    return {"final_report": report}
