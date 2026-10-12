"""Turning a guided form into a BusinessRequest and a draft plan."""

from __future__ import annotations

import logging
from typing import Any, Optional

from edrak.api.schemas import AnalysisForm
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    ResearchPlan,
    ResearchTask,
    TriggerType,
    UseCase,
    WorkerType,
)
from edrak.db.repositories.company import industry_text, profile_contract_fields

logger = logging.getLogger(__name__)

# Source toggles on the form -> allowed source types stored on the request.
TOGGLE_TO_SOURCE_TYPES = {
    "internal_files": ["internal_kb"],
    "competitor_web": ["competitor_sites", "web_search"],
    "news_open_data": ["news", "open_data"],
    "reviews_social": ["social_forums"],
}

FOCUS_PHRASE = {
    "features": "Product features",
    "pricing": "Pricing and packaging",
    "positioning": "Positioning",
    "sentiment": "Customer sentiment",
    "demand": "Demand and market size",
    "regulation": "Regulation and licensing",
    "competition": "Competition",
    "customers": "Customers",
}


def allowed_source_types(form: AnalysisForm) -> list[str]:
    out: list[str] = []
    for toggle, types in TOGGLE_TO_SOURCE_TYPES.items():
        if form.sources.get(toggle, True):
            out += [t for t in types if t not in out]
    return out or [t for types in TOGGLE_TO_SOURCE_TYPES.values() for t in types]


def workers_for(source_types: list[str]) -> list[WorkerType]:
    """Which workers may run, given what Edrak is allowed to read for this analysis."""
    allowed = set(source_types)
    workers: list[WorkerType] = []
    if "internal_kb" in allowed:
        workers.append(WorkerType.INTERNAL_INTELLIGENCE)
    if allowed & {"competitor_sites", "web_search"}:
        workers.append(WorkerType.COMPETITOR_INTELLIGENCE)
    if allowed & {"news", "open_data"}:
        workers.append(WorkerType.MARKET_INTELLIGENCE)
    if "social_forums" in allowed:
        workers.append(WorkerType.CUSTOMER_TRENDS)
    return workers or list(WorkerType)


def derive_title(form: AnalysisForm) -> str:
    if form.use_case == UseCase.PRODUCT_LAUNCH and form.offering.strip():
        return form.offering.strip()[:64]
    if form.use_case == UseCase.MARKET_ENTRY_EXPANSION and form.market.strip():
        return f"Expansion into {form.market.strip()[:48]}"
    if form.competitors:
        comps = [c for c in form.competitors if c.strip()][:2]
        extra = len(form.competitors) - 2
        return (f"Competitors: {', '.join(comps)}" + (f" +{extra}" if extra > 0 else ""))[:64]
    return (form.goal.strip() or "Untitled analysis")[:64]


def standard_inputs(form: AnalysisForm, company: dict[str, Any]) -> dict[str, Any]:
    """Competitors, markets, offering and target customers, under one set of names for every use case.

    Competitors come from the form; when the form names none, the competitors the company tracks
    (from onboarding or the watchlist) are used.
    """
    competitors = []
    known = {c["name"].lower(): c for c in company.get("competitors") or []}
    names = [n for n in form.competitors if n.strip()] or [c["name"] for c in company.get("competitors") or []]
    for name in names:
        name = name.strip()
        if not name or name.lower() in {c["name"].lower() for c in competitors}:
            continue
        entity = known.get(name.lower())
        competitors.append({
            "name": name,
            "entity_id": entity["entity_id"] if entity else None,
            "website": (entity or {}).get("website")
            or next((l["url"] for l in (entity or {}).get("links", []) if l["platform"] == "website"), None)
            or (f"https://{entity['website_domain']}" if (entity or {}).get("website_domain") else None),
            "social_accounts": [
                {"platform": l["platform"], "url": l["url"]}
                for l in (entity or {}).get("links", []) if l["platform"] != "website"
            ],
            "discovered_by": (entity or {}).get("discovered_by", "user"),
        })
    markets = [m.strip() for m in form.market.replace(";", ",").split(",") if m.strip()]
    if not markets and form.use_case != UseCase.MARKET_ENTRY_EXPANSION:
        # Without a market named, the analysis is about where the company already sells.
        markets = [m["name"] for m in company.get("markets") or [] if m.get("relation") == "serves"]
    return {
        "competitors": competitors,
        "markets": markets,
        "offering": form.offering.strip() or None,
        "target_customers": (form.target_customers or "").strip() or None,
    }


