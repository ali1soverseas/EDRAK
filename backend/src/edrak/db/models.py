"""Database models and SQL schemas for EDRAK."""

from __future__ import annotations

INIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    title         TEXT,
    workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token        TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_profiles (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(workspace_id),
    fields_json  TEXT NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS company_documents (
    document_id  TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
    filename     TEXT NOT NULL,
    size_json    TEXT,
    status       TEXT NOT NULL DEFAULT 'indexing',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id      TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL REFERENCES workspaces(workspace_id),
    title            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'draft',
    form_json        TEXT NOT NULL,
    request_json     TEXT NOT NULL,
    plan_json        TEXT,
    rejection_reason TEXT,
    brief_no         INTEGER,
    updated_at       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    analysis_id  TEXT PRIMARY KEY REFERENCES analyses(analysis_id),
    approved_at  TEXT NOT NULL,
    approved_by  TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    state        TEXT NOT NULL DEFAULT 'running',
    stage        TEXT NOT NULL DEFAULT 'plan',
    result_json  TEXT,
    cancelled_at TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
    brief_id     TEXT PRIMARY KEY,
    analysis_id  TEXT NOT NULL REFERENCES analyses(analysis_id),
    brief_no     INTEGER NOT NULL,
    brief_json   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""
