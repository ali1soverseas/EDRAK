def review_prompt(statement: str, source_text: str) -> str:
    return (
        "You are checking a research claim against the source text it was summarized from.\n"
        "Use only that source text. Do not add outside knowledge.\n\n"
        f"CLAIM:\n{statement}\n\n"
        f"SAVED SOURCE TEXT:\n{source_text}\n\n"
        "Return ONLY valid JSON with these keys:\n"
        "{\n"
        '  "evidence_quality": "high" | "medium" | "low",\n'
        '  "contradictions": ["one sentence each, or an empty list"],\n'
        '  "invented_details": ["numbers, dates, prices, or percentages in the claim that are not in the source, or an empty list"]\n'
        "}\n\n"
        "evidence_quality:\n"
        "- high: official documentation, a company announcement, a pricing page, or a named industry report\n"
        "- medium: a news article or other identifiable publication\n"
        "- low: a search snippet, a blog, or text that does not contain the claim\n\n"
        "contradictions: list a conflict when two saved sources disagree, or when a source says the opposite of the claim.\n"
        "invented_details: list each number, date, price, or percentage in the claim that does not appear in the saved source text.\n"
    )
