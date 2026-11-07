"""Seed all EDRAK agent prompt templates into the Langfuse Prompt Management catalog.

Run this script to upload initial prompt versions into your self-hosted Langfuse UI:
    python scripts/seed_langfuse_prompts.py
"""

import sys
from pathlib import Path

# Add backend/src to path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "backend" / "src"))

from edrak.core.config import settings
from edrak.core.observability import get_langfuse_client

# 1. Orchestration
from edrak.orchestration.prompts import (
    PLANNER_SYSTEM_PROMPT,
    REQUEST_BUILDER_SYSTEM_PROMPT,
)

# 2. Internal Intelligence
from edrak.agents.internal_intelligence.prompts import (
    INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
    INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
)

# 3. Cross-Signal Intelligence
from edrak.CrossSignal.prompts import (
    CROSS_SIGNAL_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)

# 4. Decision Analysis
from edrak.DecisionAnalysis.prompts import (
    DECISION_ANALYSIS_SYSTEM_PROMPT,
)

# 5. Metrics Extraction
from edrak.agents.metrics.prompts import (
    FIND_NUMBERS,
)

# 6. Customer Trends
from edrak.agents.customer_trends.prompts import (
    PLAN_QUERIES,
    WRITE_FINDINGS,
    WRITE_HEADLINE,
)

VERIFICATION_REVIEW_PROMPT = """You are checking whether a research claim is supported by the source text it was summarized from.
Use only that source text. Do not add outside knowledge. Do not judge publisher quality or URL class.

CLAIM:
{{statement}}

SAVED SOURCE TEXT:
{{source_text}}

Return ONLY valid JSON with these keys:
{
  "supported": true,
  "contradictions": ["one sentence each, or an empty list"],
  "invented_details": ["numbers, dates, prices, or percentages in the claim that are not in the source, or an empty list"]
}

supported: true when the core of the claim is stated or clearly implied by the source text, even if it is worded differently or summarized. false only when the source is about something else, or the main fact is absent.
contradictions: list a conflict ONLY when a source states the opposite of the claim about the same product and attribute. Different plans, tiers, preview vs general availability, or other facts that can all be true are NOT contradictions.
invented_details: list each price, percentage, or year in the claim that does not appear in the saved source text, allowing for formatting differences (10K = 10,000; three = 3). Ignore other numbers such as version names.
"""

ALL_EDRAK_PROMPTS = [
    # Orchestration
    {
        "name": "edrak-planner-prompt",
        "prompt": PLANNER_SYSTEM_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["orchestration", "planner"],
        "commit_message": "Control-plane research decomposition planner",
    },
    {
        "name": "edrak-request-builder-prompt",
        "prompt": REQUEST_BUILDER_SYSTEM_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["orchestration", "parser"],
        "commit_message": "Unstructured business query parser into structured request",
    },
    # Internal Intelligence (Tri-Modal)
    {
        "name": "edrak-internal-query-planner",
        "prompt": INTERNAL_QUERY_PLANNING_SYSTEM_PROMPT,
        "labels": ["production", "v2.0", "optimized"],
        "tags": ["internal_intelligence", "retrieval", "feat-optimized"],
        "commit_message": "Optimized: query formulation up to 10 queries covering strict checklist",
    },
    {
        "name": "edrak-internal-synthesis",
        "prompt": INTERNAL_SYNTHESIS_SYSTEM_PROMPT,
        "labels": ["production", "v2.0", "optimized"],
        "tags": ["internal_intelligence", "synthesis", "feat-optimized"],
        "commit_message": "Optimized: Specificity Gate (Rule 7), concrete metrics/models, explicit per-topic gaps",
    },
    # Cross-Signal Intelligence
    {
        "name": "edrak-cross-signal-synthesis",
        "prompt": CROSS_SIGNAL_SYSTEM_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["cross_signal", "synthesis"],
        "commit_message": "Multi-agent cross-domain signal reconciliation and convergence",
    },
    {
        "name": "edrak-cross-signal-summary",
        "prompt": SUMMARY_SYSTEM_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["cross_signal", "summary"],
        "commit_message": "Executive cross-signal synthesis summary",
    },
    # Decision Analysis
    {
        "name": "edrak-decision-analysis",
        "prompt": DECISION_ANALYSIS_SYSTEM_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["decision_analysis", "strategy"],
        "commit_message": "Opportunity assessment and decision support recommendations",
    },
    # Metrics
    {
        "name": "edrak-metrics-finder",
        "prompt": FIND_NUMBERS,
        "labels": ["production", "v1.0"],
        "tags": ["metrics", "extraction"],
        "commit_message": "Strict quantitative metric and KPI extraction from text",
    },
    # Verification Engine
    {
        "name": "edrak-verification-review",
        "prompt": VERIFICATION_REVIEW_PROMPT,
        "labels": ["production", "v1.0"],
        "tags": ["verification", "hallucination_check"],
        "commit_message": "Claim verification and hallucination detection against raw evidence",
    },
    # Customer Trends
    {
        "name": "edrak-customer-trends-planner",
        "prompt": PLAN_QUERIES,
        "labels": ["production", "v1.0"],
        "tags": ["customer_trends", "planner"],
        "commit_message": "Query formulation across reviews, demand, and social signals",
    },
    {
        "name": "edrak-customer-trends-writer",
        "prompt": WRITE_FINDINGS,
        "labels": ["production", "v1.0"],
        "tags": ["customer_trends", "synthesis"],
        "commit_message": "Customer sentiment and customer voice findings synthesis",
    },
    {
        "name": "edrak-customer-trends-headline",
        "prompt": WRITE_HEADLINE,
        "labels": ["production", "v1.0"],
        "tags": ["customer_trends", "summary"],
        "commit_message": "Customer signal executive headline generation",
    },
]


def seed_prompts():
    print(f"Connecting to Langfuse at {settings.LANGFUSE_HOST}...")
    client = get_langfuse_client()
    if client is None:
        print("[ERROR] Langfuse client could not be initialized.")
        print("Please ensure LANGFUSE_ENABLED=true and LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set in .env")
        return

    success_count = 0
    for item in ALL_EDRAK_PROMPTS:
        name = item["name"]
        try:
            print(f"Uploading prompt: '{name}' [{', '.join(item['tags'])}]...")
            client.create_prompt(
                name=name,
                prompt=item["prompt"],
                labels=item["labels"],
                tags=item["tags"],
                commit_message=item["commit_message"],
            )
            print(f" -> OK: '{name}' registered with labels {item['labels']}.")
            success_count += 1
        except Exception as exc:
            print(f" -> [WARNING] Could not register '{name}': {exc}")

    print(f"\nSuccessfully registered {success_count}/{len(ALL_EDRAK_PROMPTS)} prompts into Langfuse!")
    print("Open your Langfuse UI at http://localhost:3000 and click 'Prompts' in the left menu to view them all.")


if __name__ == "__main__":
    seed_prompts()
