def review_prompt(statement: str, source_text: str) -> str:
    return (
        "You are checking whether a research claim is supported by the source text "
        "it was summarized from.\n"
        "Use only that source text. Do not add outside knowledge. "
        "Do not judge publisher quality or URL class.\n\n"
        f"CLAIM:\n{statement}\n\n"
        f"SAVED SOURCE TEXT:\n{source_text}\n\n"
        "Return ONLY valid JSON with these keys:\n"
        "{\n"
        '  "supported": true,\n'
        '  "contradictions": ["one sentence each, or an empty list"],\n'
        '  "invented_details": ["numbers, dates, prices, or percentages in the claim that are not in the source, or an empty list"]\n'
        "}\n\n"
        "supported: true when the core of the claim is stated or clearly implied by the source text, "
        "even if it is worded differently or summarized. "
        "false only when the source is about something else, or the main fact is absent.\n"
        "contradictions: list a conflict ONLY when a source states the opposite of the claim "
        "about the same product and attribute. Different plans, tiers, preview vs general "
        "availability, or other facts that can all be true are NOT contradictions.\n"
        "invented_details: list each price, percentage, or year in the claim that does not "
        "appear in the saved source text, allowing for formatting differences "
        "(10K = 10,000; three = 3). Ignore other numbers such as version names.\n"
    )