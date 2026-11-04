"""The usage report as a self-contained HTML page.

Presentation only. Every figure comes from ``report`` - the same aggregations the
terminal report prints - so a page and the CLI cannot disagree, including the two
rules that matter: an unpriced model reads *unknown* rather than ``$0.00``, and a
dimension no record captured says so instead of drawing an empty table.

One inline stylesheet, no JavaScript and no external assets, so the file opens
straight from the filesystem and works offline. Written next to the log by
``scripts/llm_usage_report.py --html``.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

from . import report
from .report import _display_name, _money, _ms, _num, _seconds, _tps
from .store import read_records

__all__ = [
    "render_index_html",
    "render_run_html",
    "render_system_html",
    "run_file_name",
    "write_index",
    "write_run_page",
]

# The numeric columns are shared by every table so the same figure reads the
# same way wherever it appears. "name" is the first column, replaced per table.
_METRIC_HEADERS = ["calls", "retry", "fail", "in tok", "out tok", "cost (USD)", "avg lat", "share"]

_CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--line:#e2e5ea;--ink:#16181d;--muted:#5a6472;--accent:#3b6ef6;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#16181d;--panel:#1d2026;--line:#2c313a;--ink:#e9ecf1;--muted:#98a2b3;--accent:#6d95ff;--bad:#ff6b5e}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.35rem;margin:0 0 1rem}
h2{font-size:.95rem;margin:2rem 0 .6rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
h2:first-child{margin-top:0}
h3{font-size:1.05rem;margin:0 0 .5rem;display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
h4{font-size:.78rem;margin:1.1rem 0 .4rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
a{color:var(--accent)}
code{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace}
.muted{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem 1.25rem}
.stats{list-style:none;margin:0;padding:0;display:grid;gap:.3rem 2rem;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.stats li{display:flex;justify-content:space-between;gap:1rem;padding:.2rem 0;border-bottom:1px dashed var(--line)}
.stats b{font-variant-numeric:tabular-nums;font-weight:600}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:.5rem .7rem;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{background:rgba(127,127,127,.10);font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600}
tbody tr+tr td{border-top:1px solid var(--line)}
td.n,strong{font-variant-numeric:tabular-nums}
td.bad{color:var(--bad);font-weight:600;font-variant-numeric:tabular-nums}
td.share{width:150px}
.bar{display:block;height:6px;border-radius:3px;background:rgba(127,127,127,.22);overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
footer{margin-top:2rem;color:var(--muted);font-size:.85rem}
nav.agents{display:flex;flex-wrap:wrap;gap:.35rem .9rem;margin:.2rem 0 1.1rem;font-size:.9rem}
.agent{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem 1.15rem 1.15rem;margin:0 0 1.15rem}
.agent .panel{background:transparent;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:0 0 .5rem;margin-bottom:.9rem}
""".strip()


