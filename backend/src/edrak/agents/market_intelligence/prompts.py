from edrak.agents.market_intelligence.schemas import (
    AnalysisClaim,
    TaskPlan,
    Usefulness,
    schema_text,
)


def tool_catalog_lines(tools) -> str:
    return "\n".join(
        f"  {tool.name}: {(tool.description or '').split(chr(10))[0].strip()}"
        for tool in tools
    )


def task_planner_prompt(goal: str, context: str, tool_catalog: str) -> str:
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
        "3. For each task, suggest the single best tool from the list above.\n"
        "4. Order tasks from most important to least important.\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        f"{schema_text(TaskPlan)}\n"
    )


def tool_arg_prompt(
    tool_name: str,
    tool_description: str,
    task_description: str,
    context: str,
    goal: str,
    attempt: int,
) -> str:
    prompt = (
        f"You are generating search arguments for the tool: {tool_name}\n\n"
        f"Tool description: {tool_description}\n\n"
        f"Research task: {task_description}\n"
        f"Business context: {context[:300]}\n"
        f"Overall goal: {goal[:200]}\n"
    )
    if attempt > 0:
        prompt += (
            f"\nPrevious attempt #{attempt} returned empty or irrelevant data. "
            "Generate DIFFERENT query keywords or parameters to try a fresh angle."
        )
    prompt += (
        "\n\nGenerate the best query arguments for this tool and task.\n"
        "Return ONLY valid JSON matching the tool's parameter schema.\n"
        "Examples:\n"
        '  list queries:  {"queries": ["AI cybersecurity market 2024", "enterprise threat detection"]}\n'
        '  series IDs:    {"series_ids": ["CPIAUCSL", "FEDFUNDS"]}\n'
        '  tickers:       {"tickers": ["PANW", "CRWD"]}\n'
        '  worldbank:     {"country": "USA", "indicator_codes": ["NY.GDP.MKTP.KD.ZG"]}'
    )
    return prompt


def usefulness_prompt(task_description: str, result_str: str) -> str:
    return (
        f"Research task: {task_description}\n\n"
        f"Tool result (first 2000 chars):\n{result_str[:2000]}\n\n"
        "Is this result USEFUL for the research task?\n"
        "USEFUL = contains relevant facts, data, or information about the topic.\n"
        "NOT_USEFUL = empty, off-topic, error messages, or clearly irrelevant.\n\n"
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
        "Based ONLY on the information provided above:\n"
        "1. Extract the most relevant and specific facts related to the research task.\n"
        "2. Write a concise, evidence-backed claim or summary (2-4 sentences).\n"
        "3. Do NOT add information not present in the source data.\n"
        "4. Do NOT invent statistics or quotes.\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        f"{schema_text(AnalysisClaim)}\n"
    )
