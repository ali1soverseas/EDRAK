"""Action plan document. Lenient on input (a person edits it), strict on structure."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["low", "moderate", "high"]


class PlanAction(BaseModel):
    id: str
    title: str
    description: str = ""
    owner_role: str = ""
    start_week: int = Field(default=1, ge=1)
    duration_weeks: int = Field(default=1, ge=1)
    depends_on: list[str] = Field(default_factory=list)
    deliverable: str = ""
    success_measure: str = ""


class Workstream(BaseModel):
    id: str
    name: str
    objective: str = ""
    actions: list[PlanAction] = Field(default_factory=list)


class Milestone(BaseModel):
    id: str
    week: int = Field(default=1, ge=1)
    title: str
    go_criteria: str = ""
    kill_criteria: str = ""


class PlanKpi(BaseModel):
    id: str
    name: str
    metric_id: Optional[str] = None
    entity: str = ""
    new_measure: bool = False
    baseline_uid: Optional[str] = None
    baseline: str = Field(default="", description="Filled by code from the stored metric; read-only for the person.")
    direction: Literal["increase", "decrease", "maintain"] = "increase"
    target: str = ""
    target_status: Literal["proposed", "accepted", "edited"] = "proposed"
    cadence: str = ""


class PlanRisk(BaseModel):
    id: str
    text: str
    likelihood: Level = "moderate"
    impact: Level = "moderate"
    early_warning: str = ""
    mitigation: str = ""
    mitigation_action_ids: list[str] = Field(default_factory=list)
    owner_role: str = ""
    evidence_refs: list[int] = Field(default_factory=list)


class PlanOpportunity(BaseModel):
    id: str
    text: str
    how_to_capture: str = ""
    action_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[int] = Field(default_factory=list)


class Assumption(BaseModel):
    id: str
    text: str
    metric_uid: Optional[str] = None
    gap: str = ""


class OpenQuestion(BaseModel):
    id: str
    text: str
    close_by_week: Optional[int] = None


class ChosenOption(BaseModel):
    key: str
    title: str
    why: str = ""
    signal_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[int] = Field(default_factory=list)


class NotChosen(BaseModel):
    key: str
    title: str
    why_not: str = ""


class ActionPlan(BaseModel):
    objective: str
    chosen_option: ChosenOption
    alternatives_not_chosen: list[NotChosen] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    workstreams: list[Workstream] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    kpis: list[PlanKpi] = Field(default_factory=list)
    risks: list[PlanRisk] = Field(default_factory=list)
    opportunities: list[PlanOpportunity] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    review_date: Optional[str] = None
    review_cadence: str = ""
    total_weeks: int = 0
    notes: list[str] = Field(default_factory=list)
