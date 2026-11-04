"""Aggregate and render the usage log.

Three views, kept separate on purpose:

* per workflow  - one run's calls, split by stage
* per stage     - tokens, cost, calls, latency and failure rate for an agent
* system        - totals, cost per report, latency and throughput across runs

Financial figures (cost) and operational ones (latency, throughput) are reported
side by side but never mixed: a workflow's latency is its real start-to-end span
from the runs table, not the sum of its calls, which would inflate it as soon as
workers run in parallel.
"""

from __future__ import annotations

import math
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import flush, read_records


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _numbers(records: Iterable[dict[str, Any]], key: str) -> list[float]:
    return [r[key] for r in records if isinstance(r.get(key), (int, float))]


def _total(records: Iterable[dict[str, Any]], key: str) -> int:
    return int(sum(v for v in _numbers(records, key)))


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[rank], 3)


def _cost(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    known = [
        r
        for r in records
        if r.get("pricing_known") and isinstance(r.get("estimated_cost_usd"), (int, float))
    ]
    # A failed call that reported no usage has no measurable cost; it must not
    # drag the whole total to "unknown". An unpriced call that did return tokens,
    # or simply succeeded, does make the total unknown.
    unpriced = [
        r
        for r in records
        if not r.get("pricing_known")
        and (r.get("status") == "success" or r.get("total_tokens") is not None)
    ]
    return {
        "cost_usd": round(sum(r["estimated_cost_usd"] for r in known), 8),
        "cost_known": not unpriced,
        "cost_unknown_calls": len(unpriced),
    }


def _tokens(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "input_tokens": _total(records, "input_tokens"),
        "output_tokens": _total(records, "output_tokens"),
        "total_tokens": _total(records, "total_tokens"),
    }


def _throughput(records: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    """Output tokens per second across the calls, and how it was measured.

    Preferred basis is the provider's own generation time, which excludes prompt
    processing and network time. Ollama Cloud reports ``total_duration`` but
    leaves ``eval_duration`` null, so the honest fallback is end-to-end latency -
    labeled as such rather than passed off as generation speed.
    """
    out = _total(records, "output_tokens")
    if not out:
        return None, None
    generation_ms = sum(_numbers(records, "generation_ms"))
    if generation_ms:
        return round(out / (generation_ms / 1000), 3), "generation"
    latency_ms = sum(_numbers(records, "latency_ms"))
    if latency_ms:
        return round(out / (latency_ms / 1000), 3), "end-to-end"
    return None, None


def _group(
    records: Iterable[dict[str, Any]], key: str, *, order: str = "tokens"
) -> list[dict[str, Any]]:
    """Break records down by one key.

    ``order`` is "tokens" (biggest spend first, for a cost ranking) or
    "first_seen" (the order the groups first appear, which for one agent's
    operations is the order it actually worked through them).
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record.get(key) or "-"), []).append(record)
    out = []
    for name, group in buckets.items():
        row = {"name": name}
        row.update(_tokens(group))
        row.update(_cost(group))
        row["calls"] = len(group)
        row["retries"] = sum(1 for r in group if (r.get("retry_attempt") or 0) > 0)
        row["failures"] = sum(1 for r in group if r.get("status") == "failure")
        row["avg_latency_ms"] = _mean(_numbers(group, "latency_ms"))
        out.append(row)
    if order == "tokens":
        out.sort(key=lambda r: r["total_tokens"], reverse=True)
    # "first_seen": a dict keeps insertion order, so ``out`` already holds it.
    return out


def _call_row(record: dict[str, Any]) -> dict[str, Any]:
    """One model call, as a row a report can print."""
    return {
        "timestamp": record.get("timestamp"),
        "operation": record.get("operation") or "-",
        "model": record.get("model"),
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "cache_tokens": record.get("cached_input_tokens"),
        "cost_usd": record.get("estimated_cost_usd"),
        "cost_known": bool(record.get("pricing_known"))
        and isinstance(record.get("estimated_cost_usd"), (int, float)),
        "pricing_source": record.get("pricing_source"),
        "latency_ms": record.get("latency_ms"),
        "status": record.get("status"),
        "retry_attempt": record.get("retry_attempt") or 0,
        "error_type": record.get("error_type"),
    }


def stage_details(
    records: list[dict[str, Any]] | None = None,
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Per agent: its totals, its operations and its calls, in the order they ran.

    ``stage`` names the agent or pipeline stage and ``operation`` the sub-phase
    inside it, so this is the view that answers "what did each agent actually do
    during this run, and what did it cost". Operations and agents both keep the
    order they first appeared in, because that order *is* the run's sequence;
    ``by_operation`` stays cost-ranked for the "what was expensive" question.

    Pass ``run_id`` to look at one workflow. Without it, every record is covered.
    """
    all_records = records if records is not None else read_records()
    if run_id is not None:
        all_records = [r for r in all_records if r.get("run_id") == run_id]

    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in all_records:
        buckets.setdefault(str(record.get("stage") or "-"), []).append(record)

    out: list[dict[str, Any]] = []
    for stage, group in buckets.items():
        ordered = sorted(group, key=lambda r: str(r.get("timestamp") or ""))
        detail: dict[str, Any] = {
            "name": stage,
            "calls": len(ordered),
            "retries": sum(1 for r in ordered if (r.get("retry_attempt") or 0) > 0),
            "failures": sum(1 for r in ordered if r.get("status") == "failure"),
            "avg_latency_ms": _mean(_numbers(group, "latency_ms")),
            "started_at": ordered[0].get("timestamp"),
            "ended_at": ordered[-1].get("timestamp"),
        }
        detail.update(_tokens(group))
        detail.update(_cost(group))
        detail["operations"] = _group(ordered, "operation", order="first_seen")
        detail["calls_detail"] = [_call_row(r) for r in ordered]
        out.append(detail)
    out.sort(key=lambda d: str(d["started_at"] or ""))
    return out


def _run_span_seconds(run_id: str) -> float | None:
    """The run's real start-to-end span from the runs table, if it is there."""
    try:
        from edrak.core.config import settings

        path = Path(settings.get_db_path())
        if not path.is_file():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT started_at, completed_at FROM runs WHERE request_id = ?", (run_id,)
            ).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError, AttributeError, TypeError):
        return None
    if not row:
        return None
    start = _parse_iso(row[0])
    end = _parse_iso(row[1])
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _completed_runs() -> set[str] | None:
    try:
        from edrak.core.config import settings

        path = Path(settings.get_db_path())
        if not path.is_file():
            return None
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT request_id FROM runs WHERE status IN ('completed','partial')"
            ).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}
    except (sqlite3.Error, OSError, AttributeError, TypeError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _span_from_records(records: list[dict[str, Any]]) -> tuple[float | None, str | None, str | None]:
    """Observed span of the tracked calls, used when the runs table is unavailable."""
    starts = [s for s in (_parse_iso(r.get("timestamp")) for r in records) if s]
    if not starts:
        return None, None, None
    start = min(starts)
    end = start
    for record in records:
        begin = _parse_iso(record.get("timestamp")) or start
        latency = record.get("latency_ms")
        if isinstance(latency, (int, float)):
            begin = begin.replace() + _delta(latency)
        end = max(end, begin)
    return (end - start).total_seconds(), start.isoformat(), end.isoformat()


def _delta(milliseconds: float):
    from datetime import timedelta

    return timedelta(milliseconds=milliseconds)


def workflow_summary(run_id: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything one run consumed, split by stage."""
    all_records = records if records is not None else read_records()
    mine = [r for r in all_records if r.get("run_id") == run_id]

    summary: dict[str, Any] = {"run_id": run_id, "calls": len(mine)}
    if not mine:
        return summary

    summary.update(_tokens(mine))
    summary.update(_cost(mine))
    summary["retries"] = sum(1 for r in mine if (r.get("retry_attempt") or 0) > 0)
    summary["failures"] = sum(1 for r in mine if r.get("status") == "failure")

    latencies = _numbers(mine, "latency_ms")
    summary["avg_latency_ms"] = _mean(latencies)
    summary["p95_latency_ms"] = _p95(latencies)
    tps, basis = _throughput(mine)
    summary["throughput_tps"] = tps
    summary["throughput_basis"] = basis

    models = {r.get("model") for r in mine if r.get("model")}
    summary["model"] = ", ".join(sorted(models)) if models else None
    use_cases = {r.get("use_case") for r in mine if r.get("use_case")}
    summary["use_case"] = ", ".join(sorted(use_cases)) if use_cases else None
    providers = {r.get("provider") for r in mine if r.get("provider")}
    summary["provider"] = ", ".join(sorted(providers)) if providers else None

    seconds = _run_span_seconds(run_id)
    if seconds is not None:
        summary["workflow_seconds"] = seconds
        summary["workflow_source"] = "run"
    else:
        observed, _, _ = _span_from_records(mine)
        summary["workflow_seconds"] = observed
        summary["workflow_source"] = "calls" if observed is not None else None

    summary["stages"] = _group(mine, "stage")
    summary["operations"] = _group(mine, "operation")

    starts = [s for s in (_parse_iso(r.get("timestamp")) for r in mine) if s]
    if starts:
        summary["started_at"] = min(starts).isoformat()
    return summary


def system_summary(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Totals across every tracked run, with per-model and per-use-case splits."""
    all_records = records if records is not None else read_records()
    summary: dict[str, Any] = {"calls": len(all_records)}
    if not all_records:
        return summary

    summary.update(_tokens(all_records))
    summary.update(_cost(all_records))
    summary["success"] = sum(1 for r in all_records if r.get("status") == "success")
    summary["failures"] = sum(1 for r in all_records if r.get("status") == "failure")
    summary["retries"] = sum(1 for r in all_records if (r.get("retry_attempt") or 0) > 0)

    latencies = _numbers(all_records, "latency_ms")
    summary["avg_latency_ms"] = _mean(latencies)
    summary["p95_latency_ms"] = _p95(latencies)
    tps, basis = _throughput(all_records)
    summary["throughput_tps"] = tps
    summary["throughput_basis"] = basis

    runs = {r.get("run_id") for r in all_records if r.get("run_id")}
    summary["runs"] = len(runs)
    completed = _completed_runs()
    if completed is not None:
        summary["completed_reports"] = len(completed & runs) if runs else len(completed)
        summary["completed_source"] = "run"
    else:
        summary["completed_reports"] = len(runs)
        summary["completed_source"] = "calls"
    reports = summary["completed_reports"] or 0
    summary["avg_cost_per_report"] = (
        round(summary["cost_usd"] / reports, 8) if reports and summary["cost_known"] else None
    )

    summary["by_model"] = _group(all_records, "model")
    summary["by_use_case"] = _group(all_records, "use_case")
    summary["by_stage"] = _group(all_records, "stage")
    summary["by_operation"] = _group(all_records, "operation")
    return summary


def run_ids(records: list[dict[str, Any]] | None = None) -> list[str]:
    """Every run that has tracked calls, newest run first.

    A record with no ``run_id`` belongs to no run and is skipped, so the list
    never holds an id the per-run page could not describe.
    """
    all_records = records if records is not None else read_records()
    earliest: dict[str, str] = {}
    for record in all_records:
        run_id = record.get("run_id")
        if not run_id:
            continue
        stamp = str(record.get("timestamp") or "")
        if run_id not in earliest or stamp < earliest[run_id]:
            earliest[run_id] = stamp
    return sorted(earliest, key=lambda rid: earliest[rid], reverse=True)


def run_overview(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One row per run, taken from ``workflow_summary`` so a run's page and its
    row on the index cannot disagree. Reads the log once."""
    all_records = records if records is not None else read_records()
    rows: list[dict[str, Any]] = []
    for run_id in run_ids(all_records):
        summary = workflow_summary(run_id, all_records)
        if not summary.get("calls"):
            continue
        rows.append(
            {
                "run_id": run_id,
                "use_case": summary.get("use_case"),
                "calls": summary["calls"],
                "retries": summary.get("retries"),
                "failures": summary.get("failures"),
                "input_tokens": summary.get("input_tokens"),
                "output_tokens": summary.get("output_tokens"),
                "total_tokens": summary.get("total_tokens"),
                "cost_usd": summary.get("cost_usd"),
                "cost_known": summary.get("cost_known", True),
                "workflow_seconds": summary.get("workflow_seconds"),
                "started_at": summary.get("started_at"),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _display_name(name: str) -> str:
    """The bucket for records with no value on the grouping key, spelled out.

    ``_group`` keys them on "-" internally. Showing that row rather than hiding
    it keeps every breakdown table adding up to the same total the banner
    reports, which a table that silently drops those calls would not.
    """
    return "(not captured)" if name == "-" else name


def _money(row: dict[str, Any]) -> str:
    if not row.get("cost_known"):
        return "unknown"
    cost = row.get("cost_usd") or 0.0
    if cost == 0:
        return "$0.00"
    return f"${cost:,.4f}"


def _ms(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"


def _num(value: Any) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) else "-"


def _tps(summary: dict[str, Any]) -> str:
    value = summary.get("throughput_tps")
    if value is None:
        return "-"
    basis = summary.get("throughput_basis")
    return f"{value} tok/s ({basis})" if basis else f"{value} tok/s"


def _seconds(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


_UNICODE = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│",
    "tt": "┬", "bt": "┴", "lt": "├", "rt": "┤", "x": "┼",
}
_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
    "tt": "+", "bt": "+", "lt": "+", "rt": "+", "x": "+",
}


def _stdout_supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "╭─│".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _glyphs(unicode: bool | None) -> dict[str, str]:
    return _UNICODE if (unicode if unicode is not None else _stdout_supports_unicode()) else _ASCII


def _banner(title: str, lines: list[str], width: int, g: dict[str, str]) -> list[str]:
    """A closed box for the report header."""
    inner = max(width - 4, 10)

    def row(text: str) -> str:
        return g["v"] + " " + text[:inner].ljust(inner) + " " + g["v"]

    out = [g["tl"] + g["h"] * (width - 2) + g["tr"], row(title)]
    for line in lines:
        out.append(row(line))
    out.append(g["bl"] + g["h"] * (width - 2) + g["br"])
    return out


def _table(headers: list[str], rows: list[list[str]], width: int, g: dict[str, str]) -> list[str]:
    columns = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    # Spread any leftover width over the columns so the box stays aligned.
    slack = width - 4 - sum(widths) - 3 * (columns - 1)
    if slack > 0:
        widths[0] += slack

    def line(cells: list[str]) -> str:
        body = f" {g['v']} ".join(c.ljust(w) for c, w in zip(cells, widths))
        return g["v"] + body + g["v"]

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join(g["h"] * (w + 2) for w in widths) + right

    out = [rule(g["tl"], g["tt"], g["tr"]), line(headers), rule(g["lt"], g["x"], g["rt"])]
    for row in rows:
        out.append(line(row))
    out.append(rule(g["bl"], g["bt"], g["br"]))
    return out


def _group_table(label: str, groups: list[dict[str, Any]], width: int, g: dict[str, str]) -> list[str]:
    """One breakdown table: calls, retries, failures, tokens, cost and latency per group.

    When no record carried the key at all there is nothing to break down, so the
    dimension is reported as not captured rather than printed as a single
    meaningless "-" row. When only some records carried it, those calls are shown
    as "(not captured)" so the table still adds up to the total above it.
    """
    if not groups or all(row["name"] == "-" for row in groups):
        return ["", f"{label}: not captured in these records"]
    rows = [
        [
            _display_name(row["name"]),
            _num(row["calls"]),
            _num(row["retries"]),
            _num(row["failures"]),
            _num(row["input_tokens"]),
            _num(row["output_tokens"]),
            _money(row),
            _ms(row["avg_latency_ms"]),
        ]
        for row in groups
    ]
    return [
        "",
        label,
        *_table(
            ["name", "calls", "retry", "fail", "in tok", "out tok", "cost (USD)", "avg lat"],
            rows,
            width,
            g,
        ),
    ]


def render_workflow(run_id: str, *, width: int = 92, unicode: bool | None = None) -> str:
    summary = workflow_summary(run_id)
    if not summary.get("calls"):
        return f"No LLM calls recorded for run {run_id!r} at {_log_hint()}"

    g = _glyphs(unicode)
    head = _banner(
        f"EDRAK LLM USAGE — run {run_id}",
        [
            f"use case: {summary.get('use_case') or '-'} · model: {summary.get('model') or '-'} · provider: {summary.get('provider') or '-'}",
            f"workflow: {_seconds(summary.get('workflow_seconds'))} ({summary.get('workflow_source')}) · calls {summary['calls']} · retries {summary['retries']} · failures {summary['failures']}",
        ],
        width,
        g,
    )

    rows = []
    for stage in summary["stages"]:
        rows.append(
            [
                _display_name(stage["name"]),
                _num(stage["calls"]),
                _num(stage["retries"]),
                _num(stage["failures"]),
                _num(stage["input_tokens"]),
                _num(stage["output_tokens"]),
                _money(stage),
                _ms(stage["avg_latency_ms"]),
            ]
        )
    rows.append(
        [
            "TOTAL",
            _num(summary["calls"]),
            _num(summary["retries"]),
            _num(summary["failures"]),
            _num(summary["input_tokens"]),
            _num(summary["output_tokens"]),
            _money(summary),
            _ms(summary["avg_latency_ms"]),
        ]
    )
    table = _table(
        ["stage", "calls", "retry", "fail", "in tok", "out tok", "cost (USD)", "avg lat"],
        rows,
        width,
        g,
    )
    foot = f"tokens: {_num(summary['total_tokens'])} total · throughput {_tps(summary)} · P95 {_ms(summary['p95_latency_ms'])}"
    return "\n".join([*head, *table, foot, *_group_table("by operation", summary.get("operations") or [], width, g)])


def render_stages(run_id: str, *, width: int = 92, unicode: bool | None = None) -> str:
    return render_workflow(run_id, width=width, unicode=unicode)


def render_system(*, width: int = 92, unicode: bool | None = None) -> str:
    summary = system_summary()
    if not summary.get("calls"):
        return f"No LLM calls recorded yet at {_log_hint()}"

    g = _glyphs(unicode)
    title = "EDRAK LLM USAGE — system"
    lines = [
        f"calls {summary['calls']} · success {summary['success']} · failures {summary['failures']} · retries {summary['retries']}",
        f"tokens {_num(summary['total_tokens'])} (in {_num(summary['input_tokens'])} / out {_num(summary['output_tokens'])})",
        f"estimated cost {_money(summary)} · runs {summary['runs']} · completed reports {summary['completed_reports']} ({summary.get('completed_source')})",
        f"avg cost / report {_money({'cost_known': summary['cost_known'], 'cost_usd': summary.get('avg_cost_per_report') or 0.0}) if summary.get('avg_cost_per_report') is not None else 'unknown'}",
        f"latency avg {_ms(summary['avg_latency_ms'])} · P95 {_ms(summary['p95_latency_ms'])} · throughput {_tps(summary)}",
    ]
    head = _banner(title, lines, width, g)

    body = [
        *_group_table("by stage", summary["by_stage"], width, g),
        *_group_table("by operation", summary["by_operation"], width, g),
        *_group_table("by model", summary["by_model"], width, g),
        *_group_table("by use case", summary["by_use_case"], width, g),
    ]
    return "\n".join([*head, *body])


def _log_hint() -> str:
    from .store import log_dir

    return str(log_dir() / "llm_calls.jsonl")


def flush_log() -> bool:
    return flush()
