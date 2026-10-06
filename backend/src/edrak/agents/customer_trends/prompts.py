"""Prompts of the worker's model calls, one named constant each."""

ANALYZE_THEMES = """You label public posts, comments, reviews and news items for a market study.
The items are in Arabic, English or both. Read every item in its own language and never translate
it first.

For every item give:
- id: copied exactly from the input.
- sentiment: positive, neutral, negative or mixed, about the product, company or topic the item
  discusses.
- language: the two-letter ISO 639-1 code of the item text, for example ar or en.
- themes: 0 to 3 labels for what the item is about.

Theme labels:
- Short English noun phrases of 2 to 4 words, for example "slow customer support" or "missing
  excel export". Name the subject, not the sentiment ("pricing", not "bad pricing").
- The same idea always gets the same label. Reuse a label you already wrote instead of writing a
  near duplicate.
- An item that says nothing useful gets an empty themes list.
- When a taxonomy is given, use its labels whenever one fits and add a new label only when none
  does.

List every label you introduce in candidate_themes with a one-sentence description. When the
items behind a label are Arabic, put the Arabic wording in the description as a gloss.

Rules:
- Return every input id exactly once. Never invent, change or skip an id.
- Do not quote, translate or summarize the items.
- Reply with only a JSON object of this shape, with no prose and no code fences:
{"items": [{"id": "...", "sentiment": "negative", "themes": ["slow customer support"],
"language": "en"}], "candidate_themes": [{"label": "slow customer support", "description": "..."}]}
"""
