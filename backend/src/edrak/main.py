from fastapi import FastAPI

from edrak.agents.market_intelligence import run
from edrak.contracts import ResearchTask, WorkerResult

app = FastAPI(title="EDRAK")


@app.post("/agents/market", response_model=WorkerResult)
def run_market_intelligence(task: ResearchTask) -> WorkerResult:
    return run(task)
