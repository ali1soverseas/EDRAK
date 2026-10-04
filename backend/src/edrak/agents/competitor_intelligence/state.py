"""State, schemas and configuration for the competitor research graph."""

from pathlib import Path
from typing import TypedDict, List, Dict, Any, Literal

from pydantic import BaseModel, Field, field_validator


# ============================================================
# CONFIG
# ============================================================

MAX_RESULTS_PER_QUERY = 5

MAX_RESEARCH_STAGES = 6

MAX_QUERIES_PER_REQUIREMENT = 2

MAX_STORE_CHARS = 6000

# One value for BOTH synthesis and verification (they used to differ: 3500 vs 3000)
SOURCE_CHARS = 3500

# Evidence size per LLM call. Results are split into batches, never truncated.
MAX_EVIDENCE_CHARS = 60000

VERIFY_BATCH_SIZE = 5

MAX_EVIDENCE_PER_FINDING = 3

DUPLICATE_SIMILARITY = 0.85

# A requirement only counts as fulfilled once at least one finding is backed
# by a first-party source (official docs, pricing, announcement, release notes).
REQUIRE_OFFICIAL_SOURCE = True

# Also dump the raw per-finding list into the final JSON (without source text).
INCLUDE_RAW_FINDINGS = False

OFFICIAL_DOMAINS = {
    "GitHub": ["docs.github.com", "github.blog", "github.com"],
    "Microsoft Azure DevOps": [
        "learn.microsoft.com",
        "azure.microsoft.com",
        "devblogs.microsoft.com",
        "visualstudio.microsoft.com",
    ],
    "Atlassian Bitbucket": [
        "bitbucket.org",
        "atlassian.com",
        "support.atlassian.com",
    ],
    "Amazon Web Services (AWS)": [
        "docs.aws.amazon.com",
        "aws.amazon.com",
        "aboutamazon.com",
    ],
    "Google": [
        "cloud.google.com",
        "developers.google.com",
        "blog.google",
        "ai.google.dev",
        "developers.googleblog.com",
    ],
}

# Keys MUST match the names in the competitors list.
COMPETITOR_SCOPE = {
    "GitHub": (
        "GitHub, GitHub Copilot, repositories, issues, pull requests, code "
        "review, coding agents and developer workflows."
    ),
    "Microsoft Azure DevOps": (
        "Azure DevOps, Azure Repos, Azure Boards, Azure Pipelines, Azure Test "
        "Plans and AI explicitly connected to Azure DevOps. Do NOT transfer "
        "capabilities from unrelated Microsoft products."
    ),
    "Atlassian Bitbucket": (
        "Bitbucket and AI capabilities explicitly connected to Bitbucket."
    ),
    "Amazon Web Services (AWS)": (
        "AWS software-development and developer-assistance products relevant "
        "to the research goal, not unrelated AWS services."
    ),
    "Google": (
        "Google's relevant developer and software-development products, not "
        "unrelated consumer AI products."
    ),
}

STAGE_STRATEGY = {
    1: "discovery",
    2: "official_sources",
    3: "deep_verification",
}

# ============================================================
# OUTPUT
# ============================================================

OUTPUT_DIR = Path("edrak_competitor_outputs")


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


class BatchVerification(BaseModel):
    verified_findings: List[LLMFinding]


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


# ---- Completeness ------------------------------------------
class RequirementCheck(BaseModel):
    requirement_id: str
    status: Literal["fulfilled", "missing"]
    evidence_found: str = ""
    missing_reason: str = Field(
        default="",
        description=(
            "REQUIRED when status is 'missing': exactly which sub-facts are "
            "not yet established by the verified findings."
        ),
    )


class RequirementCheckList(BaseModel):
    checks: List[RequirementCheck]


# ============================================================
# STATE
# ============================================================

class CompetitorState(TypedDict, total=False):
    company: str
    company_context: Dict[str, Any]
    competitors: List[str]
    research_goal: str

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

    final_report: Dict[str, Any]


# ============================================================
# NODE 9: COMPARE TARGET COMPANY WITH COMPETITORS
# ============================================================

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

COMPARISON_DIMENSIONS = [
    "AI capabilities and software-development lifecycle coverage",
    "Degree of autonomy and human approval points",
    "Integration with existing development workflows",
    "Availability (plans, deployment models, GA vs preview vs announced)",
    "Pricing and packaging",
    "Recent evolution (2026 changes)",
]
