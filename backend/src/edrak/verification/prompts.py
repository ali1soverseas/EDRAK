def missing_information_prompt(statement: str, evidence_facts: list[str]) -> str:
    facts = "\n".join(f"- {fact}" for fact in evidence_facts) or "- (none)"
    return (
        "You are verifying a research claim.\n\n"
        f"CLAIM:\n{statement}\n\n"
        f"EVIDENCE FACTS:\n{facts}\n\n"
        "List specific missing details required to fully support the claim.\n"
        "Return ONLY valid JSON: {\"missing_information\": [\"...\"]}"
    )
