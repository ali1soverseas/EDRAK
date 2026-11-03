"""All /api routes."""

from __future__ import annotations

from fastapi import APIRouter

from edrak.api.routers import action_plans, analyses, cards, auth, briefs, chat, company, company_research, documents, entities, monitors, profile_import, runs, workspace

router = APIRouter(prefix="/api")
for module in (auth, company, profile_import, company_research, documents, entities, analyses, runs, briefs, cards, action_plans, monitors, workspace, chat):
    router.include_router(module.router)
