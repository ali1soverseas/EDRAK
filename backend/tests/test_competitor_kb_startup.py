import asyncio
from contextlib import asynccontextmanager

from edrak import main
from edrak.db import competitor_kb as competitor_kb_repo
from edrak.knowledge import competitor_kb
from edrak.monitoring import scheduler


def test_startup_requeues_interrupted_competitor_builds(monkeypatch):
    queued = []

    @asynccontextmanager
    async def fake_get_db():
        yield object()

    async def no_op(*_args, **_kwargs):
        return None

    async def no_rows(*_args, **_kwargs):
        return []

    async def interrupted_jobs(_db):
        return [
            {"workspace_id": "workspace-a", "entity_id": "intel"},
            {"workspace_id": "workspace-a", "entity_id": "google"},
            {"workspace_id": "workspace-b", "entity_id": "broadcom"},
        ]

    async def queue_builds(_db, workspace_id, entity_ids):
        queued.append((workspace_id, entity_ids))

    async def run_forever():
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "init_db", no_op)
    monkeypatch.setattr(main, "get_db", fake_get_db)
    monkeypatch.setattr(main, "fetch_all", no_rows)
    monkeypatch.setattr(main, "fail_orphaned_runs", no_rows)
    monkeypatch.setattr(main, "purge_expired_sessions", no_op)
    monkeypatch.setattr(competitor_kb_repo, "interrupted_jobs", interrupted_jobs)
    monkeypatch.setattr(competitor_kb, "queue_builds", queue_builds)
    monkeypatch.setattr(scheduler, "run_forever", run_forever)

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert queued == [
                ("workspace-a", ["intel", "google"]),
                ("workspace-b", ["broadcom"]),
            ]

    asyncio.run(exercise_lifespan())