def _page(title: str, sections: list[str]) -> str:
    body = "\n".join(section for section in sections if section)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{escape(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{escape(title)}</h1>\n{body}\n"
        "<footer>Generated from the EDRAK LLM usage log by "
        "<code>scripts/llm_usage_report.py --html</code>. Costs are provider-rated "
        "estimates; a model with no known price reads <i>unknown</i>.</footer>\n"
        "</main>\n</body>\n</html>\n"
    )


def _stats(items: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<li><span>{escape(label)}</span><b>{escape(value)}</b></li>" for label, value in items
    )
    return f'<section class="panel"><ul class="stats">{rows}</ul></section>'


def _table(headers: list[str], body: list[str]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _metric_cells(row: dict[str, Any], peak: int, *, strong: bool = False) -> str:
    """The first column plus the shared numeric columns, so tables cannot drift apart."""
    name = escape(_display_name(str(row["name"])))
    label = f"<strong>{name}</strong>" if strong else name
    share = round(100 * (row.get("total_tokens") or 0) / peak)
    fail_class = "bad" if row.get("failures") else "n"
    return (
        f"<td>{label}</td>"
        f'<td class="n">{_num(row.get("calls"))}</td>'
        f'<td class="n">{_num(row.get("retries"))}</td>'
        f'<td class="{fail_class}">{_num(row.get("failures"))}</td>'
        f'<td class="n">{_num(row.get("input_tokens"))}</td>'
        f'<td class="n">{_num(row.get("output_tokens"))}</td>'
        f'<td class="n">{_money(row)}</td>'
        f'<td class="n">{_ms(row.get("avg_latency_ms"))}</td>'
        f'<td class="share"><span class="bar"><i style="width:{share}%"></i></span></td>'
    )


def _group_section(label: str, groups: list[dict[str, Any]]) -> str:
    """One breakdown table, or the honest sentence when nothing was captured.

    A row is never drawn as a bare "-": if no record carried the key the dimension
    is reported as not captured, and if only some did, the rest is shown as
    "(not captured)" so the rows still add up to the total above them.
    """
    heading = f"<h2>{escape(label)}</h2>"
    if not groups or all(row["name"] == "-" for row in groups):
        return heading + f'<p class="muted">{escape(label + ": not captured in these records")}</p>'
    peak = max((row.get("total_tokens") or 0) for row in groups) or 1
    body = [f"<tr>{_metric_cells(row, peak)}</tr>" for row in groups]
    return heading + _table(["name", *_METRIC_HEADERS], body)


def _avg_cost(summary: dict[str, Any]) -> str:
    value = summary.get("avg_cost_per_report")
    if value is None:
        return "unknown"
    return _money({"cost_known": summary.get("cost_known", True), "cost_usd": value})


def _system_sections(summary: dict[str, Any]) -> list[str]:
    stats = _stats(
        [
            ("calls", _num(summary.get("calls"))),
            ("success", _num(summary.get("success"))),
            ("failures", _num(summary.get("failures"))),
            ("retries", _num(summary.get("retries"))),
            ("input tokens", _num(summary.get("input_tokens"))),
            ("output tokens", _num(summary.get("output_tokens"))),
            ("total tokens", _num(summary.get("total_tokens"))),
            ("estimated cost", _money(summary)),
            ("runs", _num(summary.get("runs"))),
            ("completed reports", f"{_num(summary.get('completed_reports'))} ({summary.get('completed_source')})"),
            ("avg cost / report", _avg_cost(summary)),
            ("avg latency", _ms(summary.get("avg_latency_ms"))),
            ("P95 latency", _ms(summary.get("p95_latency_ms"))),
            ("throughput", _tps(summary)),
        ]
    )
    return [
        stats,
        _group_section("by stage", summary["by_stage"]),
        _group_section("by operation", summary["by_operation"]),
        _group_section("by model", summary["by_model"]),
        _group_section("by use case", summary["by_use_case"]),
    ]


def _run_section(rows: list[dict[str, Any]], records: list[dict[str, Any]] | None = None) -> str:
    """The index's list of runs, each linking to its own page."""
    heading = "<h2>runs</h2>"
    if not rows:
        return heading + '<p class="muted">no run has tracked calls yet, so there is nothing to link to</p>'
    body = []
    for row in rows:
        fail_class = "bad" if row.get("failures") else "n"
        href = escape(run_file_name(row["run_id"], records), quote=True)
        body.append(
            "<tr>"
            f'<td><a href="{href}"><code>{escape(str(row["run_id"]))}</code></a></td>'
            f'<td>{escape(str(row.get("use_case") or "-"))}</td>'
            f'<td class="n">{_num(row.get("calls"))}</td>'
            f'<td class="n">{_num(row.get("retries"))}</td>'
            f'<td class="{fail_class}">{_num(row.get("failures"))}</td>'
            f'<td class="n">{_num(row.get("input_tokens"))}</td>'
            f'<td class="n">{_num(row.get("output_tokens"))}</td>'
            f'<td class="n">{_money(row)}</td>'
            f'<td class="n">{_seconds(row.get("workflow_seconds"))}</td>'
            "</tr>"
        )
    headers = ["run", "use case", "calls", "retry", "fail", "in tok", "out tok", "cost (USD)", "workflow"]
    return heading + _table(headers, body)


_CALL_HEADERS = ["time", "operation", "model", "in tok", "out tok", "cost (USD)", "latency", "status"]


def _anchor(name: str) -> str:
    """A page anchor for one agent, whatever its name contains."""
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(name)).strip("-")
    return f"agent-{slug or 'unnamed'}"


def _short_time(value: Any) -> str:
    """``HH:MM:SS`` out of an ISO timestamp, so a call list stays scannable."""
    text = str(value or "")
    if len(text) >= 19 and text[10] == "T":
        return text[11:19]
    return text or "-"


def _calls_table(calls: list[dict[str, Any]]) -> str:
    """One row per model call, in the order that agent made them."""
    body = []
    for call in calls:
        attempt = int(call.get("retry_attempt") or 0)
        status = str(call.get("status") or "-")
        if attempt:
            status = f"{status} · attempt {attempt + 1}"
        if call.get("error_type"):
            status = f"{status} ({call['error_type']})"
        status_class = "bad" if call.get("status") == "failure" else "n"
        stamp = str(call.get("timestamp") or "")
        body.append(
            "<tr>"
            f'<td class="n" title="{escape(stamp, quote=True)}">{escape(_short_time(stamp))}</td>'
            f"<td>{escape(str(call.get('operation') or '-'))}</td>"
            f"<td>{escape(str(call.get('model') or '-'))}</td>"
            f'<td class="n">{_num(call.get("input_tokens"))}</td>'
            f'<td class="n">{_num(call.get("output_tokens"))}</td>'
            f'<td class="n">{_money(call)}</td>'
            f'<td class="n">{_ms(call.get("latency_ms"))}</td>'
            f'<td class="{status_class}">{escape(status)}</td>'
            "</tr>"
        )
    return _table(_CALL_HEADERS, body)


def _agent_section(detail: dict[str, Any]) -> str:
    """One agent: what it spent, the parts it worked through, then every call."""
    name = _display_name(str(detail["name"]))
    operations = detail.get("operations") or []
    peak = max((row.get("total_tokens") or 0) for row in operations) or 1
    stats = _stats(
        [
            ("calls", _num(detail.get("calls"))),
            ("retries", _num(detail.get("retries"))),
            ("failures", _num(detail.get("failures"))),
            ("input tokens", _num(detail.get("input_tokens"))),
            ("output tokens", _num(detail.get("output_tokens"))),
            ("total tokens", _num(detail.get("total_tokens"))),
            ("estimated cost", _money(detail)),
            ("avg latency", _ms(detail.get("avg_latency_ms"))),
            ("first call", _short_time(detail.get("started_at"))),
            ("last call", _short_time(detail.get("ended_at"))),
        ]
    )
    body = [f"<tr>{_metric_cells(row, peak)}</tr>" for row in operations]
    return (
        f'<section class="agent" id="{_anchor(detail["name"])}">'
        f"<h3>{escape(name)}</h3>"
        f"{stats}"
        f"<h4>by operation</h4>{_table(['operation', *_METRIC_HEADERS], body)}"
        f"<h4>calls</h4>{_calls_table(detail.get('calls_detail') or [])}"
        "</section>"
    )


def _agent_sections(details: list[dict[str, Any]]) -> str:
    """Every agent that ran, in the order each first called a model."""
    heading = "<h2>agents through the run</h2>"
    if not details:
        return heading + '<p class="muted">this run made no model calls</p>'
    links = "".join(
        f'<a href="#{_anchor(d["name"])}">{escape(_display_name(str(d["name"])))}</a>' for d in details
    )
    return heading + f'<nav class="agents">{links}</nav>' + "".join(_agent_section(d) for d in details)


def _comparison_nav() -> str:
    """Links to the hand-written comparison pages kept beside the generated ones."""
    from .store import log_dir

    try:
        pages = sorted(path.name for path in log_dir().glob("comparison-*.html"))
    except OSError:
        return ""
    if not pages:
        return ""
    links = "".join(
        f'<a href="{escape(name, quote=True)}">'
        f'{escape(name[len("comparison-"):-len(".html")].replace("-", " "))}</a>'
        for name in pages
    )
    return f"<h2>comparisons</h2><nav class=\"agents\">{links}</nav>"


def render_system_html(records: list[dict[str, Any]] | None = None) -> str:
    """Totals across every tracked call."""
    summary = report.system_summary(records)
    title = "EDRAK LLM usage — system"
    if not summary.get("calls"):
        return _page(title, [_empty_note()])
    return _page(title, [_comparison_nav(), *_system_sections(summary)])


def render_index_html(records: list[dict[str, Any]] | None = None) -> str:
    """The system view plus a row per run linking to its page."""
    all_records = records if records is not None else read_records()
    summary = report.system_summary(all_records)
    title = "EDRAK LLM usage"
    if not summary.get("calls"):
        return _page(title, [_empty_note()])
    return _page(
        title,
        [_comparison_nav(), _run_section(report.run_overview(all_records), all_records), *_system_sections(summary)],
    )


def render_run_html(run_id: str, records: list[dict[str, Any]] | None = None) -> str:
    """One workflow: its banner, then every agent's calls through the run."""
    all_records = records if records is not None else read_records()
    summary = report.workflow_summary(run_id, all_records)
    title = f"EDRAK LLM usage — run {run_id}"
    if not summary.get("calls"):
        return _page(
            title,
            [
                (
                    f'<p class="muted">No LLM calls recorded for run '
                    f'<code>{escape(str(run_id))}</code> at <code>{escape(str(report._log_hint()))}</code>.</p>'
                )
            ],
        )

    stats = _stats(
        [
            ("use case", str(summary.get("use_case") or "-")),
            ("model", str(summary.get("model") or "-")),
            ("provider", str(summary.get("provider") or "-")),
            ("workflow", f"{_seconds(summary.get('workflow_seconds'))} ({summary.get('workflow_source')})"),
            ("calls", _num(summary.get("calls"))),
            ("retries", _num(summary.get("retries"))),
            ("failures", _num(summary.get("failures"))),
            ("input tokens", _num(summary.get("input_tokens"))),
            ("output tokens", _num(summary.get("output_tokens"))),
            ("total tokens", _num(summary.get("total_tokens"))),
            ("estimated cost", _money(summary)),
            ("avg latency", _ms(summary.get("avg_latency_ms"))),
            ("P95 latency", _ms(summary.get("p95_latency_ms"))),
            ("throughput", _tps(summary)),
        ]
    )

    named_stages = [row for row in (summary.get("stages") or []) if row["name"] != "-"]
    if named_stages:
        peak = max((row.get("total_tokens") or 0) for row in named_stages) or 1
        body = [f"<tr>{_metric_cells(row, peak)}</tr>" for row in named_stages]
        total = {"name": "TOTAL", "avg_latency_ms": summary.get("avg_latency_ms")}
        total.update({key: summary.get(key) for key in ("calls", "retries", "failures", "input_tokens",
                                                       "output_tokens", "total_tokens", "cost_usd", "cost_known")})
        body.append(f"<tr>{_metric_cells(total, peak, strong=True)}</tr>")
        stage_section = "<h2>by stage</h2>" + _table(["stage", *_METRIC_HEADERS], body)
    else:
        # Every call in this run was made outside a stage; say so rather than
        # drawing a table whose only row is a dash.
        stage_section = _group_section("by stage", [])

    return _page(
        title,
        [
            stats,
            stage_section,
            _agent_sections(report.stage_details(all_records, run_id=run_id)),
            _group_section("by operation", summary.get("operations") or []),
        ],
    )


def _run_slug(run_id: str, records: list[dict[str, Any]]) -> str:
    """A readable word for a run: its use case and the model it mostly used.

    Only used for opaque ids (a 32-character hex hash). A run that already has
    a speaking id keeps it: `smoke-qwen-paced` should not become
    `qwen-smoke-qwen-paced`.
    """
    rows = [row for row in records if row.get("run_id") == run_id]
    if not rows:
        return ""
    models: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "success":
            continue  # a failed call can carry a fallback label, not a real model
        model = str(row.get("model") or "").strip()
        if model:
            models[model] = models.get(model, 0) + 1
    mark = ""
    if not models:
        # A run that never succeeded still deserves a name; say so.
        for row in rows:
            model = str(row.get("model") or "").strip()
            if model:
                models[model] = models.get(model, 0) + 1
        mark = "failed"
    parts = []
    use_case = next((str(row.get("use_case") or "") for row in rows if row.get("use_case")), "")
    if use_case:
        parts.append(use_case)
    if models:
        model = max(models, key=models.get)  # the model that made most of the calls
        parts.append(model.split("/")[-1].replace(":", "-"))
    if mark:
        parts.append(mark)
    slug = "-".join(parts).lower()
    slug = re.sub(r"[^a-z0-9.]+", "-", slug).strip("-")
    return slug[:48].strip("-")


def _is_opaque(run_id: str) -> bool:
    """True for a run id that says nothing to a reader, such as a hex hash."""
    text = str(run_id)
    return len(text) >= 16 and all(ch in "0123456789abcdef" for ch in text.lower())


def run_file_name(run_id: str, records: list[dict[str, Any]] | None = None) -> str:
    """A filename any filesystem accepts, saying what the run was.

    `run-<use case>-<model>-<8 hex>.html` when the id is a hash and the log
    knows the run; otherwise just the id, so a speaking id stays readable and
    a pathological one stays safe.
    """
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(run_id))
    if records is not None and _is_opaque(str(run_id)):
        slug = _run_slug(str(run_id), records)
        if slug:
            return f"run-{slug}-{safe[:8]}.html"
    return f"run-{safe or 'unknown'}.html"


def write_index(directory: Path, records: list[dict[str, Any]] | None = None) -> list[Path]:
    """Write ``index.html``, ``system.html`` and one page per run. Returns the paths."""
    all_records = records if records is not None else read_records()
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in (
        ("index.html", render_index_html(all_records)),
        ("system.html", render_system_html(all_records)),
    ):
        path = directory / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    written.extend(write_run_page(directory, run_id, all_records) for run_id in report.run_ids(all_records))
    return written


def write_run_page(directory: Path, run_id: str, records: list[dict[str, Any]] | None = None) -> Path:
    """Write just one run's page. Returns the path."""
    all_records = records if records is not None else read_records()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / run_file_name(run_id, all_records)
    path.write_text(render_run_html(run_id, records), encoding="utf-8")
    return path


def _empty_note() -> str:
    return (
        f'<p class="muted">No LLM calls recorded yet at '
        f'<code>{escape(str(report._log_hint()))}</code>.</p>'
    )
