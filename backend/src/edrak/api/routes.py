"""All /api routes."""

from __future__ import annotations

from fastapi import APIRouter

from edrak.api.routers import action_plans, analyses, cards, auth, briefs, chat, company, documents, entities, evaluation, monitors, runs, workspace

router = APIRouter(prefix="/api")
for module in (auth, company, documents, entities, analyses, runs, briefs, cards, action_plans, monitors, workspace, chat, evaluation):
    router.include_router(module.router)
