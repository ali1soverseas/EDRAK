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
        "supported: true only when the source states the claim. "
        "false when the source is about something else or does not contain the claim.\n"
        "contradictions: list a conflict when two saved sources disagree, "
        "or when a source says the opposite of the claim. "
        "Do not treat extra numbers in the claim as contradictions.\n"
        "invented_details: list each number, date, price, or percentage in the claim "
        "that does not appear in the saved source text.\n"
    )
