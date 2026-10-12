"""Database package for EDRAK: the v0.2 SQLite schema and its repositories."""

from edrak.db.connection import get_db, init_db

__all__ = ["get_db", "init_db"]
