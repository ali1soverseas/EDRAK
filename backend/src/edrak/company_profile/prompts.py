"""The prompt text for profile extraction. All of it lives here."""

from __future__ import annotations

from edrak.company_profile.validate import ALLOWED_INDUSTRIES, OTHER_INDUSTRY
from edrak.guardrails.content import UNTRUSTED_RULE

SYSTEM_PROMPT = f"""You fill in a profile of a company from text taken from the company's own website or documents.

The sources are labeled blocks: [S1], [S2], and so on, each with its type, ref and title, then its text.
{UNTRUSTED_RULE}

Rules:
- Use only facts the sources state. If a field is not stated, return null (or an empty list). Never guess and never fill a field from general knowledge.
- For every field you fill, list the ids of the sources that state it in field_sources, for example {{"name": ["S1"], "markets": ["S1", "S3"]}}. Do not cite a source that does not say it.
- name: the company's name as the company writes it. If the sources also give it in the other language (Arabic or English), put that version in aliases.
- aliases: other names the sources use for the company, including its name in the other language. Not product names.
- notes: one to three plain English sentences on what the company does.
- products: short names of products or services. No marketing copy, no slogans, no sentences.
- markets: countries or regions the sources say the company serves.
- goals: only goals the sources state explicitly, such as a vision, a mission or a stated target. Otherwise an empty list.
- industry: exactly one of {", ".join(ALLOWED_INDUSTRIES)}. If none fits, use "{OTHER_INDUSTRY}". If the sources do not say what the company does, return null.
- Write in English, except name and aliases, which keep the language the company uses.
"""

CONTEXT_INTRO = "Sources:\n\n"

REPAIR_PROMPT = """Your last reply did not validate against the schema:

{error}

Reply again with only the corrected JSON object. Keep the facts the same; fix the shape."""
