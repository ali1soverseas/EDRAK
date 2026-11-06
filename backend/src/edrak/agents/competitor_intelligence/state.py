"""State, schemas and configuration for the competitor research graph."""

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from ...contracts.task import WorkerType


# ============================================================
# CONFIGURATION (one definition of each limit)
# ============================================================

MAX_RESULTS_PER_QUERY = 3

# The loop is bounded three ways: stages, queries and progress.
MAX_RESEARCH_STAGES = 3
MAX_REQUIREMENTS_PER_COMPETITOR = 8
MAX_QUERIES_PER_REQUIREMENT = 3
MAX_QUERIES_TOTAL = 40
# A further stage runs only if the last one added at least this many grounded findings.
MIN_NEW_FINDINGS_TO_CONTINUE = 2

MAX_STORE_CHARS = 6000
SOURCE_CHARS = 3500            # per source, for synthesis and grounding
MAX_EVIDENCE_CHARS = 60000     # per synthesis call; sources are split into batches, never truncated
MAX_EVIDENCE_PER_FINDING = 3
DUPLICATE_SIMILARITY = 0.85
INCLUDE_RAW_FINDINGS = False   # also put the raw per-finding list into the final report

CURRENT_YEAR = date.today().year

STAGE_STRATEGY = {
    1: "official_sources",
    2: "discovery",
    3: "targeted_missing_requirements",
}

OUTPUT_DIR = Path("edrak_competitor_outputs")

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

FindingCategoryName = Literal[
    "product_feature", "pricing_packaging", "positioning", "target_customer", "strength", "gap",
    "market_signal", "customer_sentiment", "risk", "opportunity", "other",
]

# The 14 aspects of the competitor profile (edrak/knowledge/profile_aspects.py), plus the older
# category names, which map to their aspect.
RequirementCategory = Literal[
    "company_overview", "products_services", "technology", "pricing_commercial", "customers_market",
    "market_position", "financials", "partnerships", "go_to_market", "customer_experience", "strategy",
    "operations", "legal_risk", "recent_developments",
    "products", "technical_capabilities", "pricing", "customers", "revenue", "ecosystem", "other",
]


