from edrak.agents.market_intelligence.schemas import (
    AnalysisReply,
    Usefulness,
    schema_text,
)


def tool_catalog_lines(tools) -> str:
    return "\n".join(
        f"  {tool.name}: {(tool.description or '').split(chr(10))[0].strip()}"
        for tool in tools
    )


def task_planner_prompt(goal: str, context: str, tool_catalog: str, scope_note: str) -> str:
    return (
        "You are a market research planning agent.\n"
        "Your job is to break down a business research goal into a concrete, ordered list "
        "of research tasks.\n\n"
        f"GOAL:\n{goal}\n\n"
        f"BUSINESS CONTEXT:\n{context}\n\n"
        f"AVAILABLE TOOLS:\n{tool_catalog}\n\n"
        "Instructions:\n"
        "1. Generate 4-7 specific, actionable research tasks needed to answer the goal.\n"
        "2. Each task should focus on one clear aspect (trends, competitors, demand, tech, etc.).\n"
        "3. For each task, suggest one tool from the list above.\n"
        "   Use tool_web_search only for laws, licensing, and data-residency rules.\n"
        "   Use tool_serper for market size, competitors, pricing, and customer needs.\n"
        "   Use tool_newsapi only for recent news or adoption announcements.\n"
        "4. Order tasks from most important to least important.\n"
        "5. On every task, add queries. Each query object carries the brief's scope:\n"
        "   q, gl (region), language, recency (day, week, month, or year), "
        "and include_domains only when specific sites are required.\n"
        "   q must name the companies or questions in THAT task. "
        "Do not put market-size queries on a competition, pricing, or customer-needs task.\n"
        f"6. Target scope: {scope_note}\n"
        "   Copy that gl, language, and recency onto every query. "
        "Do not keep the sample values when the brief names a different place. "
        "Do not put the country name in q; gl already has the region.\n\n"
        "Return ONLY this JSON object, with 4 to 7 tasks. No markdown.\n"
        "{\n"
        '  "tasks": [\n'
        "    {\n"
        '      "description": "Identify current market size and growth of agentic AI coding tools",\n'
        '      "tool_hint": "tool_serper",\n'
        '      "queries": [\n'
        '        {"q": "agentic AI coding tools market size", "language": "en", "recency": "year"}\n'
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "description": "Map GitHub Copilot and Amazon Q Developer pricing models",\n'
        '      "tool_hint": "tool_serper",\n'
        '      "queries": [\n'
        '        {"q": "GitHub Copilot Business pricing", "language": "en", "recency": "year"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )


def tool_arg_prompt(
    tool_name: str,
    tool_description: str,
    task_description: str,
    context: str,
    goal: str,
    attempt: int,
    scope_note: str = "",
    rejected_queries: list[str] | None = None,
) -> str:
    prompt = (
        f"You are writing web search keywords for THIS research task only.\n"
        f"Tool: {tool_name}. {tool_description}\n\n"
        f"Research task: {task_description}\n"
    )
    if rejected_queries:
        listed = "\n".join(f"- {query}" for query in rejected_queries[-9:])
        prompt += (
            "\nThese queries were already tried and did not answer the task. Do not reuse them:\n"
            f"{listed}\n"
        )
    elif attempt > 0:
        prompt += (
            f"\nAttempt {attempt} was off-topic or empty. "
            "Use different keywords. Keep the products and question from the task.\n"
        )
    prompt += (
        "\nReturn ONLY this JSON shape:\n"
        '{"queries": [{"q": "3 to 8 keywords", "language": "en"}]}\n'
        "Rules:\n"
        "- Search this task only. If the task is competition or pricing, "
        "do not search market size or growth.\n"
        "- Put named products from the task in the queries "
        "(for example GitHub Copilot, Amazon Q Developer, Cursor).\n"
        "- Keywords only. Do not paste the task sentence.\n"
        "- Do not put the country or region in q; it is already in gl.\n"
        "- Do not add tickers, series IDs, or indicator codes.\n"
    )
    if scope_note:
        prompt += f"- Target scope: {scope_note}\n"
    if "again in" in scope_note:
        prompt += "- Include at least one English query and one local-language query.\n"
    return prompt


def usefulness_prompt(task_description: str, result_str: str) -> str:
    return (
        f"Research task: {task_description}\n\n"
        f"Ranked search hits:\n{result_str[:3000]}\n\n"
        "Is this result USEFUL for the research task?\n"
        "USEFUL if at least one hit states a fact that answers this task: "
        "a named product, price, competitor, customer need, rule, or figure.\n"
        "Ignore other topics from a broader research program. "
        "A GitHub Copilot or Amazon Q pricing page is useful for a competition/pricing task "
        "even if it does not mention our company.\n"
        "NOT_USEFUL only when none of the hits answer this task "
        "(empty, a different industry, or only an unrelated market-size overview).\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        f"{schema_text(Usefulness)}\n"
    )


def analysis_prompt(
    task_description: str,
    goal: str,
    context: str,
    context_note: str,
    context_block: str,
) -> str:
    return (
        "You are a market research analyst.\n\n"
        f"RESEARCH TASK: {task_description}\n"
        f"OVERALL GOAL: {goal}\n"
        f"BUSINESS CONTEXT: {context[:200]}\n\n"
        f"{context_note}\n{context_block[:5000]}\n\n"
        "Based ONLY on the source text above:\n"
        "1. Write 1 to 3 claims. Each claim is one specific fact: a number, a named rule, "
        "a named product, or a price.\n"
        "2. Each claim has its own quote copied from the source that supports that claim.\n"
        "3. If the source has no such fact, return an empty claims list.\n"
        "4. Do not write 'Data retrieved for' and do not restate the task.\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        f"{schema_text(AnalysisReply)}\n"
    )
