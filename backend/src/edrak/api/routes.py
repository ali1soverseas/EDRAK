"""All /api routes."""

from __future__ import annotations

from fastapi import APIRouter

from edrak.api.routers import analyses, auth, briefs, company, documents, runs

router = APIRouter(prefix="/api")
for module in (auth, company, documents, analyses, runs, briefs):
    router.include_router(module.router)