def _coerce_str_list(value):
    """Models sometimes send "" or a bare string where a list is expected."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return value


# ============================================================
# REQUIREMENTS
# ============================================================

class ResearchRequirement(BaseModel):
    """One researchable requirement. Every field except the question has a safe default, so a
    model that returns the older two-field shape (competitor, requirement) still validates."""

    requirement_id: str = Field(default="", description="Leave empty; assigned by the agent.")
    competitor: str = Field(default="", description="The competitor's name, exactly as listed.")
    research_question: str = Field(description="One precise, independently researchable question.")
    importance: Literal["critical", "high", "medium", "low"] = "medium"
    time_scope: str = Field(default="", description="The period that matters.")

    @model_validator(mode="before")
    @classmethod
    def _accept_older_shape(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("research_question") and data.get("requirement"):
            data = {**data, "research_question": data["requirement"]}
        if isinstance(data, dict) and data.get("importance") not in ("critical", "high", "medium", "low"):
            data = {**data, "importance": "medium"}
        return data

import hashlib
import logging

from typing import Any, Optional, Literal, List

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class RequirementJudgment(BaseModel):
    requirement_id: str

    status: Literal["covered", "missing"]

    score: int = Field(
        ge=0,
        le=100,
        description="Evidence relevance and coverage score from 0 to 100."
    )

    best_evidence: list[str] = Field(
        default_factory=list,
        description="IDs of the most relevant retrieved facts and passages."
    )

    reason: str = ""

    missing_information: str = ""


class ResearchRequirements(BaseModel):
    requirements: List[ResearchRequirement] = Field(default_factory=list)

from typing import Literal
from pydantic import BaseModel, Field


class EvidenceAssessment(BaseModel):
    evidence_id: str

    relevance: Literal[
        "direct",
        "supporting",
        "irrelevant"
    ] = "irrelevant"

    supported_claim: str = ""
    reason: str = ""

    @field_validator("relevance", mode="before")
    @classmethod
    def _relevance(cls, v):
        v = str(v).strip().lower()
        return v if v in ("direct", "supporting") else "irrelevant"

    @field_validator("supported_claim", "reason", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)


class RequirementGap(BaseModel):
    requirement_id: str

    status: Literal[
        "covered",
        "missing"
    ] = "missing"

    score: int = Field(default=0, ge=0, le=100)

    evidence_assessments: list[EvidenceAssessment] = Field(
        default_factory=list
    )

    best_evidence: list[str] = Field(
        default_factory=list
    )

    covered_components: list[str] = Field(
        default_factory=list
    )

    missing_components: list[str] = Field(
        default_factory=list
    )

    reason: str = ""

    missing_information: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v):
        return "covered" if str(v).strip().lower() == "covered" else "missing"

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, v):
        try:
            return max(0, min(100, int(float(v))))
        except (TypeError, ValueError):
            return 0

    @field_validator(
        "evidence_assessments", "best_evidence", "covered_components", "missing_components", mode="before"
    )
    @classmethod
    def _as_list(cls, v):
        if v is None:
            return []
        if isinstance(v, (str, dict)):
            return [v] if v else []
        return list(v)

    @field_validator("reason", "missing_information", mode="before")
    @classmethod
    def _joined(cls, v):
        if isinstance(v, (list, tuple)):
            return "; ".join(str(x) for x in v)
        return "" if v is None else str(v)


class GapAssessment(BaseModel):
    requirements: list[RequirementGap] = Field(
        default_factory=list
    )
# ============================================================
# QUERIES
# ============================================================

class SearchQuery(BaseModel):
    requirement_id: str = Field(description="The id (e.g. R3) of the target requirement")
    query: str
    purpose: str = ""


class SearchQueryList(BaseModel):
    queries: List[SearchQuery] = Field(default_factory=list)


# ============================================================
# FINDINGS
# LLMFinding is what the model sees (no `evidence` field); Python attaches the real evidence.
# ============================================================

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

    @field_validator("category", mode="before")
    @classmethod
    def _known_category(cls, v):
        return v if v in FindingCategoryName.__args__ else "other"


class FindingList(BaseModel):
    findings: List[LLMFinding] = Field(default_factory=list)


# ============================================================
# COMPARISON AND PROFILES
# ============================================================

class CompetitorAssessment(BaseModel):
    competitor: str
    summary: str = Field(description="2-3 sentences comparing this competitor with the target company on this dimension")
    relative_to_target: Literal["broader", "comparable", "narrower", "different_approach", "not_established"]
    confidence: Literal["high", "medium", "low"]
    requirement_ids: List[str] = Field(
        default_factory=list, description="Ids (R1, R2...) of THIS competitor's requirements the assessment relies on"
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

    @field_validator("areas_where_target_appears_ahead", "areas_where_competitors_appear_ahead", "limitations",
                     mode="before")
    @classmethod
    def _lists(cls, v):
        return _coerce_str_list(v)


class ConsolidatedRequirement(BaseModel):
    requirement_id: str
    summary: str = Field(description="2-4 sentence merged answer using only the supplied findings")
    release_status: Literal["generally_available", "preview_or_beta", "announced_or_planned", "mixed", "not_stated"] = "not_stated"
    evidence_source_ids: List[str] = Field(
        default_factory=list, description="Source IDs (S1, S2...) that support the summary. IDs only."
    )

    @field_validator("evidence_source_ids", mode="before")
    @classmethod
    def _ids_to_list(cls, v):
        return _coerce_str_list(v)


class CompetitorProfile(BaseModel):
    overview: str = Field(description="3-5 sentence factual overview of this competitor's relevant offering")
    requirements: List[ConsolidatedRequirement] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)

    @field_validator("caveats", mode="before")
    @classmethod
    def _caveats_to_list(cls, v):
        return _coerce_str_list(v)


# ============================================================
# COMPARISON DIMENSIONS
# ============================================================

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


# ============================================================
# STATE (every key a node reads or writes is declared)
# ============================================================

class CompetitorState(TypedDict, total=False):
    # the task (set by _task_to_initial_state)
    task_id: str
    attempt: int
    today: str
    company: str
    company_profile: Dict[str, Any]
    business_context: Dict[str, Any]
    research_goal: str
    research_focus: Any
    competitors: List[str]
    competitor_info: Dict[str, Dict[str, Any]]

    # requirements
    research_requirements: List[Dict[str, Any]]
    missing_requirement_competitors: List[str]

    # knowledge base and gaps (gap_nodes.py)
    kb_evidence: Dict[str, Dict[str, Any]]
    kb_profiles: Dict[str, Dict[str, Any]]
    kb_coverage: Dict[str, Dict[str, Any]]
    gap_requirements: List[str]
    gap_queries_dropped_by_budget: int

    # the research loop
    research_stage: int
    research_strategy: str
    search_queries: List[Dict[str, Any]]
    executed_queries: List[Dict[str, Any]]
    search_results: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    new_findings: List[Dict[str, Any]]
    verified_findings: List[Dict[str, Any]]
    missing_information: List[Dict[str, Any]]
    requirement_checks: List[Dict[str, Any]]
    new_sources_this_stage: int
    new_verified_this_stage: int
    dropped_sources: List[Dict[str, Any]]
    next_stage_plan: Optional[Dict[str, Any]]

    # output
    final_report: Optional[Dict[str, Any]]
    comparison: Optional[Dict[str, Any]]
