"""One set of names for the things every use case talks about.

The same idea has the same name everywhere, in every use case, on every screen and in every prompt:

  competitors       companies the user's company is compared against
  markets           places (countries, regions, cities)
  offering          the product or service being assessed or launched
  target customers  who the offering is for

The frontend mirrors these labels in frontend/src/lib/vocabulary.ts.
"""

from __future__ import annotations

from .request import BusinessContext, BusinessRequest, CompanyProfile, UseCase
from .task import WorkerType

USE_CASE_LABELS: dict[UseCase, str] = {
    UseCase.COMPETITIVE_INTELLIGENCE: "Competitive intelligence",
    UseCase.MARKET_ENTRY_EXPANSION: "Market entry and expansion",
    UseCase.PRODUCT_LAUNCH: "Product launch",
}

USE_CASE_QUESTIONS: dict[UseCase, str] = {
    UseCase.COMPETITIVE_INTELLIGENCE: "What are competitors doing, and what does it mean for us?",
    UseCase.MARKET_ENTRY_EXPANSION: "What does entering this market look like for us?",
    UseCase.PRODUCT_LAUNCH: "What does launching this offering look like for us?",
}

WORKER_LABELS: dict[WorkerType, str] = {
    WorkerType.INTERNAL_INTELLIGENCE: "Internal",
    WorkerType.COMPETITOR_INTELLIGENCE: "Competitors",
    WorkerType.MARKET_INTELLIGENCE: "Market",
    WorkerType.CUSTOMER_TRENDS: "Customers and trends",
}

TERMS = {
    "competitors": "Competitors",
    "markets": "Markets",
    "offering": "Offering",
    "target_customers": "Target customers",
}


def competitor_names(context: BusinessContext) -> list[str]:
    """Competitor names from the standard field, falling back to `targets`."""
    names = [c.name for c in context.competitors]
    for t in context.targets:
        if t.lower() not in {n.lower() for n in names}:
            names.append(t)
    return names


def markets_of(request_or_context: BusinessRequest | BusinessContext) -> list[str]:
    context = request_or_context.business_context if isinstance(request_or_context, BusinessRequest) else request_or_context
    if context.markets:
        return list(context.markets)
    if isinstance(request_or_context, BusinessRequest):
        extra = request_or_context.extras.get("markets")
        if isinstance(extra, list):
            return [str(m) for m in extra if str(m).strip()]
    return []


def describe_context(company: CompanyProfile, context: BusinessContext) -> str:
    """The standard inputs as plain lines, for prompts. Empty items are left out."""
    lines = [f"Use case: {USE_CASE_LABELS[context.use_case]}", f"Our company: {company.name}"]
    if company.products:
        lines.append(f"Our offerings: {', '.join(company.products)}")
    competitors = competitor_names(context)
    if competitors:
        lines.append(f"{TERMS['competitors']}: {', '.join(competitors)}")
    if context.markets:
        lines.append(f"{TERMS['markets']}: {', '.join(context.markets)}")
    if context.offering:
        lines.append(f"{TERMS['offering']}: {context.offering}")
    if context.target_customers:
        lines.append(f"{TERMS['target_customers']}: {context.target_customers}")
    if context.focus_areas:
        lines.append(f"Focus: {', '.join(context.focus_areas)}")
    if context.time_window_days:
        lines.append(f"Time window: last {context.time_window_days} days")
    return "\n".join(lines)
