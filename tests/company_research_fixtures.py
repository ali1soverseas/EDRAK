"""Setup shared by the company research tests: a workspace to attach facts and runs to."""

from __future__ import annotations

import uuid

from edrak.db.repositories import auth as auth_repo


async def new_workspace(db, name: str = "Acme") -> str:
    _, workspace = await auth_repo.create_account(
        db, email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="Layla",
        job_title=None, workspace_name=name,
    )
    return workspace["id"]
