"""Neo4j connection for the EDRAK knowledge graph.

The graph is an optional second view of EDRAK's contracts: SQLite stays the store
of record. So every caller must survive Neo4j being off or unreachable:

* ``GRAPH_ENABLED=false`` (the default) makes the graph a no-op everywhere.
* An unreachable server raises ``GraphUnavailable`` once, then the connection
  refuses to retry for ``NEO4J_RETRY_AFTER_S`` seconds, so a pipeline run never
  waits on a dead database once per task.

Usage::

    from edrak.graph.connection import get_connection

    conn = get_connection()
    if conn.available():
        rows = conn.read("MATCH (c:Company) RETURN c.name AS name LIMIT $n", n=5)
"""

from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.cypher")


class GraphSettings(BaseSettings):
    """Neo4j settings, read from the environment or the same .env as edrak.core.config."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[4] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GRAPH_ENABLED: bool = False
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: SecretStr = SecretStr("neo4j")
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_CONNECT_TIMEOUT_S: float = 5.0
    NEO4J_RETRY_AFTER_S: float = 60.0
    GRAPH_BATCH_SIZE: int = 500
    # Append a short "known from earlier runs" note to ResearchTask.focus (agent_context.GraphAwareWorker).
    GRAPH_FOCUS_HINTS: bool = True


@lru_cache(maxsize=1)
def get_graph_settings() -> GraphSettings:
    return GraphSettings()


class GraphUnavailable(RuntimeError):
    """The graph is disabled or Neo4j cannot be reached right now."""


def split_statements(text: str) -> list[str]:
    """Split a .cypher file into statements: drops // comments, splits on ';'.

    Only meant for schema files: it does not understand ';' or '//' inside string literals.
    """
    lines = []
    for line in text.splitlines():
        cut = line.find("//")
        lines.append(line if cut < 0 else line[:cut])
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


class GraphConnection:
    """One Neo4j driver per process, created on first use."""

    def __init__(self, settings: GraphSettings | None = None, driver: Any = None) -> None:
        self.settings = settings or get_graph_settings()
        self._driver = driver  # tests may inject a fake driver
        self._lock = threading.Lock()
        self._failed_at: float | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def enabled(self) -> bool:
        return bool(self.settings.GRAPH_ENABLED) or self._driver is not None

    @property
    def database(self) -> str:
        return self.settings.NEO4J_DATABASE

    def driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        if not self.settings.GRAPH_ENABLED:
            raise GraphUnavailable("GRAPH_ENABLED is false")
        if self._failed_at is not None and time.monotonic() - self._failed_at < self.settings.NEO4J_RETRY_AFTER_S:
            raise GraphUnavailable("Neo4j was unreachable a moment ago; not retrying yet")

        with self._lock:
            if self._driver is None:
                try:
                    from neo4j import GraphDatabase
                except ImportError as exc:
                    raise GraphUnavailable("Neo4j driver is not installed; install the project requirements") from exc

                s = self.settings
                drv = GraphDatabase.driver(
                    s.NEO4J_URI,
                    auth=(s.NEO4J_USER, s.NEO4J_PASSWORD.get_secret_value()),
                    connection_timeout=s.NEO4J_CONNECT_TIMEOUT_S,
                    max_connection_lifetime=3600,
                )
                try:
                    drv.verify_connectivity()
                except Exception as exc:  # noqa: BLE001 - auth, DNS, refused: all mean "not now"
                    drv.close()
                    self._failed_at = time.monotonic()
                    raise GraphUnavailable(f"cannot reach Neo4j at {s.NEO4J_URI}: {exc}") from exc
                self._driver = drv
                self._failed_at = None
                logger.info("Connected to Neo4j at %s (database %s)", s.NEO4J_URI, s.NEO4J_DATABASE)
        return self._driver

    def available(self) -> bool:
        try:
            self.driver()
            return True
        except GraphUnavailable as exc:
            if self.settings.GRAPH_ENABLED:
                logger.warning("Graph unavailable: %s", exc)
            else:
                logger.debug("Graph unavailable: %s", exc)
            return False

    def close(self) -> None:
        with self._lock:
            if self._driver is not None:
                self._driver.close()
                self._driver = None

    def __enter__(self) -> GraphConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ queries

    def read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        from neo4j import RoutingControl

        records, _, _ = self.driver().execute_query(
            query, parameters_=params, routing_=RoutingControl.READ, database_=self.database
        )
        return [record.data() for record in records]

    def write(self, query: str, **params: Any) -> Any:
        """Run one write query in its own transaction. Returns the summary counters."""
        from neo4j import RoutingControl

        result = self.driver().execute_query(
            query, parameters_=params, routing_=RoutingControl.WRITE, database_=self.database
        )
        return result.summary.counters

    def write_batches(self, query: str, rows: Sequence[dict[str, Any]], **params: Any) -> int:
        """Run an ``UNWIND $rows AS row ...`` query in batches. Returns the number of rows sent."""
        size = max(1, int(self.settings.GRAPH_BATCH_SIZE))
        rows = list(rows)
        for start in range(0, len(rows), size):
            self.write(query, rows=rows[start : start + size], **params)
        return len(rows)

    # ------------------------------------------------------------------ schema

    def apply_schema(self, path: Path | str = SCHEMA_PATH) -> int:
        """Create constraints, indexes and the Worker seed. Idempotent."""
        statements = split_statements(Path(path).read_text(encoding="utf-8"))
        for statement in statements:
            self.write(statement)
        logger.info("Applied %d schema statements from %s", len(statements), path)
        return len(statements)

    def wipe(self) -> None:
        """Delete every node and relationship. Development databases only."""
        # CALL ... IN TRANSACTIONS needs an auto-commit transaction, which execute_query is not.
        while True:
            rows = self.read_write("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS deleted")
            if not rows or rows[0]["deleted"] == 0:
                break

    def read_write(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """A write query whose RETURN rows the caller needs."""
        from neo4j import RoutingControl

        records, _, _ = self.driver().execute_query(
            query, parameters_=params, routing_=RoutingControl.WRITE, database_=self.database
        )
        return [record.data() for record in records]

    def counts(self) -> dict[str, int]:
        rows = self.read("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label")
        return {row["label"]: row["n"] for row in rows}


_default: GraphConnection | None = None
_default_lock = threading.Lock()


def get_connection() -> GraphConnection:
    """The process-wide connection. Cheap to call; connects lazily."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = GraphConnection()
    return _default
