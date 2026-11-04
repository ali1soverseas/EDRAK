"""EDRAK FastAPI application entrypoint."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from edrak.api.routes import router as api_router
from edrak.db.connection import get_db, init_db
from edrak.db.repositories.auth import purge_expired_sessions
from edrak.db.repositories.runs import fail_orphaned_runs
from edrak.db.util import fetch_all

logger = logging.getLogger(__name__)


def keep_console_from_failing_a_worker() -> None:
    """Stop an unencodable character in a print() from losing a whole worker.

    A Windows console defaults to a legacy codepage (cp1256 on this machine) that
    cannot encode characters models and web pages produce freely - U+2011
    NON-BREAKING HYPHEN, for one. The worker agents print scraped text and model
    output directly, so such a character raised UnicodeEncodeError, which dispatch
    caught and turned into a failed WorkerResult: a worker lost to a hyphen, in the
    competitor and market stages, while internal (which only logs) was untouched.

    Escaping the rare unencodable character costs nothing; losing a worker does.
    The encoding is left alone so the console still renders Arabic; only the error
    handler changes. Every CLI runner already does this - the app never did.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


keep_console_from_failing_a_worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the database, or migrate a v1 file to the current schema.
    await init_db()
    async with get_db() as db:
        # A run that was executing when the server last stopped can never finish. Say so.
        orphaned = await fail_orphaned_runs(db)
        if orphaned:
            logger.warning("Marked %d interrupted run(s) as failed: %s", len(orphaned), ", ".join(orphaned))
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

    scheduler = asyncio.create_task(run_forever())
    yield
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
