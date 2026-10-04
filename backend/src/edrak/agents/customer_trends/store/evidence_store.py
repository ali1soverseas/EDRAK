"""SQLite evidence store: the worker's only database (SPEC section 3).

Tools write full records here and hand the model pointers. One connection is shared behind
a lock, in WAL mode, so threads (the UI runs the graph on a worker thread) and other
processes can use the same file.
"""

import json
import random
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from edrak.agents.customer_trends.schemas.analysis import MetricResult, ThemeAggregate
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.findings import Finding
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.utils.text import normalize_text

if TYPE_CHECKING:
    from edrak.agents.customer_trends.settings import Settings

Sample = Literal["top", "recent", "random"]

COUNT_FIELDS = frozenset(
    {"platform", "source_type", "language", "provider", "batch_id", "snippet_only"}
)
UNKNOWN = "unknown"

_DB_FILE = "evidence.db"
_DT_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BUSY_TIMEOUT_MS = 5000


class UnknownRunError(KeyError):
    """A batch or record was written for a run that was never created."""


@dataclass(frozen=True)
class QueryResult:
    items: list[EvidenceItem]
    total: int


class RunSummary(BaseModel):
    run_id: str
    task_id: str
    created_at: datetime
    evidence_count: int = 0
    batch_ids: list[str] = Field(default_factory=list)
    by_platform: dict[str, int] = Field(default_factory=dict)
    by_source_type: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    findings_count: int = 0
    result_location: str | None = None


def _dt_to_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime(_DT_FORMAT)


def _day_start(day: date) -> str:
    return datetime.combine(day, time.min, tzinfo=UTC).strftime(_DT_FORMAT)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _norm_for_search(value: str) -> str:
    return normalize_text(value).casefold()


