"""Pydantic shapes for market-agent planning and analysis replies."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class PlannedTask(BaseModel):
    description: str = Field(min_length=1)
    tool_hint: str = Field(min_length=1)


class TaskPlan(BaseModel):
    tasks: list[PlannedTask] = Field(min_length=1)


class Usefulness(BaseModel):
    useful: bool
    reason: str = ""


class AnalysisClaim(BaseModel):
    claim: str = Field(min_length=1)


def schema_text(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2)
