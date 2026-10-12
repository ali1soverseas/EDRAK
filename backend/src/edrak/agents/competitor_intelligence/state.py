"""State, schemas and configuration for the competitor research graph."""

from pathlib import Path
from typing import TypedDict, List, Dict, Any, Literal

from pydantic import BaseModel, Field, field_validator


# ============================================================
# CONFIG
# ============================================================

MAX_RESULTS_PER_QUERY = 3

# The loop is bounded three ways: stages, queries, and progress.
MAX_RESEARCH_STAGES = 3
MAX_QUERIES_PER_REQUIREMENT = 2
MAX_QUERIES_TOTAL = 24
MAX_REQUIREMENTS_PER_COMPETITOR = 5
# A further stage runs only if the last one added at least this many grounded findings.
MIN_NEW_FINDINGS_TO_CONTINUE = 2

MAX_STORE_CHARS = 6000

# One value for synthesis and grounding.
SOURCE_CHARS = 3500

# Evidence size per LLM call. Results are split into batches, never truncated.
MAX_EVIDENCE_CHARS = 60000

MAX_EVIDENCE_PER_FINDING = 3

DUPLICATE_SIMILARITY = 0.85

# Also dump the raw per-finding list into the final JSON (without source text).
INCLUDE_RAW_FINDINGS = False

STAGE_STRATEGY = {
    1: "discovery",
    2: "official_sources",
    3: "targeted_missing_requirements",
}

# ============================================================
# OUTPUT
# ============================================================

OUTPUT_DIR = Path("edrak_competitor_outputs")

FindingCategoryName = Literal[
    "product_feature", "pricing_packaging", "positioning", "target_customer", "strength", "gap",
    "market_signal", "customer_sentiment", "risk", "opportunity", "other",
]


# ============================================================
# PYDANTIC MODELS
# ============================================================