def build_request(
    *,
    request_id: str,
    form: AnalysisForm,
    company: dict[str, Any],
    workspace_id: str,
    trigger: TriggerType = TriggerType.ON_DEMAND,
    rejection_feedback: Optional[str] = None,
) -> tuple[BusinessRequest, dict[str, Any]]:
    inputs = standard_inputs(form, company)
    profile_fields = profile_contract_fields(company)
    known_profile = set(CompanyProfile.model_fields)
    profile = CompanyProfile(
        **{k: v for k, v in profile_fields.items() if k in known_profile},
        workspace_id=workspace_id,
    )
    focus_areas = [FOCUS_PHRASE.get(f, f) for f in form.focus if f in FOCUS_PHRASE]
    context: dict[str, Any] = {
        "use_case": form.use_case,
        # targets is the contract's list of companies to compare against: competitors only.
        "targets": [c["name"] for c in inputs["competitors"]],
        "focus_areas": focus_areas,
        "time_window_days": form.time_window_days,
        "trigger": trigger,
    }
    context_fields = set(BusinessContext.model_fields)
    for key in ("competitors", "markets", "offering", "target_customers"):
        if key in context_fields:
            context[key] = inputs[key]
    extras: dict[str, Any] = {
        "allowed_source_types": allowed_source_types(form),
        # Until every worker reads the standard fields, they also travel here.
        "markets": inputs["markets"],
        "offering": inputs["offering"],
        "target_customers": inputs["target_customers"],
    }
    if rejection_feedback:
        extras["rejected_plan_feedback"] = rejection_feedback
    request = BusinessRequest(
        request_id=request_id,
        goal=form.goal.strip() or "Strategic assessment",
        company_profile=profile,
        business_context=BusinessContext(**context),
        extras=extras,
    )
    return request, inputs


def draft_plan(request: BusinessRequest, allowed_workers: list[WorkerType]) -> ResearchPlan:
    """The planner's plan, kept to the allowed workers. Falls back to one task per worker."""
    try:
        from edrak.orchestration.planner import LlmPlanner

        full = LlmPlanner().plan(request)
        tasks = [t for t in full.tasks if t.worker in allowed_workers]
        if tasks:
            return ResearchPlan(request_id=request.request_id, tasks=tasks, rationale=full.rationale)
    except Exception as exc:  # noqa: BLE001 - the deterministic plan below still lets the user proceed
        logger.warning("Planner failed, using the standard plan: %s", exc)

    focus = ", ".join(request.business_context.focus_areas) or "the key signals for this decision"
    goals = {
        WorkerType.INTERNAL_INTELLIGENCE: ("What do our own documents say about this?",
                                           "Read internal documents for capabilities, constraints and prior decisions."),
        WorkerType.COMPETITOR_INTELLIGENCE: ("What are the competitors doing?",
                                             "Compare competitors' offerings, pricing and positioning from public sources."),
        WorkerType.MARKET_INTELLIGENCE: ("What is happening in this market?",
                                         "Summarise market trends, demand signals and regulation from news and open data."),
        WorkerType.CUSTOMER_TRENDS: ("What do customers say and search for?",
                                     "Read reviews, social posts and search interest for demand and complaints."),
    }
    from edrak.metrics.planning import requirements_for
    ctx = request.business_context
    competitors = [c.name for c in ctx.competitors] or list(ctx.targets)
    tasks = [
        ResearchTask(
            parent_request_id=request.request_id,
            worker=w,
            goal=goals[w][0] + " " + request.goal,
            focus=f"{goals[w][1]} Focus on {focus}.",
            company_profile=request.company_profile,
            business_context=request.business_context,
            requirements=requirements_for(
                w.value, ctx.use_case.value, request.company_profile.name,
                competitors, list(ctx.markets), ctx.offering or "",
            ),
        )
        for w in allowed_workers
    ]
    return ResearchPlan(
        request_id=request.request_id,
        tasks=tasks,
        rationale=f"Each of the {len(tasks)} workers investigates its own angle in parallel; verification then checks what they found.",
    )


def estimate_minutes(plan: ResearchPlan) -> int:
    """Rough wall time: workers run in parallel, then verification and synthesis."""
    return 6 + 2 * len({t.worker for t in plan.tasks})


def discover_for(company: dict[str, Any], markets: list[str], offering: Optional[str]) -> list[dict[str, Any]]:
    """Competitors found on the web for an analysis that named none (blocking; run in a thread)."""
    from edrak.agents.competitor_intelligence.discovery import discover_competitors

    found = discover_competitors(
        company=company.get("name") or "",
        description=company.get("description") or None,
        offerings=[*(company.get("offerings") or []), *([offering] if offering else [])],
        industry=industry_text(company) or None,
        markets=markets,
        target_customers=company.get("target_customers") or None,
        exclude=company.get("aliases") or [],
        limit=4,
    )
    return [
        {
            "name": c["name"],
            "entity_id": None,
            "website": c.get("website"),
            "social_accounts": [{"platform": l["platform"], "url": l["url"]} for l in c.get("links", [])],
            "discovered_by": "agent",
            "discovery_reason": c.get("reason"),
            "discovery_confidence": c.get("confidence"),
            "links": c.get("links", []),
        }
        for c in found
    ]