def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            brief_json TEXT
        );
        CREATE TABLE batches (
            batch_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            inserted INTEGER NOT NULL,
            duplicates INTEGER NOT NULL,
            meta_json TEXT NOT NULL
        );
        CREATE INDEX batches_run ON batches(run_id);
        CREATE TABLE evidence (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            batch_id TEXT NOT NULL REFERENCES batches(batch_id),
            source_type TEXT NOT NULL,
            platform TEXT,
            url TEXT,
            author TEXT,
            text TEXT NOT NULL,
            language TEXT,
            published_at TEXT,
            collected_at TEXT NOT NULL,
            engagement_json TEXT NOT NULL,
            engagement_total INTEGER NOT NULL,
            snippet_only INTEGER NOT NULL,
            provider TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY (run_id, id)
        );
        CREATE UNIQUE INDEX evidence_dedupe
            ON evidence(run_id, IFNULL(platform, ''), content_hash);
        CREATE INDEX evidence_batch ON evidence(run_id, batch_id);
        CREATE TABLE aggregates (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            theme_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, theme_key)
        );
        CREATE TABLE trend_series (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            batch_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, batch_id, keyword)
        );
        CREATE TABLE metrics (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            metric_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, metric_id)
        );
        CREATE TABLE findings (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE (run_id, id)
        );
        CREATE TABLE results (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
            location TEXT NOT NULL,
            registered_at TEXT NOT NULL
        );
        """
    )


_MIGRATIONS = (_migration_1,)


class EvidenceStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._conn.create_function("norm_text", 1, _norm_for_search, deterministic=True)
        self._migrate()

    @classmethod
    def from_settings(cls, settings: "Settings | None" = None) -> "EvidenceStore":
        from edrak.agents.customer_trends.settings import get_settings

        return cls((settings or get_settings()).data_dir / _DB_FILE)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version, migrate in enumerate(_MIGRATIONS, start=1):
                if version > current:
                    migrate(self._conn)
                    self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            return int(row["v"] or 0)

    def _require_run(self, conn: sqlite3.Connection, run_id: str) -> None:
        if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
            raise UnknownRunError(run_id)

    # runs and batches

    def create_run(self, run_id: str, task_id: str, brief: TaskBrief | None = None) -> bool:
        """Register a run. Returns False, changing nothing, if it already exists."""
        with self._tx() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO runs(run_id, task_id, created_at, brief_json)"
                " VALUES (?,?,?,?)",
                (
                    run_id,
                    task_id,
                    _dt_to_text(datetime.now(UTC)),
                    brief.model_dump_json() if brief else None,
                ),
            )
            return cursor.rowcount == 1

    def add_batch(
        self,
        run_id: str,
        task_id: str,
        kind: str,
        items: Sequence[EvidenceItem],
        meta: dict[str, Any] | None = None,
    ) -> tuple[str, int, int]:
        """Store a batch and return `(batch_id, inserted, duplicates)`.

        The batch stamps its own run_id, task_id and batch_id on every item. An item whose
        `(platform, content_hash)` is already stored for the run is counted as a duplicate
        and skipped. A batch may be empty: it can still carry metadata and trend series.
        """
        batch_id = f"b_{uuid.uuid4().hex[:12]}"
        inserted = 0
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.execute(
                "INSERT INTO batches(batch_id, run_id, task_id, kind, created_at, inserted,"
                " duplicates, meta_json) VALUES (?,?,?,?,?,0,0,?)",
                (
                    batch_id,
                    run_id,
                    task_id,
                    kind,
                    _dt_to_text(datetime.now(UTC)),
                    _json(meta or {}),
                ),
            )
            for item in items:
                stamped = item.model_copy(
                    update={"run_id": run_id, "task_id": task_id, "batch_id": batch_id}
                )
                inserted += self._insert_item(conn, stamped)
            duplicates = len(items) - inserted
            conn.execute(
                "UPDATE batches SET inserted = ?, duplicates = ? WHERE batch_id = ?",
                (inserted, duplicates, batch_id),
            )
        return batch_id, inserted, duplicates

    @staticmethod
    def _insert_item(conn: sqlite3.Connection, item: EvidenceItem) -> int:
        dumped = item.model_dump(mode="json")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO evidence(run_id, id, task_id, batch_id, source_type, platform,"
            " url, author, text, language, published_at, collected_at, engagement_json,"
            " engagement_total, snippet_only, provider, content_hash, metadata_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.run_id,
                item.id,
                item.task_id,
                item.batch_id,
                item.source_type.value,
                item.platform.value if item.platform else None,
                item.url,
                item.author,
                item.text,
                item.language,
                _dt_to_text(item.published_at),
                _dt_to_text(item.collected_at),
                _json(dumped["engagement"]),
                item.engagement_total,
                int(item.snippet_only),
                item.provider,
                item.content_hash,
                _json(dumped["metadata"]),
            ),
        )
        return cursor.rowcount

    def batch_meta(self, run_id: str, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT meta_json FROM batches WHERE run_id = ? AND batch_id = ?",
                (run_id, batch_id),
            ).fetchone()
        meta: dict[str, Any] | None = None if row is None else json.loads(row["meta_json"])
        return meta

    # evidence reads

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> EvidenceItem:
        return EvidenceItem(
            id=row["id"],
            run_id=row["run_id"],
            task_id=row["task_id"],
            batch_id=row["batch_id"],
            source_type=row["source_type"],
            platform=row["platform"],
            url=row["url"],
            author=row["author"],
            text=row["text"],
            language=row["language"],
            published_at=row["published_at"],
            collected_at=row["collected_at"],
            engagement=json.loads(row["engagement_json"]),
            snippet_only=bool(row["snippet_only"]),
            provider=row["provider"],
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata_json"]),
        )

    def get_items(self, run_id: str, ids: Sequence[str]) -> list[EvidenceItem]:
        """Items for `ids` in the order given. Unknown ids are skipped."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evidence"
                " WHERE run_id = ? AND id IN (SELECT value FROM json_each(?))",
                (run_id, json.dumps(list(ids))),
            ).fetchall()
        by_id = {row["id"]: self._row_to_item(row) for row in rows}
        return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]

    def existing_ids(self, run_id: str, ids: Sequence[str]) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM evidence"
                " WHERE run_id = ? AND id IN (SELECT value FROM json_each(?))",
                (run_id, json.dumps(list(ids))),
            ).fetchall()
        return {row["id"] for row in rows}

    def _where(self, run_id: str, filters: EvidenceFilters) -> tuple[str, list[Any]]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        for column in ("platform", "source_type", "language"):
            value = getattr(filters, column)
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(getattr(value, "value", value))
        if filters.text_contains:
            clauses.append("INSTR(norm_text(text), ?) > 0")
            params.append(_norm_for_search(filters.text_contains))
        if filters.min_engagement is not None:
            clauses.append("engagement_total >= ?")
            params.append(filters.min_engagement)
        if filters.since is not None:
            clauses.append("published_at >= ?")
            params.append(_day_start(filters.since))
        if filters.until is not None:
            clauses.append("published_at < ?")
            params.append(_day_start(filters.until + timedelta(days=1)))
        if filters.batch_ids is not None:
            clauses.append("batch_id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(filters.batch_ids))
        if filters.theme is not None:
            clauses.append("id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(self._theme_evidence_ids(run_id, filters.theme)))
        return " AND ".join(clauses), params

    def _theme_evidence_ids(self, run_id: str, theme: str) -> list[str]:
        row = self._conn.execute(
            "SELECT payload_json FROM aggregates WHERE run_id = ? AND theme_key = ?",
            (run_id, _norm_for_search(theme)),
        ).fetchone()
        if row is None:
            return []
        ids: list[str] = json.loads(row["payload_json"])["evidence_ids"]
        return ids

    def query(
        self,
        run_id: str,
        filters: EvidenceFilters | None = None,
        *,
        limit: int = 10,
        sample: Sample = "top",
        seed: int | None = None,
    ) -> QueryResult:
        """Matching items (at most `limit`) and the total number of matches.

        `top` orders by interaction count, `recent` by publication date (undated last),
        `random` draws a sample, reproducible with `seed`.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._lock:
            where, params = self._where(run_id, filters or EvidenceFilters())
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM evidence WHERE {where}",  # noqa: S608  # fixed clauses, bound values
                params,
            ).fetchone()["n"]
            if sample == "random":
                ids = [
                    row["id"]
                    for row in self._conn.execute(
                        f"SELECT id FROM evidence WHERE {where} ORDER BY id",  # noqa: S608  # fixed clauses, bound values
                        params,
                    )
                ]
                rng = random.Random(seed)  # noqa: S311  # sampling for variety, not security
                chosen = rng.sample(ids, min(limit, len(ids)))
                return QueryResult(self.get_items(run_id, chosen), total)
            order = (
                "engagement_total DESC, published_at DESC, id"
                if sample == "top"
                else "published_at DESC, collected_at DESC, id"
            )
            rows = self._conn.execute(
                f"SELECT * FROM evidence WHERE {where} ORDER BY {order} LIMIT ?",  # noqa: S608  # fixed clauses
                [*params, limit],
            ).fetchall()
        return QueryResult([self._row_to_item(row) for row in rows], total)

    def count_by(
        self, run_id: str, field: str, batch_ids: Sequence[str] | None = None
    ) -> dict[str, int]:
        """Item counts grouped by one column. Missing values are counted as `unknown`."""
        if field not in COUNT_FIELDS:
            raise ValueError(f"cannot count by {field!r}; choose one of {sorted(COUNT_FIELDS)}")
        sql = f"SELECT {field} AS key, COUNT(*) AS n FROM evidence WHERE run_id = ?"  # noqa: S608  # field is whitelisted
        params: list[Any] = [run_id]
        if batch_ids is not None:
            sql += " AND batch_id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(list(batch_ids)))
        with self._lock:
            rows = self._conn.execute(sql + f" GROUP BY {field} ORDER BY n DESC, key", params)
            return {str(UNKNOWN if row["key"] is None else row["key"]): row["n"] for row in rows}

    # analysis products

    def save_aggregates(self, run_id: str, aggregates: Sequence[ThemeAggregate]) -> None:
        """Replace the run's theme aggregates."""
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.execute("DELETE FROM aggregates WHERE run_id = ?", (run_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO aggregates(run_id, theme_key, payload_json) VALUES (?,?,?)",
                [
                    (run_id, _norm_for_search(a.theme_label), a.model_dump_json())
                    for a in aggregates
                ],
            )

    def get_aggregates(self, run_id: str) -> list[ThemeAggregate]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM aggregates WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        aggregates = [ThemeAggregate.model_validate_json(r["payload_json"]) for r in rows]
        return sorted(aggregates, key=lambda a: (-a.count, a.theme_label))

    def save_trend_series(self, run_id: str, series: Sequence[TrendSeries]) -> None:
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.executemany(
                "INSERT OR REPLACE INTO trend_series(run_id, batch_id, keyword, payload_json)"
                " VALUES (?,?,?,?)",
                [(run_id, s.batch_id, s.keyword, s.model_dump_json()) for s in series],
            )

    def get_trend_series(
        self, run_id: str, batch_ids: Sequence[str] | None = None
    ) -> list[TrendSeries]:
        sql = "SELECT payload_json FROM trend_series WHERE run_id = ?"
        params: list[Any] = [run_id]
        if batch_ids is not None:
            sql += " AND batch_id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(list(batch_ids)))
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY rowid", params).fetchall()
        return [TrendSeries.model_validate_json(r["payload_json"]) for r in rows]

    def save_metric(self, run_id: str, record: MetricResult) -> None:
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.execute(
                "INSERT OR REPLACE INTO metrics(run_id, metric_id, payload_json) VALUES (?,?,?)",
                (run_id, record.metric_id, record.model_dump_json()),
            )

    def get_metric(self, run_id: str, metric_id: str) -> MetricResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM metrics WHERE run_id = ? AND metric_id = ?",
                (run_id, metric_id),
            ).fetchone()
        return None if row is None else MetricResult.model_validate_json(row["payload_json"])

    def save_findings(self, run_id: str, findings: Sequence[Finding]) -> None:
        """Add findings; a finding with a stored id replaces it and keeps its position."""
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.executemany(
                "INSERT INTO findings(run_id, id, payload_json) VALUES (?,?,?)"
                " ON CONFLICT(run_id, id) DO UPDATE SET payload_json = excluded.payload_json",
                [(run_id, f.id, f.model_dump_json()) for f in findings],
            )

    def get_findings(self, run_id: str) -> list[Finding]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM findings WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [Finding.model_validate_json(r["payload_json"]) for r in rows]

    # results and summaries

    def register_result(self, run_id: str, location: str) -> None:
        with self._tx() as conn:
            self._require_run(conn, run_id)
            conn.execute(
                "INSERT OR REPLACE INTO results(run_id, location, registered_at) VALUES (?,?,?)",
                (run_id, location, _dt_to_text(datetime.now(UTC))),
            )

    def result_location(self, run_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT location FROM results WHERE run_id = ?", (run_id,)
            ).fetchone()
        return None if row is None else str(row["location"])

    def run_summary(self, run_id: str) -> RunSummary:
        with self._lock:
            run = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise UnknownRunError(run_id)
            batch_ids = [
                r["batch_id"]
                for r in self._conn.execute(
                    "SELECT batch_id FROM batches WHERE run_id = ? ORDER BY created_at, rowid",
                    (run_id,),
                )
            ]
            findings = self._conn.execute(
                "SELECT COUNT(*) AS n FROM findings WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            by_platform = self.count_by(run_id, "platform")
            by_source_type = self.count_by(run_id, "source_type")
            by_language = self.count_by(run_id, "language")
            location = self.result_location(run_id)
        return RunSummary(
            run_id=run_id,
            task_id=run["task_id"],
            created_at=datetime.strptime(run["created_at"], _DT_FORMAT).replace(tzinfo=UTC),
            evidence_count=sum(by_source_type.values()),
            batch_ids=batch_ids,
            by_platform=by_platform,
            by_source_type=by_source_type,
            by_language=by_language,
            findings_count=findings,
            result_location=location,
        )

    def list_runs(self) -> list[RunSummary]:
        """All runs, newest first."""
        with self._lock:
            run_ids = [
                r["run_id"]
                for r in self._conn.execute(
                    "SELECT run_id FROM runs ORDER BY created_at DESC, rowid DESC"
                )
            ]
            return [self.run_summary(run_id) for run_id in run_ids]