def _coerce_str_list(value):
    """LLMs sometimes send "" or a bare string where a list is expected."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return value


# ---- Requirements ------------------------------------------
class ResearchRequirement(BaseModel):
    competitor: str
    requirement: str


class ResearchRequirements(BaseModel):
    requirements: List[ResearchRequirement]


# ---- Queries -----------------------------------------------
class SearchQuery(BaseModel):
    requirement_id: str = Field(description="The id (e.g. R3) of the target requirement")
    query: str
    purpose: str


class SearchQueryList(BaseModel):
    queries: List[SearchQuery]


# ---- Findings ----------------------------------------------
# LLMFinding is what the model sees (no `evidence` field).
# Finding is the stored form; Python attaches the real evidence.
class LLMFinding(BaseModel):
    competitor: str
    requirement_id: str = Field(description="The id (e.g. R3) of the requirement this finding supports")
    finding: str
    category: FindingCategoryName = Field(
        default="other",
        description="The dimension this fact speaks to: product_feature, pricing_packaging, positioning, "
        "target_customer, market_signal, customer_sentiment, risk, opportunity, strength, gap or other.",
    )
    competitive_area: str = ""
    business_relevance: str = ""
    evidence_source_ids: List[str] = Field(
        default_factory=list,
        description="Source IDs such as S1, S2 copied from the SOURCE ID labels. IDs only, never URLs.",
    )

    @field_validator("evidence_source_ids", mode="before")
    @classmethod
    def _ids_to_list(cls, v):
        return _coerce_str_list(v)


class FindingList(BaseModel):
    findings: List[LLMFinding]


# ---- Target-vs-competitors comparison ----------------------
class CompetitorAssessment(BaseModel):
    
    competitor: str
    summary: str = Field(description="2-3 sentences comparing this competitor with the target company on this dimension")
    relative_to_target: Literal[
        "broader",
        "comparable",
        "narrower",
        "different_approach",
        "not_established",
    ]
    confidence: Literal["high", "medium", "low"]
    requirement_ids: List[str] = Field(
        default_factory=list,
        description="Ids (R1, R2...) of THIS competitor's requirements the assessment relies on",
    )

    @field_validator("requirement_ids", mode="before")
    @classmethod
    def _rids_to_list(cls, v):
        return _coerce_str_list(v)


class DimensionComparison(BaseModel):
    dimension: str
    target_position: str = Field(description="What the target company context documents for this dimension")
    assessments: List[CompetitorAssessment]
    takeaway: str


class ComparisonReport(BaseModel):
    executive_summary: str
    dimensions: List[DimensionComparison]
    areas_where_target_appears_ahead: List[str] = Field(default_factory=list)
    areas_where_competitors_appear_ahead: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)

    @field_validator(
        "areas_where_target_appears_ahead",
        "areas_where_competitors_appear_ahead",
        "limitations",
        mode="before",
    )
    @classmethod
    def _lists(cls, v):
        return _coerce_str_list(v)


# ---- Consolidated per-competitor profile -------------------
class ConsolidatedRequirement(BaseModel):
    requirement_id: str
    summary: str = Field(description="2-4 sentence merged answer using only the supplied findings")
    release_status: Literal[
        "generally_available",
        "preview_or_beta",
        "announced_or_planned",
        "mixed",
        "not_stated",
    ] = "not_stated"
    evidence_source_ids: List[str] = Field(
        default_factory=list,
        description="Source IDs (S1, S2...) that support the summary. IDs only.",
    )

    @field_validator("evidence_source_ids", mode="before")
    @classmethod
    def _ids_to_list(cls, v):
        return _coerce_str_list(v)


class CompetitorProfile(BaseModel):
    overview: str = Field(description="3-5 sentence factual overview of this competitor's relevant offering")
    requirements: List[ConsolidatedRequirement]
    caveats: List[str] = Field(default_factory=list)

    @field_validator("caveats", mode="before")
    @classmethod
    def _caveats_to_list(cls, v):
        return _coerce_str_list(v)


# ============================================================
# STATE
# ============================================================

class CompetitorState(TypedDict, total=False):
    task_id: str
    attempt: int
    company: str
    company_profile: Dict[str, Any]
    business_context: Dict[str, Any]
    competitors: List[str]
    competitor_info: Dict[str, Dict[str, Any]]   # name -> {website, aliases, discovered_by, reason, links}
    research_goal: str
    research_focus: str
    today: str

    research_stage: int
    research_strategy: str
    research_requirements: List[Dict[str, str]]  # each has id/competitor/requirement

    search_queries: List[Dict[str, str]]
    executed_queries: List[Dict[str, Any]]
    search_results: List[Dict[str, Any]]

    findings: List[Dict[str, Any]]
    new_findings: List[Dict[str, Any]]       # findings produced this stage
    verified_findings: List[Dict[str, Any]]

    missing_information: List[Dict[str, Any]]
    requirement_checks: List[Dict[str, Any]]

    new_sources_this_stage: int
    new_verified_this_stage: int
    dropped_sources: List[Dict[str, Any]]

    final_report: Dict[str, Any]


# ============================================================
# NODE 9: COMPARE TARGET COMPANY WITH COMPETITORS
# ============================================================

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# The comparison is made on the dimensions of the user's focus, plus these defaults per use case.
COMPARISON_DIMENSIONS_BY_USE_CASE = {
    "competitive_intelligence": [
        "Core capabilities relevant to the goal",
        "Pricing and packaging",
        "Target customers and positioning",
        "Availability (plans, regions, generally available vs preview vs announced)",
        "Integrations and ecosystem",
        "Recent changes",
    ],
    "market_entry_expansion": [
        "Presence in the target markets",
        "Offering and localisation for those markets",
        "Pricing and packaging in those markets",
        "Target customers and positioning",
        "Partnerships and distribution",
        "Recent changes",
    ],
    "product_launch": [
        "How each competitor addresses the problem the offering solves",
        "Pricing and packaging of comparable offerings",
        "Target customers and positioning",
        "Gaps and limitations people point out",
        "Availability (generally available vs preview vs announced)",
        "Recent changes",
    ],
}
COMPARISON_DIMENSIONS = COMPARISON_DIMENSIONS_BY_USE_CASE["competitive_intelligence"]


def comparison_dimensions(use_case: str, focus_areas: List[str]) -> List[str]:
    dims = list(COMPARISON_DIMENSIONS_BY_USE_CASE.get(use_case, COMPARISON_DIMENSIONS))
    for area in focus_areas:
        if area and not any(area.lower() in d.lower() or d.lower().startswith(area.lower()) for d in dims):
            dims.insert(1, area)
    return dims[:8]
