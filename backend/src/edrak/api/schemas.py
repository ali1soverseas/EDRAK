"""Pydantic schemas for EDRAK API endpoints."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from edrak.contracts import (
    BusinessRequest,
    ResearchPlan,
    UseCase,
    WorkerType,
)


# ----------------------------------------------------------------------
# Session & Auth
# ----------------------------------------------------------------------


class UserOut(BaseModel):
    user_id: str
    email: str
    name: str
    title: Optional[str] = None


class WorkspaceOut(BaseModel):
    workspace_id: str
    name: str


class SessionOut(BaseModel):
    user: UserOut
    workspace: WorkspaceOut


class SignInRequest(BaseModel):
    email: str
    password: str


class CreateAccountRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    title: Optional[str] = None
    workspace_name: Optional[str] = None


# ----------------------------------------------------------------------
# Company Setup
# ----------------------------------------------------------------------


class CompanyLink(BaseModel):
    link_id: str
    platform: str
    url: str


class DocumentSize(BaseModel):
    unit: str  # "pages" | "rows" | "kb"
    value: float


class CompanyDocumentOut(BaseModel):
    document_id: str
    filename: str
    size: Optional[DocumentSize] = None
    status: str  # "indexed" | "indexing" | "failed"


class CompanyFields(BaseModel):
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    industry: Optional[str] = None
    description: str = ""
    offerings: list[str] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    strategic_goals: str = ""
    website: str = ""
    socials: list[CompanyLink] = Field(default_factory=list)


class CompanySetupOut(CompanyFields):
    documents: list[CompanyDocumentOut] = Field(default_factory=list)
    completed: bool = False


# ----------------------------------------------------------------------
# Analyses & Planning
# ----------------------------------------------------------------------


class AnalysisForm(BaseModel):
    use_case: UseCase = UseCase.COMPETITIVE_INTELLIGENCE
    goal: str = ""
    competitors: list[str] = Field(default_factory=list)
    market: str = ""
    offering: str = ""
    target_customers: Optional[str] = None
    focus: list[str] = Field(default_factory=list)
    time_window_days: Optional[int] = None
    sources: dict[str, bool] = Field(default_factory=dict)
    repeat_weekly: bool = False


class DraftPlanRequest(BaseModel):
    form: AnalysisForm
    analysis_id: Optional[str] = None


class RejectPlanRequest(BaseModel):
    reason: str = ""


class AnalysisSummaryOut(BaseModel):
    analysis_id: str
    title: str
    use_case: UseCase
    status: str
    updated_at: str
    repeat_weekly: bool
    tasks_total: int
    tasks_done: int
    tasks_failed: int
    brief_id: Optional[str] = None
    brief_no: Optional[int] = None
    verified: bool = False


class AnalysisDetailOut(BaseModel):
    analysis_id: str
    title: str
    status: str
    request: BusinessRequest
    plan: Optional[ResearchPlan] = None
    form: AnalysisForm
    rejection_reason: Optional[str] = None
    allowed_sources: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Live Run
# ----------------------------------------------------------------------


class RunTaskView(BaseModel):
    task_id: str
    worker: WorkerType
    state: str  # "queued" | "running" | "done" | "failed"
    progress: int  # 0 to 100
    sources: int = 0
    activity: str = ""
    log: list[dict[str, str]] = Field(default_factory=list)
    error: Optional[str] = None


class RunEventCallout(BaseModel):
    text: str
    highlight: Optional[str] = None


class RunEvent(BaseModel):
    event_id: str
    at: str
    source: str  # "supervisor" | WorkerType
    kind: str  # "info" | "gap" | "replan" | "failure" | "decision"
    message: str
    callout: Optional[RunEventCallout] = None


class RunViewOut(BaseModel):
    analysis_id: str
    title: str
    use_case: UseCase
    state: str  # "running" | "completed" | "partial" | "failed" | "cancelled"
    stage: str  # "plan" | "dispatch" | "workers" | "verify" | "synthesize" | "brief"
    approved_at: str
    approved_by: str
    started_at: str
    finished_at: Optional[str] = None
    tasks: list[RunTaskView] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)
    verification: str = "waiting"  # "waiting" | "running" | "done"
    brief_id: Optional[str] = None
