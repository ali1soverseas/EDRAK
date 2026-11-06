"""All /api routes."""

from __future__ import annotations

from fastapi import APIRouter

from edrak.api.routers import (
    action_plans,
    analyses,
    auth,
    briefs,
    cards,
    chat,
    company,
    competitor_kb,
    documents,
    entities,
    evaluation,
    monitors,
    runs,
    workspace,
)

router = APIRouter(prefix="/api")
for module in (
    auth,
    company,
    documents,
    entities,
    analyses,
    runs,
    briefs,
    cards,
    action_plans,
    monitors,
    workspace,
    chat,
    evaluation,
    competitor_kb,
):
    router.include_router(module.router)
