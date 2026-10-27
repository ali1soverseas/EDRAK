import asyncio
from datetime import timedelta

import aiosqlite
from edrak.core.security import hash_token
from edrak.db.repositories.auth import resolve_session
from edrak.db.util import now_iso


def test_session_lookup_can_skip_activity_write_for_polling_requests(tmp_path):
    async def run_test():
        db = await aiosqlite.connect(str(tmp_path / "auth.db"))
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY, email TEXT, full_name TEXT, job_title TEXT
            );
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY, owner_id TEXT, name TEXT
            );
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT, expires_at TEXT, last_seen_at TEXT
            );
            """
        )
        token = "active-session-token"
        last_seen = now_iso(timedelta(minutes=-5))
        await db.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            ("user-1", "user@example.test", "Test User", None),
        )
        await db.execute(
            "INSERT INTO workspaces VALUES (?, ?, ?)",
            ("workspace-1", "user-1", "Test Workspace"),
        )
        await db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (hash_token(token), "user-1", now_iso(timedelta(days=1)), last_seen),
        )
        await db.commit()

        assert await resolve_session(db, token, touch=False)
        row = await (await db.execute("SELECT last_seen_at FROM sessions")).fetchone()
        assert row["last_seen_at"] == last_seen

        assert await resolve_session(db, token, touch=True)
        row = await (await db.execute("SELECT last_seen_at FROM sessions")).fetchone()
        assert row["last_seen_at"] != last_seen
        await db.close()

    asyncio.run(run_test())
