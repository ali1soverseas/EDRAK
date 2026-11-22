"""EDRAK FastAPI application entrypoint."""

import logging
import sys
from contextlib import asynccontextmanager

# Windows consoles default to a legacy code page. A model or a source can emit
# U+2011 (non-breaking hyphen), and printing it would kill the worker.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is None:
        continue
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from edrak.api.routes import router as api_router
from edrak.db.connection import get_db, init_db
from edrak.db.repositories.auth import purge_expired_sessions
from edrak.db.repositories.research_runs import fail_orphaned_research_runs
from edrak.db.repositories.runs import fail_orphaned_runs
from edrak.db.util import fetch_all

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the database, or migrate a v1 file to the current schema.
    await init_db()
    async with get_db() as db:
        # A run that was executing when the server last stopped can never finish. Say so.
        orphaned = await fail_orphaned_runs(db)
        if orphaned:
            logger.warning("Marked %d interrupted run(s) as failed: %s", len(orphaned), ", ".join(orphaned))
        interrupted = await fail_orphaned_research_runs(db)
        if interrupted:
            logger.warning("Marked %d interrupted research run(s) as failed: %s", len(interrupted), ", ".join(interrupted))
        await purge_expired_sessions(db)
        # Files that were waiting for or in the middle of indexing are queued again.
        pending = await fetch_all(db, "SELECT id, workspace_id FROM documents WHERE status IN ('uploaded','indexing')")
    if pending:
        from edrak.knowledge.pipeline import schedule_indexing

        for doc in pending:
            schedule_indexing(doc["workspace_id"], doc["id"])
        logger.info("Re-queued %d document(s) for indexing", len(pending))
    # Monitors run on their schedule while the app is up.
    import asyncio

    from edrak.monitoring.scheduler import run_forever

    # Say once whether scanned PDFs can be read. The check never stops the server from starting.
    try:
        from edrak.company_profile.pdf_import import log_ocr_status

        log_ocr_status()
    except Exception:
        logger.exception("Could not check OCR availability")

    scheduler = asyncio.create_task(run_forever())
    from edrak.i18n.localize import schedule_brief_backfill

    backfill = schedule_brief_backfill()
    yield
    backfill.cancel()
    scheduler.cancel()


app = FastAPI(
    title="EDRAK",
    description="Agentic Business Decision Intelligence Platform API",
    lifespan=lifespan,
)

# CORS for local frontend development. In production the frontend is served from the same origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:[0-9]+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = True,
) -> None:
    """Start the API with reload settings that cannot hang the site.

    Reload watches only ``backend/src``, so installs into ``.venv`` and writes
    under ``artifacts`` do not restart the process. A five-second graceful
    shutdown timeout stops a mid-run reload from waiting forever on the open
    SSE connection that streams a live analysis.
    """
    import uvicorn
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[1]
    uvicorn.run(
        "edrak.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(src_dir)] if reload else None,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    run()
