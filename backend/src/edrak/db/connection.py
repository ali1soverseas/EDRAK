"""Async SQLite database connection and initialization."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from edrak.core.config import settings
from edrak.db.models import INIT_SCHEMA_SQL


def get_db_path() -> Path:
    """Return path to SQLite database file."""
    data_dir = settings.BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "edrak.db"


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Provide an asynchronous connection to the SQLite database."""
    db_path = get_db_path()
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    try:
        yield db
    finally:
        await db.close()


def hash_password(password: str) -> str:
    """Hash password using sha256."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


async def init_db() -> None:
    """Initialize SQLite database tables and seed defaults if empty."""
    async with get_db() as db:
        await db.executescript(INIT_SCHEMA_SQL)
        await db.commit()

        # Check if default workspace exists
        cursor = await db.execute("SELECT workspace_id FROM workspaces LIMIT 1;")
        row = await cursor.fetchone()
        if not row:
            default_ws_id = "ws-default"
            default_user_id = "user-default"
            demo_email = "demo@edrak.ai"
            demo_password_hash = hash_password("edrak-demo")

            await db.execute(
                "INSERT INTO workspaces (workspace_id, name) VALUES (?, ?);",
                (default_ws_id, "Default Workspace"),
            )
            await db.execute(
                """
                INSERT INTO users (user_id, email, password_hash, name, title, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    default_user_id,
                    demo_email,
                    demo_password_hash,
                    "Strategy Lead",
                    "Head of Strategy",
                    default_ws_id,
                ),
            )
            # Also seed Layla demo user if desired
            layla_user_id = "user-layla"
            layla_email = "layla@nileledger.example"
            await db.execute(
                """
                INSERT INTO users (user_id, email, password_hash, name, title, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    layla_user_id,
                    layla_email,
                    hash_password("edrak-demo"),
                    "Layla",
                    "Strategy Director",
                    default_ws_id,
                ),
            )

            # Check if active or gitlab profile exists to seed company profile
            profile_path = settings.BASE_DIR / "data" / "profiles" / "gitlab_profile.json"
            initial_fields = {
                "name": "GitLab",
                "aliases": ["GitLab Inc.", "GitLab.com"],
                "industry": "saas",
                "description": "GitLab is an enterprise AI-powered DevSecOps platform.",
                "offerings": ["GitLab Duo", "GitLab CI/CD", "GitLab Ultimate"],
                "markets": ["Global Enterprise DevSecOps"],
                "strategic_goals": "Expand AI-assisted DevSecOps market share with GitLab Duo.",
                "website": "https://about.gitlab.com",
                "socials": [],
            }
            if profile_path.exists():
                try:
                    with open(profile_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            initial_fields["name"] = data.get("name", "GitLab")
                            initial_fields["aliases"] = data.get("aliases", [])
                            initial_fields["description"] = data.get("notes", initial_fields["description"])
                            initial_fields["offerings"] = data.get("products", initial_fields["offerings"])
                except Exception:
                    pass

            await db.execute(
                """
                INSERT INTO company_profiles (workspace_id, fields_json, completed)
                VALUES (?, ?, 1);
                """,
                (default_ws_id, json.dumps(initial_fields)),
            )

            await db.commit()
