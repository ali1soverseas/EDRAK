"""EDRAK FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from edrak.agents.market_intelligence import run
from edrak.api.routes import router as api_router
from edrak.contracts import ResearchTask, WorkerResult
from edrak.db.connection import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema and seed defaults
    await init_db()
    yield


app = FastAPI(
    title="EDRAK",
    description="Agentic Business Decision Intelligence Platform API",
    lifespan=lifespan,
)

# CORS middleware for local frontend development
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

# Include main API router
app.include_router(api_router)


# Existing agent test endpoint
@app.post("/agents/market", response_model=WorkerResult)
def run_market_intelligence(task: ResearchTask) -> WorkerResult:
    return run(task)
