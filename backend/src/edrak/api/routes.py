"""All /api routes."""

from __future__ import annotations

from fastapi import APIRouter

from edrak.api.routers import analyses, auth, briefs, chat, company, documents, entities, monitors, runs, workspace

router = APIRouter(prefix="/api")
for module in (auth, company, documents, entities, analyses, runs, briefs, monitors, workspace, chat):
    router.include_router(module.router)
