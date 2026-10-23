from __future__ import annotations

import argparse
import copy
import csv
import difflib
import email.utils
import glob
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlparse

MAX_CTX_CHARS = 3500          # per evidence text sent to the judge
MAX_CTX_ITEMS = 8             # evidence items per unit sent to the judge
TRUE = (True, "True", "true", 1)
# prices, percentages, big numbers, decimals. Years are handled separately (YEAR_RE).
NUM_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%|\b(?!(?:19|20)\d{2}\b)\d{3,}(?:,\d{3})*\b|\b\d+\.\d+\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
# "no subscription tiers are mentioned", "not specified", "none found": an absence of evidence stated as a fact
ABSENCE_RE = re.compile(r"\b(?:no|not|none|neither|nor|without)\b[^.;]{0,80}?\b"
                        r"(?:mentioned|stated|specified|disclosed|reported|listed|found|indicated|provided)\b", re.I)
CONF_WORDS = {"low": 0.3, "medium": 0.6, "high": 0.85}      # some agents report confidence as a word
EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_CACHE_FLUSH_LOCK = threading.Lock()

# DeepEval metrics: name -> (DeepEval class, needs retrieval context, flag raised when it fails)
METRICS = {
    "faithfulness":      ("FaithfulnessMetric",         True,  "unfaithful"),
    "relevancy":         ("AnswerRelevancyMetric",      False, "low_relevancy"),
    "context_relevancy": ("ContextualRelevancyMetric",  True,  "irrelevant_evidence"),
    "context_precision": ("ContextualPrecisionMetric",  True,  "low_context_precision"),
    "context_recall":    ("ContextualRecallMetric",     True,  "low_context_recall"),
    "specificity":       ("GEval",                      False, "generic_statement"),
    "insight":           ("GEval",                      True,  "no_added_insight"),
    "actionability":     ("GEval",                      True,  "not_actionable"),
}
# Custom (GEval) metrics: name -> (criteria, uses retrieval context). Changing a criterion invalidates its cache.
GEVAL = {
    "specificity": ("Score how specific and decision-useful the statement is. High: concrete facts, names, numbers, "
                    "dates or places that someone could check. Low: vague, generic or boilerplate wording that "
                    "would be true of almost any company.", False),
    "insight": ("The output is a strategic signal built from several findings (the context). Score high only if it "
                "states something that goes beyond restating any single finding (a pattern, gap, tension or "
                "consequence across findings) AND its interpretation and implications follow reasonably from the "
                "findings. Score low if it merely repeats one finding or makes leaps the findings do not support.",
                True),
    "actionability": ("The output is a business recommendation built from signals (the context). Score high if it "
                      "is specific (what to do, where, by when or in what order), measurable (success criteria or "
                      "indicators), consistent with the signals, and honest about uncertainty and evidence gaps. "
                      "Score low if it is vague, unmeasurable, contradicts the signals or ignores stated gaps.",
                      True),
}
# Worker findings need domain-specific criteria; they are not signals or recommendations.
GEVAL_BY_AGENT = {
    "worker": {
        "insight": ("The output is a research finding and the context is its source evidence. Score high only if it "
                    "goes beyond restating a source sentence and explains a relevant implication for the stated "
                    "business goal, with reasoning supported by the evidence. Score low if it only repeats a source "
                    "or draws conclusions the evidence does not support.",
                    True),
        "actionability": ("The output is a research finding and the context is its source evidence. Score high if "
                          "it gives a concrete, decision-useful fact, constraint, trend, customer need, or mechanism "
                          "that a business owner can investigate or compare. Do not require a competitor name unless "
                          "the research question is specifically about competitors. Score low if it is too vague or "
                          "generic to inform a decision.", True),
    },
    "competitor_intelligence": {
        "actionability": ("The output is a competitor research finding and the context is its source evidence. Score "
                          "high if it gives a product, pricing, or strategy owner a concrete competitor mechanism "
                          "(such as a fee, threshold, program, delivery promise, or condition) and a useful basis for "
                          "comparison. Score low if it is too vague or generic to inform a decision.", True),
    },
    "internal_intelligence": {
        "actionability": ("The output is an internal research finding and the context is its source evidence. Score "
                          "high if it identifies a concrete internal capability, constraint, decision, or priority "
                          "that an owner can use to inform the business goal. Score low if it is vague, generic, or "
                          "not supported by the supplied internal evidence.", True),
    },
    "market_intelligence": {
        "insight": ("The output is a market research finding and the context is its source evidence. Score high only "
                    "if it explains a supported implication of a market, economic, regulatory, or industry "
                    "development for the stated business goal. Do not require a competitor comparison. Score low if "
                    "it only restates a headline or makes unsupported forecasts.", True),
    },
    "customer_trends": {
        "insight": ("The output is a customer and trends research finding and the context is its source evidence. "
                    "Score high only if it explains a supported customer need, sentiment pattern, behavior, or "
                    "demand implication relevant to the stated business goal. Account for sample and source limits; "
                    "do not require a competitor comparison. Score low if it overgeneralizes or merely repeats a post.",
                    True),
    },
}
EXECUTION_GEVAL = {
    "tool_selection": (
        "Given the business task and the tools available in the trace, judge whether the agent "
        "selected appropriate tools and used them in a sensible order. Do not assume tools or "
        "requirements that are not present in the supplied trace.",
        False,
    ),
    "tool_arguments": (
        "Given the task, tool names, schemas, and arguments, judge whether each tool call supplied "
        "appropriate and valid arguments. Do not infer argument values that are not shown.",
        False,
    ),
    "tool_result_use": (
        "Judge whether the final agent output uses the supplied tool results correctly, avoids "
        "unsupported claims, and acknowledges missing or failed results.",
        True,
    ),
    "workflow_sequence": (
        "Compare the observed ordered execution trace with the expected workflow supplied in the "
        "context. Judge whether required steps and dependencies were followed.",
        True,
    ),
    "error_handling": (
        "Judge whether tool failures, empty results, retries, and fallbacks were handled "
        "appropriately according to the task and the recorded execution trace.",
        False,
    ),
    "efficiency": (
        "Judge whether the execution trace avoids unnecessary repeated tool calls and work while "
        "still gathering enough information for the task.",
        False,
    ),
}
EXECUTION_QUALITY_EXCLUDED = {"verification", "cross_signal", "decision", "orchestration"}
WORKER_ALLOWED_TOOLS = {
    "internal_intelligence": ("internal_rag.retrieve",),
    "competitor_intelligence": ("competitor_web_search",),
    "market_intelligence": ("market_search",),
    "customer_trends": (
        "account_posts",
        "analyze_text",
        "compute_metrics",
        "evidence_query",
        "fetch_page",
        "news_coverage",
        "reviews_fetch",
        "search_interest",
        "social_comments",
        "social_search",
        "submit_findings",
        "web_search",
    ),
}
TOOL_ARGUMENT_SCHEMAS = {
    "internal_rag.retrieve": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
            "min_score": {"type": "number"},
            "where_filter": {"type": "object"},
            "workspace_id": {"type": "string"},
            "allow_all_workspaces": {"type": "boolean"},
            "origin": {"type": ["string", "object"]},
        },
    },
    "competitor_web_search": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
            "stage": {"type": "integer"},
            "domains": {"type": ["array", "null"]},
        },
    },
    "market_search": {
        "type": "object",
        "required": ["queries"],
        "properties": {"queries": {"type": "array"}},
    },
}
CUSTOM = tuple(GEVAL)
AGENT_METRICS = {
    "worker":       ["faithfulness", "relevancy", "specificity", "insight", "actionability"],
    "verification": ["faithfulness", "relevancy"],
    "cross_signal": ["faithfulness", "relevancy", "insight"],
    "decision":     ["faithfulness", "relevancy", "actionability"],
}
# Workers judge only the metrics that fit their evidence; unlisted worker types keep the generic worker set.
WORKER_METRICS = {
    "internal_intelligence":   ["faithfulness", "relevancy", "actionability"],
    "competitor_intelligence": ["faithfulness", "relevancy", "specificity", "actionability"],
    "market_intelligence":     ["faithfulness", "relevancy", "specificity", "insight"],
    "customer_trends":         ["faithfulness", "relevancy", "insight"],
}


# =============================================================================== 1. small helpers
def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")
    return data


def detect(d: dict[str, Any]) -> str:
    """Detect pipeline outputs, orchestration plans, RAG traces, and generic observability payloads."""
    if d.get("metrics_stage") is True:
        return "metrics"
    if "company_profile" in d and "research_goal" in d:
        return "input"
    if any(k in d for k in ("retrieval_results", "retrieved_documents", "retrieved_chunks")) and any(
            k in d for k in ("query", "user_query", "retrieval_query")):
        return "rag"
    if "question_recommendations" in d:
        return "decision"
    if "signals" in d and "decision_ready_context" in d:
        return "cross_signal"
    if "plan" in d and "results" in d:
        return "orchestration"
    if isinstance(d.get("findings"), list):
        if "control_summary" in d:
            return "verification"
        if d.get("worker"):
            return "worker"
    if any(
        key in d
        for key in (
            "spans",
            "trace_events",
            "observability_events",
            "tool_traces",
            "events",
            "tool_calls",
            "tools_used",
            "tool_events",
        )
    ):
        return "telemetry"
    return "unknown"


def parse_ts(s: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def file_time(kind: str, d: dict[str, Any]) -> datetime | None:
    """When the file was produced. Used to put multi-phase runs in order."""
    keys = {"worker": ("completed_at", "started_at"), "verification": ("completed_at",),
            "cross_signal": ("created_at",), "decision": ("created_at",)}.get(kind, ())
    for k in keys:
        t = parse_ts(d.get(k))
        if t:
            return t
    return None


def dict_items(x: Any) -> list[dict]:
    return [i for i in (x or []) if isinstance(i, dict)]


def comp_name(c: Any) -> str:
    """Competitors are plain strings in some use cases and objects ({'name': ...}) in others."""
    return str(c if isinstance(c, str) else (c or {}).get("name") or (c or {}).get("competitor") or "").strip()


def evidence_map(items: Any) -> dict[str, dict]:
    return {str(e["evidence_id"]): e for e in dict_items(items) if e.get("evidence_id")}


def cited_ids(finding: dict[str, Any]) -> list[str]:
    refs = finding.get("evidence_ids", finding.get("evidence_refs", [])) or []
    out = []
    for r in refs:
        rid = r.get("evidence_id") if isinstance(r, dict) else r
        if rid:
            out.append(str(rid))
    return out


def ev_text(rec: dict[str, Any]) -> str:
    """Source text of an evidence record. A title or URL alone is not source text."""
    body = str(rec.get("excerpt") or rec.get("extracted_fact") or rec.get("text") or rec.get("page_text") or "").strip()
    if not body:
        return ""
    title = str(rec.get("source_title") or "").strip()
    if title.startswith("http") or title in body:
        title = ""
    return (f"{title}\n{body}" if title else body)[:MAX_CTX_CHARS]


def is_synthetic(rec: dict[str, Any]) -> bool:
    return rec.get("is_synthetic") in TRUE or str(rec.get("source_type", "")).startswith("synthetic")


def ev_meta(rec: dict[str, Any]) -> dict[str, Any]:
    m = rec.get("metadata")
    return m if isinstance(m, dict) else {}


def retrieval_score(rec: dict[str, Any]) -> float | None:
    m = ev_meta(rec)
    for k in ("score", "relevance_score"):
        if is_num(m.get(k)):
            return m[k]
    return None


def rag_document_score(rec: dict[str, Any]) -> Any:
    """Support both retrieval DTO scores and Evidence.metadata relevance scores."""
    for key in ("score", "relevance_score"):
        if key in rec:
            return rec[key]
    metadata = rec.get("metadata")
    if isinstance(metadata, dict):
        for key in ("score", "relevance_score", "confidence_score"):
            if key in metadata:
                return metadata[key]
    return None


def domain(url: Any) -> str:
    return urlparse(str(url or "")).netloc.lower().removeprefix("www.")


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_finite_num(v: Any) -> bool:
    if not is_num(v):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def conf_num(v: Any) -> float | None:
    """Numeric confidence; words (low/medium/high) are mapped so every agent can be compared."""
    if is_num(v):
        return float(v)
    return CONF_WORDS.get(str(v).strip().lower())


def pct(n, d) -> float | None:
    return round(100 * n / d, 2) if d else None


def mean(vals: list) -> float | None:
    vals = [v for v in vals if is_num(v)]
    return round(sum(vals) / len(vals), 4) if vals else None


def stdev(vals: list) -> float | None:
    vals = [v for v in vals if is_num(v)]
    return round(statistics.pstdev(vals), 4) if len(vals) > 1 else None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$%. ]+", " ", (s or "").lower())).strip()


def near_dup(a: str, b: str) -> bool:
    return bool(a and b) and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


def absence_claim(text: str) -> bool:
    """True when a statement presents an absence of evidence ('no tiers are mentioned') as a fact."""
    return bool(ABSENCE_RE.search(text or ""))


def ungrounded_numbers(output: str, ctx: list[str]) -> list[str]:
    """Prices / percentages / large numbers in `output` that never appear in `ctx` (deterministic hallucination check)."""
    raw = " ".join(ctx).lower()
    blob = re.sub(r"[\s,]", "", raw)
    words = {"20%": "one fifth", "25%": "a quarter|one quarter|one fourth", "33%": "one third|a third", "50%": "half"}
    ok = lambda m: re.sub(r"[\s,]", "", m.lower()) in blob or (m.strip() in words and re.search(words[m.strip()], raw))
    return sorted({m.strip() for m in NUM_RE.findall(output or "") if not ok(m)})


def past_years(text: str, ref_year: int | None) -> list[int]:
    """Years mentioned in `text` that are already over at the time the pipeline ran."""
    return sorted({int(y) for y in YEAR_RE.findall(text or "") if ref_year and int(y) < ref_year})


def check(name: str, ok: bool, detail: str = "", severity: str = "error") -> dict[str, Any]:
    """severity 'error' = integrity problem; 'warn' = quality signal or run state worth reading."""
    return {"check": name, "passed": bool(ok), "detail": detail, "severity": severity}


def count_check(label: str, meta: dict, keys: tuple[str, ...], actual: int) -> dict | None:
    """'metadata says N, file has N'. Returns None (skipped) when this use case's metadata does not have the key."""
    for k in keys:
        if k in meta:
            return check(f"metadata.{k} matches {label}", meta[k] == actual, f"metadata={meta[k]} actual={actual}")
    return None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(k for r in rows for k in r)) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in r.items()})


# =============================================================================== 2. shared context
@dataclass
class Ctx:
    """Everything the evaluators need to cross-reference, as of one point in time of the run."""
    goal: str = ""
    company: str = ""
    competitors: list = field(default_factory=list)       # names
    task_goals: dict = field(default_factory=dict)        # worker name -> its task goal
    evidence: dict = field(default_factory=dict)          # evidence_id -> record (all workers, all attempts)
    worker_findings: dict = field(default_factory=dict)   # finding_id -> info (latest attempt of each worker)
    verified_findings: dict = field(default_factory=dict) # finding_id -> info (latest verification pass)
    aggregate: dict = field(default_factory=dict)         # finding_id -> worker-computed numbers (social/trend workers)
    signals: dict = field(default_factory=dict)           # signal_id -> {text, support, ...}
    valid_signal_ids: set = field(default_factory=set)
    decision_questions: list = field(default_factory=list)
    worker_attempt: dict = field(default_factory=dict)    # worker -> attempt used
    verification_file: str = ""
    cross_signal_file: str = ""
    telemetry_calls: list[dict[str, Any]] = field(default_factory=list)
    run_manifest: dict[str, Any] = field(default_factory=dict)
    ref_time: datetime | None = None

    @property
    def ref_year(self) -> int | None:
        return self.ref_time.year if self.ref_time else None

    def finding(self, fid: str) -> dict | None:
        return self.verified_findings.get(fid) or self.worker_findings.get(fid)

    def signal_texts(self, ids, limit: int = 12) -> list[str]:
        """What a recommendation may rely on: the signals, their implications and the findings behind them."""
        out: list[str] = []
        for sid in ids:
            s = self.signals.get(sid)
            if not s:
                continue
            out.append(s["text"])
            out += s.get("implications", [])
            out += [f["statement"] for f in (self.finding(x) for x in s["support"]) if f]
        return list(dict.fromkeys(out))[:limit]


def _add_findings(store: dict, findings: Any, worker: str = "", overwrite: bool = True) -> None:
    for f in dict_items(findings):
        fid = f.get("finding_id")
        if fid and f.get("statement") and (overwrite or str(fid) not in store):
            store[str(fid)] = {"statement": f["statement"], "confidence": f.get("confidence"),
                               "worker": f.get("worker", worker), "evidence_ids": cited_ids(f),
                               "verification_status": str(f.get("verification_status", "")).lower()}


def _add_signals(ctx: Ctx, signals: Any) -> None:
    for s in dict_items(signals):
        if s.get("signal_id"):
            sid = str(s["signal_id"])
            support = [str(x.get("finding_id") if isinstance(x, dict) else x) for x in s.get("supporting_findings") or []]
            ctx.signals[sid] = {"support": support, "confidence": s.get("confidence"),
                                "implications": [str(x) for x in s.get("implications") or []],
                                "text": " ".join(x for x in (s.get("title"), s.get("signal"), s.get("interpretation")) if x)}
            ctx.valid_signal_ids.add(sid)


def _aggregate_texts(ctx: Ctx, d: dict[str, Any]) -> None:
    """Social / trend workers compute their numbers (shares, % change) outside the evidence list.
    Those numbers are legitimate grounding for the finding that reports them."""
    meta = d.get("metadata") or {}
    control = meta.get("control_summary") or {}
    fm = meta.get("finding_metrics") or {}
    if not (fm or control):
        return
    shared = " ".join(x for x in (control.get("headline"), json.dumps(control.get("coverage") or {}, ensure_ascii=False)) if x)
    for f in dict_items(d.get("findings")):
        fid = str(f.get("finding_id"))
        vals = fm.get(fid) or {}
        own = json.dumps(vals, ensure_ascii=False)
        pcts = " ".join(f"{abs(v)}%" for v in vals.values() if is_num(v))      # '81.9' is the same fact as '81.9%'
        ctx.aggregate[fid] = (f"Metrics computed by the worker for this finding: {own} ({pcts}). "
                              f"Run summary: {shared}")[:MAX_CTX_CHARS]


def build_ctx(loaded: list[tuple[Path, str, dict]], task_override: str = "", as_of: datetime | None = None) -> Ctx:
    """Context as it was at `as_of` (None = the final state). Later re-runs only replace earlier ones from then on."""
    ctx = Ctx()
    for path, kind, data in loaded:
        if kind == "telemetry":
            ctx.telemetry_calls.extend(_execution_calls(data)[0])
        for manifest_path in (path.parent / "manifest.json", path.parent.parent / "manifest.json"):
            if manifest_path.is_file():
                try:
                    ctx.run_manifest = read_json(manifest_path)
                except (OSError, ValueError):
                    pass
                if ctx.run_manifest:
                    break
    eligible = lambda kind, d: as_of is None or file_time(kind, d) is None or file_time(kind, d) <= as_of
    when = lambda kind, d: file_time(kind, d) or EPOCH

    # run-wide facts, independent of time
    times = [t for _, k, d in loaded for t in [file_time(k, d)] if t]
    ctx.ref_time = as_of or (max(times) if times else None)
    for _, kind, d in loaded:
        if kind == "input":
            ctx.goal = d.get("research_goal", "")
            ctx.company = d.get("company", "")
            ctx.competitors = [comp_name(c) for c in d.get("competitors") or []]
    for _, kind, d in loaded:
        if kind == "worker":
            meta = d.get("metadata") or {}
            ctx.company = ctx.company or str(meta.get("company") or "")
            if not ctx.competitors:
                ctx.competitors = [c for c in map(comp_name, meta.get("competitors") or []) if c]
        if not ctx.goal and kind in ("decision", "cross_signal"):
            ctx.goal = d.get("business_goal") or (d.get("decision_ready_context") or {}).get("business_goal") or ""

    # workers: evidence of every attempt so far, findings of the latest attempt only
    by_worker: dict[str, list[dict]] = defaultdict(list)
    for _, kind, d in loaded:
        if kind == "worker" and eligible(kind, d):
            by_worker[d["worker"]].append(d)
    for name, versions in by_worker.items():
        versions.sort(key=lambda d: (int(d.get("attempt") or 1), when("worker", d)))
        for d in versions:
            ctx.evidence.update(evidence_map(d.get("evidence")))
        latest = versions[-1]
        _add_findings(ctx.worker_findings, latest.get("findings"), name)
        _aggregate_texts(ctx, latest)
        ctx.worker_attempt[name] = int(latest.get("attempt") or 1)
        if (latest.get("metadata") or {}).get("research_goal"):
            ctx.task_goals[name] = latest["metadata"]["research_goal"]

    def latest(kind: str):
        items = [(p, d) for p, k, d in loaded if k == kind and eligible(k, d)]
        return max(items, key=lambda x: when(kind, x[1])) if items else (None, None)

    p, d = latest("verification")
    if d:
        _add_findings(ctx.verified_findings, d.get("findings"))
        ctx.verification_file = p.name
    p, d = latest("cross_signal")
    if d:
        _add_signals(ctx, d.get("signals"))
        ctx.cross_signal_file = p.name
        ctx.decision_questions = [str(q) for q in (d.get("decision_ready_context") or {}).get("decision_questions") or []]
    p, d = latest("decision")
    if d:
        ctx.valid_signal_ids.update(map(str, (d.get("metadata") or {}).get("valid_cross_signal_ids") or []))

    for _, kind, d in loaded:   # container file: only a fallback source of data
        if kind == "orchestration":
            for t in dict_items((d.get("plan") or {}).get("tasks")):
                if t.get("worker") and t.get("goal"):
                    ctx.task_goals.setdefault(t["worker"], t["goal"])
            for res in dict_items(d.get("results")):
                for k, e in evidence_map(res.get("evidence")).items():
                    ctx.evidence.setdefault(k, e)
                _add_findings(ctx.worker_findings, res.get("findings"), res.get("worker", ""), overwrite=False)
            if not ctx.signals:
                _add_signals(ctx, (d.get("cross_signal") or {}).get("signals"))
    if task_override:
        ctx.goal = task_override
    return ctx


# =============================================================================== 3. LLM judge (DeepEval, cached)
class Cfg:
    """Run settings. `for_agent(kind)` returns a copy holding the metrics and GEval criteria that apply to that agent."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.deep, self.ctx_rel = args.deep, args.context_relevancy
        self.extra = getattr(args, "extra_metrics", not getattr(args, "no_extra_metrics", False))
        self.include_tool_payloads = getattr(args, "include_tool_payloads", False)
        self.thr = {"faithfulness": args.faithfulness_threshold, "relevancy": args.relevancy_threshold,
                    "context_relevancy": args.context_relevancy_threshold, "specificity": args.specificity_threshold,
                    "context_precision": args.context_relevancy_threshold,
                    "context_recall": args.context_relevancy_threshold,
                    "insight": args.insight_threshold, "actionability": args.actionability_threshold}
        self.metrics: list[str] = []
        self.geval: dict[str, tuple] = dict(GEVAL)

    def for_agent(self, kind: str, worker_type: str | None = None) -> "Cfg":
        c = copy.copy(self)
        base = WORKER_METRICS.get(worker_type or "", AGENT_METRICS[kind]) if kind == "worker" else AGENT_METRICS[kind]
        ms = [m for m in base if self.extra or m not in CUSTOM]
        if kind == "worker" and self.ctx_rel:
            ms.append("context_relevancy")
        c.metrics = ms
        c.geval = {
            **GEVAL,
            **GEVAL_BY_AGENT.get(kind, {}),
            **GEVAL_BY_AGENT.get(worker_type or "", {}),
        }
        return c


class Judge:
    """Thin wrapper around DeepEval metrics. Results are cached on disk by content hash, so reruns are free."""

    CACHE_FLUSH_EVERY = 25

    def __init__(self, model: str, cache_path: Path, enabled: bool, retries: int = 2, workers: int = 1) -> None:
        self.model, self.cache_path, self.enabled, self.retries = model, cache_path, enabled, retries
        self.workers = max(1, workers)
        self.cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self.calls = self.successes = self.hits = self.errors = 0
        self.skipped = 0
        self.total_latency_seconds = 0.0
        self.metric_calls: Counter[str] = Counter()
        self.metric_failures: Counter[str] = Counter()
        self.metric_skips: Counter[str] = Counter()
        self.next_allowed_at = 0.0
        self.rate_limit_open = False
        self._lock = threading.RLock()
        self._unsaved = 0

    def flush(self) -> None:
        """Write the cache to disk (atomically). Called every CACHE_FLUSH_EVERY new results and at the end of a run."""
        with self._lock, _CACHE_FLUSH_LOCK:
            if not self._unsaved:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.cache_path.parent,
                    prefix=f".{self.cache_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    json.dump(self.cache, tmp, ensure_ascii=False, indent=1)
                os.replace(tmp_path, self.cache_path)
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
            self._unsaved = 0

    @staticmethod
    def _root_exception(exc: Exception) -> Exception:
        current = exc
        seen: set[int] = set()
        while id(current) not in seen:
            seen.add(id(current))
            last_attempt = getattr(current, "last_attempt", None)
            nested = None
            if last_attempt is not None:
                try:
                    nested = last_attempt.exception()
                except Exception:
                    nested = None
            nested = nested if isinstance(nested, Exception) else current.__cause__ or current.__context__
            if not isinstance(nested, Exception) or nested is current:
                break
            current = nested
        return current

    @classmethod
    def _retry_after(cls, exc: Exception) -> float | None:
        root = cls._root_exception(exc)
        response = getattr(root, "response", None)
        headers = getattr(root, "headers", None) or getattr(response, "headers", None)
        if not headers:
            return None

        retry_ms = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
        if retry_ms is not None:
            try:
                return max(0.0, float(retry_ms) / 1000)
            except (TypeError, ValueError):
                pass

        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is None:
            return None
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            try:
                retry_at = email.utils.parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _wait_for_cooldown(self) -> None:
        with self._lock:
            until = self.next_allowed_at
        delay = until - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        with self._lock:
            if self.next_allowed_at == until:   # a newer cooldown set by another thread stays in force
                self.next_allowed_at = 0.0

    @classmethod
    def _is_rate_limit(cls, exc: Exception) -> bool:
        root = cls._root_exception(exc)
        return (
            "ratelimit" in type(root).__name__.replace("_", "").casefold()
            or getattr(root, "status_code", None) == 429
            or getattr(getattr(root, "response", None), "status_code", None) == 429
        )

    def run(self, metric: str, question: str, output: str, ctx: list[str], threshold: float,
            spec: tuple | None = None, expected_output: str | None = None) -> dict[str, Any]:
        """`spec` = (criteria, uses_context) for GEval metrics; it lets each agent have its own criteria."""
        if not self.enabled:
            return {"score": None, "reason": "", "error": "dry-run"}
        spec = spec or GEVAL.get(metric)
        needs_ctx = spec[1] if spec else METRICS[metric][1]
        if needs_ctx and not ctx:
            return {"score": None, "reason": "", "error": "skipped: no context text"}
        if metric in {"context_precision", "context_recall"} and not (expected_output or "").strip():
            return {"score": None, "reason": "", "error": "skipped: expected output is required"}
        if not (output or "").strip():
            return {"score": None, "reason": "", "error": "skipped: empty output"}
        key = hashlib.sha256(json.dumps([metric, self.model, question, output, ctx, spec, expected_output,
                                         threshold],
                                        ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            if key in self.cache:
                self.hits += 1
                return self.cache[key]
        last = ""
        started_total = time.perf_counter()
        for attempt in range(max(1, self.retries)):
            with self._lock:
                if self.rate_limit_open:
                    self.skipped += 1
                    self.metric_skips[metric] += 1
                    self.total_latency_seconds += time.perf_counter() - started_total
                    return {"score": None, "reason": "", "error": "skipped: judge rate-limit circuit is open"}
            self._wait_for_cooldown()
            with self._lock:
                self.calls += 1
                self.metric_calls[metric] += 1
            try:
                res = self._measure(metric, question, output, ctx, threshold, spec, expected_output)
            except Exception as exc:  # keep going; the failure is recorded in the report
                root = self._root_exception(exc)
                last = f"{type(root).__name__}: {root}"
                rate_limited = self._is_rate_limit(exc)
                if attempt + 1 < max(1, self.retries):
                    delay = max(2 ** (attempt + 1), self._retry_after(exc) or 0.0)
                    with self._lock:
                        self.next_allowed_at = max(self.next_allowed_at, time.monotonic() + delay)
                elif rate_limited:
                    with self._lock:
                        self.rate_limit_open = True
                continue
            with self._lock:
                self.successes += 1
                self.total_latency_seconds += time.perf_counter() - started_total
                self.cache[key] = res
                self._unsaved += 1
                if self._unsaved >= self.CACHE_FLUSH_EVERY:
                    self.flush()
            return res
        with self._lock:
            self.errors += 1
            self.metric_failures[metric] += 1
            self.total_latency_seconds += time.perf_counter() - started_total
        return {"score": None, "reason": "", "error": last}

    def _measure(self, metric: str, question: str, output: str, ctx: list[str], threshold: float,
                 spec: tuple | None = None, expected_output: str | None = None) -> dict[str, Any]:
        import deepeval.metrics as dm
        from deepeval.test_case import LLMTestCase, SingleTurnParams
        case_args: dict[str, Any] = {
            "input": question or "Evaluate this output.",
            "actual_output": output,
            "retrieval_context": ctx or None,
        }
        if expected_output:
            case_args["expected_output"] = expected_output
        case = LLMTestCase(**case_args)
        if spec:
            criteria, uses_ctx = spec
            params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
            if uses_ctx:
                params.append(SingleTurnParams.RETRIEVAL_CONTEXT)
            m = dm.GEval(name=metric, criteria=criteria, evaluation_params=params, model=self.model,
                         threshold=threshold, async_mode=False)
        else:
            m = getattr(dm, METRICS[metric][0])(threshold=threshold, model=self.model, include_reason=True,
                                                async_mode=False)
        m.measure(case)
        return {"score": round(float(m.score), 4), "reason": str(m.reason or ""), "error": ""}


def judge_unit(judge: Judge, cfg: Any, question: str, output: str, ctx: list[str], faith: bool = True,
               ctx_rel: bool = False, only: tuple[str, ...] | None = None, outputs: dict[str, str] | None = None,
               skip_note: str = "") -> dict[str, Any]:
    """Score one unit and return flat columns: <metric>_score / _pass / _reason / _note.
    only      restrict to some metrics (e.g. profile overviews are not 'specific' findings)
    outputs   a different text for a given metric (e.g. the full recommendation for actionability)
    skip_note do not judge at all (the unit is only checked deterministically)."""
    cols: dict[str, Any] = {}
    geval = getattr(cfg, "geval", GEVAL)
    to_run: list[str] = []
    for m in cfg.metrics:
        applies = (only is None or m in only) and (m in ("relevancy", *CUSTOM) or (m == "faithfulness" and faith)
                                                   or (m == "context_relevancy" and ctx_rel))
        if skip_note or not applies:
            cols[m] = {"score": None, "reason": "", "error": skip_note or "not applicable"}
        else:
            to_run.append(m)

    def call(m: str) -> dict[str, Any]:
        return judge.run(m, question, (outputs or {}).get(m, output), ctx, cfg.thr[m], spec=geval.get(m))

    workers = min(getattr(judge, "workers", 1), len(to_run))
    if workers > 1:   # the metrics of one unit are independent LLM calls
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(call, to_run))
    else:
        results = [call(m) for m in to_run]
    cols.update(dict(zip(to_run, results)))
    flat: dict[str, Any] = {}
    for m in cfg.metrics:   # keep the original column order
        r = cols[m]
        flat.update({f"{m}_score": r["score"], f"{m}_pass": None if r["score"] is None else r["score"] >= cfg.thr[m],
                     f"{m}_reason": r["reason"], f"{m}_note": r["error"]})
    return flat


def base_flags(row: dict[str, Any], n_ctx: int, cfg: Any) -> list[str]:
    """Flags every unit type shares."""
    fl = []
    if n_ctx == 0:
        fl.append("no_context_text")
    if row.get("refs") and row.get("refs_resolved", 0) < row["refs"]:
        fl.append("unresolved_refs")
    if row.get("refs") == 0:
        fl.append("no_refs")
    if row.get("ungrounded_numbers"):
        fl.append("ungrounded_numbers")
    fl += [METRICS[m][2] for m in cfg.metrics if row.get(f"{m}_pass") is False]
    return fl


def llm_stats(rows: list[dict[str, Any]], cfg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"units": len(rows), "units_flagged": sum(bool(r.get("flags")) for r in rows)}
    judge_errors = 0
    for m in cfg.metrics:
        scores = [r[f"{m}_score"] for r in rows if r.get(f"{m}_score") is not None]
        passes = [r[f"{m}_pass"] for r in rows if r.get(f"{m}_pass") is not None]
        out.update({f"{m}_tested": len(passes), f"{m}_avg": mean(scores), f"{m}_pass_pct": pct(sum(passes), len(passes))})
        judge_errors += sum(1 for r in rows if (r.get(f"{m}_note") or "").strip()
                            and not str(r[f"{m}_note"]).startswith(("skipped", "dry-run", "n/a", "not applicable")))
    out["judge_errors"] = judge_errors
    return out


# =============================================================================== 4. per-agent evaluators
# Each evaluator receives (path, data, ctx, judge, cfg) and returns {"rows", "metrics", "checks", "metrics_used"}.
EVALUATORS: dict[str, Callable] = {}


def evaluator(kind: str):
    def deco(fn):
        EVALUATORS[kind] = fn
        return fn
    return deco


def _result(rows, metrics, checks, cfg):
    return {"rows": rows, "metrics": metrics, "checks": [c for c in checks if c], "metrics_used": cfg.metrics}


# ------------------------------------------------------------------ any worker (internal, competitor, market, customer, ...)
@evaluator("worker")
def eval_worker(path, d, ctx, judge, cfg):
    worker = d["worker"]
    cfg = cfg.for_agent("worker", worker_type=worker)
    meta = d.get("metadata") or {}
    control = meta.get("control_summary") or {}
    goal = ctx.task_goals.get(worker) or meta.get("research_goal") or ctx.goal
    own = evidence_map(d.get("evidence"))
    findings = dict_items(d.get("findings"))
    competitors = [c for c in map(comp_name, meta.get("competitors") or ctx.competitors) if c]
    profiles = meta.get("competitor_profiles") or {}
    competitor_style = "competitor" in worker.lower()
    internal = "internal" in worker.lower()                            # internal workers may use synthetic evidence
    attempt = int(d.get("attempt") or 1)
    rows, cited_all, by_stmt, bad_relations = [], set(), {}, 0
    fstmts_by_comp: dict[str, list[str]] = defaultdict(list)           # competitor -> its findings' statements
    fid_by_ev: dict[str, list[str]] = defaultdict(list)                # evidence id -> findings citing it

    # ---- findings
    for i, f in enumerate(findings, 1):
        fid = str(f.get("finding_id", ""))
        ids = cited_ids(f)
        cited_all.update(ids)
        recs = [r for r in (own.get(x) or ctx.evidence.get(x) for x in ids) if r]
        texts = [t for t in (ev_text(r) for r in recs) if t][:MAX_CTX_ITEMS]
        judge_ctx = texts + ([ctx.aggregate[fid]] if fid in ctx.aggregate else [])   # worker-computed numbers count
        statement = f.get("statement", "")
        by_stmt[norm(statement)] = f
        for x in ids:
            fid_by_ev[x].append(fid)
        for r in recs:
            cn = ev_meta(r).get("competitor")
            if cn:
                fstmts_by_comp[cn].append(statement)
        relations = [r.get("relation") for r in dict_items(f.get("evidence_refs"))]
        other_rel = sum(x not in (None, "supports") for x in relations)
        bad_relations += other_rel
        doms = [domain(r.get("source_url")) for r in recs]
        social = bool(recs) and all(ev_meta(r).get("kind") in ("social_post", "social_comment") for r in recs)
        first_party = bool(recs) and all(ev_meta(r).get("first_party") is True for r in recs)   # a company's own site
        row = {
            "unit_type": "finding", "unit_index": i, "unit_id": fid, "attempt": attempt,
            "category": f.get("category", ""), "statement": statement, "confidence": f.get("confidence", ""),
            "refs": len(ids), "refs_resolved": len(recs), "synthetic_refs": sum(is_synthetic(r) for r in recs),
            "distinct_source_domains": len({x for x in doms if x}),
            "third_party_refs": sum(ev_meta(r).get("first_party") is False for r in recs),
            "avg_retrieval_score": mean([retrieval_score(r) for r in recs]),
            "non_supporting_relations": other_rel, "limitations_count": len(f.get("limitations") or []),
            "ungrounded_numbers": ungrounded_numbers(statement, judge_ctx),
        }
        # Judge each finding against ITS OWN question (competitor + category), not the whole multi-requirement goal.
        comp = next((ev_meta(r).get("competitor") for r in recs if ev_meta(r).get("competitor")), "")
        if not comp:
            comp = next((c for c in competitors if c.lower() in statement.lower()), "")
        worker_topic = {
            "internal_intelligence": "internal capabilities, constraints, and decisions",
            "market_intelligence": "market, economic, industry, or regulatory conditions",
            "customer_trends": "customer needs, sentiment, and behavior",
        }.get(worker, f"competitor {comp or 'positioning'}" if competitor_style else "research findings")
        f_question = (
            f"{f.get('category') or 'Overview'} concerning {worker_topic}. "
            f"Overall research goal: {goal}"
        )
        row.update(judge_unit(judge, cfg, f_question, statement, judge_ctx, ctx_rel=True))
        flags = base_flags(row, len(judge_ctx), cfg)
        if row["refs_resolved"] and row["synthetic_refs"] == row["refs_resolved"]:
            flags.append("synthetic_evidence_only")
            if row["limitations_count"] == 0:
                flags.append("synthetic_without_limitation_note")
        if len(ids) >= 2 and row["distinct_source_domains"] == 1 and all(doms) and not internal and not social and not first_party:
            flags.append("single_domain_support")
        if other_rel:
            flags.append("non_supporting_relation")
        if competitor_style and competitors and not any(c.lower() in statement.lower() for c in competitors):
            flags.append("competitor_not_named")
        if absence_claim(statement):
            flags.append("absence_claim")
        row["flags"] = flags
        rows.append(row)

    # ---- requirement checks (competitor-type workers): the answer to a requirement is the profile summary,
    #      its evidence is whatever the worker tagged with that competitor + requirement id
    reqs = dict_items(meta.get("requirement_checks"))
    prof_req = {(cn, r.get("requirement_id")): r for cn, p in profiles.items() if isinstance(p, dict)
                for r in dict_items(p.get("requirements"))}
    ev_by_req: dict[tuple, list[str]] = defaultdict(list)
    for e in own.values():
        m = ev_meta(e)
        if m.get("requirement_id"):
            ev_by_req[(m.get("competitor"), m["requirement_id"])].append(str(e["evidence_id"]))
    fnd_by_id = {r["unit_id"]: r for r in rows}
    unlinked = count_mismatch = 0
    for i, r in enumerate(reqs, 1):
        status, key = str(r.get("status", "")).lower(), (r.get("competitor"), r.get("requirement_id"))
        ev_ids = ev_by_req.get(key, [])
        linked = sorted({fid for x in ev_ids for fid in fid_by_ev.get(x, [])})
        answer = (prof_req.get(key) or {}).get("summary") or " ".join(
            fnd_by_id[x]["statement"] for x in linked if x in fnd_by_id)
        ev_texts = [t for t in (ev_text(own[x]) for x in ev_ids if x in own) if t][:MAX_CTX_ITEMS]
        claimed = re.search(r"(\d+)\s+grounded finding", str(r.get("evidence_found", "")))
        row = {"unit_type": "requirement", "unit_index": i, "unit_id": r.get("requirement_id", ""),
               "attempt": attempt, "category": status, "competitor": r.get("competitor", ""),
               "requirement": r.get("requirement", ""), "statement": answer, "confidence": r.get("confidence"),
               "evidence_quality": r.get("evidence_quality", ""), "refs": 0, "refs_resolved": 0,
               "linked_findings": linked, "linked_evidence": len(ev_ids),
               "ungrounded_numbers": ungrounded_numbers(answer, ev_texts) if status == "fulfilled" else []}
        if status == "fulfilled" and answer:   # is the answer supported by its evidence, and does it address the requirement?
            row.update(judge_unit(judge, cfg, r.get("requirement", ""), answer, ev_texts, only=("faithfulness", "relevancy")))
        else:
            row.update(judge_unit(judge, cfg, "", "", [], skip_note="n/a (not fulfilled)"))
        flags = []
        if status == "fulfilled" and not linked:
            flags.append("fulfilled_without_findings")
            unlinked += 1
        if status == "fulfilled" and not answer:
            flags.append("fulfilled_without_answer")
        if status == "fulfilled" and claimed and int(claimed.group(1)) > len(linked):
            flags.append("grounded_count_mismatch")
            count_mismatch += 1
        if status != "fulfilled" and not r.get("missing_reason"):
            flags.append("missing_without_reason")
        if status != "fulfilled" and linked:
            flags.append("missing_despite_grounded_finding")
        if row.get("ungrounded_numbers"):
            flags.append("ungrounded_numbers")
        if status == "fulfilled" and absence_claim(answer):
            flags.append("absence_claim")
        flags += [METRICS[m][2] for m in cfg.metrics if row.get(f"{m}_pass") is False]
        row["flags"] = flags
        rows.append(row)

    # ---- competitor profile overviews, judged against the findings about that competitor
    all_stmts = [f.get("statement", "") for f in findings if f.get("statement")]
    for name, prof in profiles.items():
        prof = prof if isinstance(prof, dict) else {}
        overview = prof.get("overview", "")
        stmts = list(dict.fromkeys(fstmts_by_comp.get(name) or [s for s in all_stmts if name.lower() in s.lower()]
                                   or all_stmts))[:MAX_CTX_ITEMS * 2]
        row = {"unit_type": "profile", "unit_index": 0, "unit_id": name, "attempt": attempt, "category": "overview",
               "statement": overview, "refs": 0, "refs_resolved": 0,
               "verified_findings_count": prof.get("verified_findings_count"),
               "ungrounded_numbers": ungrounded_numbers(overview, stmts)}
        row.update(judge_unit(judge, cfg, f"Overview of {name} relevant to: {goal}", overview, stmts,
                              only=("faithfulness", "relevancy")))
        row["flags"] = [x for x in base_flags(row, len(stmts), cfg) if x != "no_refs"]
        if absence_claim(overview):
            row["flags"].append("absence_claim")
        rows.append(row)

    fnd = [r for r in rows if r["unit_type"] == "finding"]
    cited_recs = [own[x] for x in cited_all if x in own]
    dom = Counter(x for x in (domain(r.get("source_url")) for r in cited_recs) if x)
    fulfilled = sum(str(r.get("status", "")).lower() == "fulfilled" for r in reqs)
    synth_only = [r for r in fnd if "synthetic_evidence_only" in r["flags"]]
    collected = control.get("evidence_count") or meta.get("evidence_count") or meta.get("sources_read")
    fconfs = [conf_num(r["confidence"]) for r in fnd]
    overall = conf_num(d.get("confidence"))
    n_absence = sum("absence_claim" in r["flags"] for r in rows)
    metrics = {
        "attempt": attempt, "status": d.get("status"), "error": d.get("error"), "findings": len(fnd),
        "evidence_in_file": len(own), "evidence_collected": collected,
        "evidence_sample_pct": pct(len(own), collected) if is_num(collected) and collected >= len(own) else None,
        "evidence_cited": len(cited_all & set(own)), "evidence_utilization_pct": pct(len(cited_all & set(own)), len(own)),
        "synthetic_evidence_pct": pct(sum(is_synthetic(e) for e in own.values()), len(own)),
        "findings_synthetic_only_pct": pct(len(synth_only), len(fnd)),
        "distinct_source_domains_cited": len(dom),
        "top_domain_share_pct": pct(dom.most_common(1)[0][1], sum(dom.values())) if dom else None,
        "third_party_evidence_pct": pct(sum(ev_meta(e).get("first_party") is False for e in own.values()), len(own)),
        "avg_retrieval_score": mean([retrieval_score(e) for e in own.values()]),
        "requirements_total": len(reqs), "requirement_coverage_pct": pct(fulfilled, len(reqs)),
        "profiles": len(profiles),
        "gaps": len(d.get("gaps") or []), "conflicts": len(d.get("conflicts") or []),
        "queries_executed": meta.get("queries_executed") or meta.get("query_count"),
        "agent_confidence": overall, "avg_finding_confidence": mean(fconfs),
        "findings_with_numbers_not_in_evidence": sum(bool(r["ungrounded_numbers"]) for r in fnd),
        "units_with_absence_claims": n_absence,
        **llm_stats(rows, cfg),
    }
    discarded = {str(x.get("evidence_id")) for x in dict_items(meta.get("discarded_evidence"))}
    gap = abs(overall - metrics["avg_finding_confidence"]) if overall is not None and metrics["avg_finding_confidence"] is not None else None
    checks = [
        count_check("findings", meta, ("finding_count", "total_verified_findings"), len(findings)),
        count_check("evidence", meta, ("evidence_count", "total_sources"), len(own)),
        check("control_summary.findings_count matches findings", control["findings_count"] == len(findings),
              f"summary={control['findings_count']} actual={len(findings)}") if "findings_count" in control else None,
        check("every cited evidence id resolves", all(r["refs_resolved"] == r["refs"] for r in fnd)),
        check("every finding is grounded (has refs)", all(r["refs"] > 0 for r in fnd)),
        check("synthetic-only findings carry a limitation note",
              not any("synthetic_without_limitation_note" in r["flags"] for r in fnd)),
        check("all evidence relations are 'supports'", bad_relations == 0, f"{bad_relations} other"),
        check("no discarded evidence is cited", not (discarded & cited_all), ", ".join(sorted(discarded & cited_all)))
        if discarded else None,
        check("no synthetic evidence in an external-research agent", not any(is_synthetic(e) for e in own.values()))
        if not internal else None,
        check("every fulfilled requirement maps to at least one finding", unlinked == 0, f"{unlinked} unlinked") if reqs else None,
        check("fulfilled requirements: every claimed 'N grounded findings' is backed", count_mismatch == 0,
              f"{count_mismatch} mismatches", "warn") if reqs else None,
        check("every missing requirement states a reason",
              not any("missing_without_reason" in r["flags"] for r in rows)) if reqs else None,
        check("no requirement is 'missing' while a grounded finding exists",
              not any("missing_despite_grounded_finding" in r["flags"] for r in rows), severity="warn") if reqs else None,
        check("every named competitor has a profile", all(c in profiles for c in competitors), ", ".join(competitors))
        if competitor_style and competitors else None,
        check("agent confidence is close to its findings' average (<=0.15)", gap <= 0.15, f"gap={round(gap, 3)}", "warn")
        if gap is not None else None,
        check("findings do not contain numbers absent from their evidence", metrics["findings_with_numbers_not_in_evidence"] == 0,
              f"{metrics['findings_with_numbers_not_in_evidence']} findings", "warn"),
        check("no unit states an absence of evidence as a fact ('not mentioned')", n_absence == 0,
              f"{n_absence} units", "warn"),
        check("run completed (not partial/failed)", d.get("status") in (None, "completed"), str(d.get("status")), "warn"),
        check("run reported no error", not d.get("error"), str(d.get("error")), "warn"),
    ]
    return _result(rows, metrics, checks, cfg)


@evaluator("metrics")
def eval_metrics(path, d, ctx, judge, cfg):
    """Check metric observations and required-metric outcomes against their saved evidence."""
    results = dict_items(d.get("results"))
    rows = []
    observation_count = verified_count = thin_count = conflict_count = unverified_count = 0
    requirement_count = required_count = found_count = gap_count = 0
    missing_outcomes = duplicate_outcomes = unresolved_evidence = 0
    nonterminal_outcomes = orphan_outcomes = required_terminal_outcomes = 0
    invalid_claims = missing_gap_reasons = found_without_observations = 0
    captured_metric_data = False

    for result in results:
        worker = str(result.get("worker") or "")
        metadata = result.get("metadata") or {}
        evidence = evidence_map(result.get("evidence"))
        observations = dict_items(metadata.get("metrics"))
        requirements = dict_items(metadata.get("requirements"))
        outcomes = dict_items(metadata.get("requirement_outcomes"))
        captured_metric_data |= any(
            key in metadata for key in ("metrics", "requirements", "requirement_outcomes")
        )
        outcome_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        observations_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

        for outcome in outcomes:
            key = (str(outcome.get("metric_id") or ""), str(outcome.get("entity") or "").casefold())
            outcome_by_key[key].append(outcome)

        for observation in observations:
            observation_count += 1
            metric_id = str(observation.get("metric_id") or "")
            entity = str(observation.get("entity") or "")
            key = (metric_id, entity.casefold())
            observations_by_key[key].append(observation)
            status = str(observation.get("status") or "").lower()
            value_text = str(observation.get("value_text") or "")
            source_id = str(observation.get("evidence_id") or "")
            source = evidence.get(source_id)
            source_text = "\n".join(
                str(source.get(field) or "")
                for field in ("extracted_fact", "excerpt", "page_text", "text")
            ) if source else ""
            source_span = str(observation.get("source_span") or "")
            normalized_source = re.sub(r"\s+", " ", source_text).strip().casefold()
            normalized_span = re.sub(r"\s+", " ", source_span).strip().casefold()
            span_resolves = bool(normalized_span and normalized_span in normalized_source)
            number_grounded = bool(NUM_RE.findall(value_text)) and not ungrounded_numbers(value_text, [source_text])
            flags = []
            if not metric_id:
                flags.append("missing_metric_id")
            if not entity.strip():
                flags.append("missing_metric_entity")
            if not source:
                flags.append("unresolved_metric_evidence")
                unresolved_evidence += 1
            if not span_resolves:
                flags.append("source_span_not_in_evidence")
            if status != "unverified" and not number_grounded:
                flags.append("metric_value_not_in_evidence")
            if status not in {"verified", "thin", "conflict", "unverified"}:
                flags.append("invalid_metric_status")
            if status != "unverified" and not is_finite_num(observation.get("value")):
                flags.append("invalid_numeric_value")
            if flags and status != "unverified":
                invalid_claims += 1
            if status == "verified":
                verified_count += 1
            elif status == "thin":
                thin_count += 1
            elif status == "conflict":
                conflict_count += 1
            elif status == "unverified":
                unverified_count += 1
            rows.append({
                "unit_type": "metric_observation",
                "unit_index": observation_count,
                "unit_id": f"{worker}:{metric_id}:{entity}:{observation.get('evidence_id') or ''}",
                "worker": worker,
                "metric_id": metric_id,
                "entity": entity,
                "value_text": value_text,
                "value": observation.get("value"),
                "status": status,
                "evidence_id": source_id,
                "flags": flags,
            })

        seen_required: set[tuple[str, str]] = set()
        for requirement in requirements:
            requirement_count += 1
            metric_id = str(requirement.get("metric_id") or "")
            entity = str(requirement.get("entity") or "")
            key = (metric_id, entity.casefold())
            if requirement.get("required", True):
                required_count += 1
                seen_required.add(key)
            matches = outcome_by_key.get(key, [])
            flags = []
            if requirement.get("required", True) and not matches:
                flags.append("required_outcome_missing")
                missing_outcomes += 1
            if len(matches) > 1:
                flags.append("duplicate_requirement_outcomes")
                duplicate_outcomes += len(matches) - 1
            outcome = matches[0] if matches else {}
            outcome_status = str(outcome.get("status") or "").lower()
            if outcome_status == "found":
                found_count += 1
                if requirement.get("required", True):
                    required_terminal_outcomes += 1
                supported = any(
                    str(item.get("status") or "").lower() in {"verified", "thin"}
                    for item in observations_by_key.get(key, [])
                )
                if not supported:
                    flags.append("found_without_metric_observation")
                    found_without_observations += 1
            elif outcome_status == "gap":
                gap_count += 1
                if requirement.get("required", True):
                    required_terminal_outcomes += 1
                if not outcome.get("reason"):
                    flags.append("gap_without_reason")
                    missing_gap_reasons += 1
            elif outcome_status:
                flags.append("nonterminal_requirement_outcome")
                nonterminal_outcomes += 1
            elif matches:
                flags.append("invalid_requirement_outcome")
                nonterminal_outcomes += 1
            rows.append({
                "unit_type": "metric_requirement",
                "unit_index": requirement_count,
                "unit_id": f"{worker}:{metric_id}:{entity}",
                "worker": worker,
                "metric_id": metric_id,
                "entity": entity,
                "required": requirement.get("required", True),
                "outcome": outcome_status or None,
                "gap_reason": outcome.get("reason"),
                "flags": flags,
            })

        for key, matches in outcome_by_key.items():
            if key not in seen_required and not any(
                (str(req.get("metric_id") or ""), str(req.get("entity") or "").casefold()) == key
                for req in requirements
            ):
                orphan_outcomes += len(matches)
                rows.append({
                    "unit_type": "metric_requirement_outcome",
                    "unit_index": len(rows) + 1,
                    "unit_id": f"{worker}:{key[0]}:{key[1]}",
                    "worker": worker,
                    "metric_id": key[0],
                    "entity": matches[0].get("entity"),
                    "outcome": matches[0].get("status"),
                    "flags": ["outcome_without_requirement"],
                })

    metrics = {
        "worker_results": len(results),
        "metric_observations": observation_count,
        "verified_observations": verified_count,
        "thin_observations": thin_count,
        "conflicting_observations": conflict_count,
        "unverified_observations": unverified_count,
        "requirements": requirement_count,
        "required_requirements": required_count,
        "found_outcomes": found_count,
        "gap_outcomes": gap_count,
        "required_outcome_coverage_pct": pct(required_terminal_outcomes, required_count),
        "unresolved_evidence_refs": unresolved_evidence,
        "invalid_grounded_claims": invalid_claims,
        "missing_gap_reasons": missing_gap_reasons,
        "found_without_observations": found_without_observations,
        "duplicate_outcomes": duplicate_outcomes,
        "nonterminal_outcomes": nonterminal_outcomes,
        "orphan_outcomes": orphan_outcomes,
    }
    checks = [
        check(
            "verified metric observations are grounded in cited sources",
            invalid_claims == 0,
            f"{invalid_claims} unsupported or malformed verified/thin/conflicting observation(s)",
        ),
        check(
            "every required metric has a terminal outcome",
            missing_outcomes == 0 and duplicate_outcomes == 0 and nonterminal_outcomes == 0,
            f"{missing_outcomes} missing, {duplicate_outcomes} duplicate, "
            f"and {nonterminal_outcomes} nonterminal outcome(s)",
        ),
        check(
            "metric outcomes map to declared requirements",
            orphan_outcomes == 0,
            f"{orphan_outcomes} outcome(s) without a matching requirement",
        ),
        check(
            "every metric gap states a reason",
            missing_gap_reasons == 0,
            f"{missing_gap_reasons} gap(s) without a reason",
        ),
        check(
            "found outcomes have supporting metric observations",
            found_without_observations == 0,
            f"{found_without_observations} unsupported found outcome(s)",
        ),
        check(
            "metrics-stage data was captured",
            captured_metric_data,
            "no worker result contains metric, requirement, or outcome metadata",
            "warn",
        ),
    ]
    return {
        "rows": rows,
        "metrics": metrics,
        "checks": checks,
        "metrics_used": ["metric_evidence_integrity", "metric_requirement_coverage"],
    }


# ------------------------------------------------------------------ verification agent
@evaluator("verification")
def eval_verification(path, d, ctx, judge, cfg):
    """The verifier is itself judged: its labels are compared with an independent faithfulness score."""
    cfg = cfg.for_agent("verification")
    POSITIVE = {"verified"}
    meta, dec, control = d.get("metadata") or {}, d.get("decision") or {}, d.get("control_summary") or {}
    findings = dict_items(d.get("findings"))
    rows, seen = [], []

    for i, f in enumerate(findings, 1):
        fid = str(f.get("finding_id", ""))
        ids = cited_ids(f)
        recs = [ctx.evidence[x] for x in ids if x in ctx.evidence]
        texts = [t for t in (ev_text(r) for r in recs) if t][:MAX_CTX_ITEMS]
        judge_ctx = texts + ([ctx.aggregate[fid]] if fid in ctx.aggregate else [])
        statement = f.get("statement", "")
        status = str(f.get("verification_status", "")).lower()
        quality = str(f.get("evidence_quality", "")).lower()
        conf = conf_num(f.get("confidence"))
        original = (ctx.worker_findings.get(fid) or {}).get("statement")
        dup_of = next((sid for sid, s in seen if near_dup(norm(statement), s)), "")
        seen.append((fid, norm(statement)))
        row = {
            "unit_type": "finding", "unit_index": i, "unit_id": fid, "worker": f.get("worker", ""),
            "statement": statement, "verification_status": status, "evidence_quality": quality,
            "confidence": f.get("confidence", ""), "contradictions": len(f.get("contradictions") or []),
            "missing_information_items": len(f.get("missing_information") or []),
            "refs": len(ids), "refs_resolved": len(recs), "synthetic_refs": sum(is_synthetic(r) for r in recs),
            "near_duplicate_of": dup_of, "rewritten_vs_worker": bool(original and norm(original) != norm(statement)),
            "ungrounded_numbers": ungrounded_numbers(statement, judge_ctx),
        }
        row.update(judge_unit(judge, cfg, ctx.goal, statement, judge_ctx))
        flags = base_flags(row, len(judge_ctx), cfg)
        verified = status in POSITIVE
        if verified and row["faithfulness_pass"] is False:
            flags.append("verified_but_unfaithful")
        if not verified and row["faithfulness_pass"] is True:
            flags.append("rejected_but_faithful")
        if verified and row["refs_resolved"] and row["synthetic_refs"] == row["refs_resolved"]:
            flags.append("verified_on_synthetic_only")
        if verified and (row["contradictions"] or row["missing_information_items"]):
            flags.append("verified_with_open_issues")
        if verified and quality == "low":
            flags.append("verified_on_low_quality_evidence")
        if conf is not None and ((verified and conf < 0.5) or (not verified and conf >= 0.7)):
            flags.append("confidence_contradicts_status")
        if dup_of:
            flags.append("near_duplicate")
        if row["rewritten_vs_worker"]:
            flags.append("statement_rewritten_by_verifier")
        row["flags"] = flags
        rows.append(row)

    tested = [r for r in rows if r.get("faithfulness_pass") is not None]
    ver = [r for r in tested if r["verification_status"] in POSITIVE]
    rej = [r for r in tested if r["verification_status"] not in POSITIVE]
    fp = sum(r["faithfulness_pass"] is False for r in ver)      # verifier said yes, judge says no
    fn = sum(r["faithfulness_pass"] is True for r in rej)       # verifier said no, judge says yes
    confs = [conf_num(r["confidence"]) for r in rows if conf_num(r["confidence"]) is not None]
    n_ver = sum(r["verification_status"] in POSITIVE for r in rows)
    n_not = len(rows) - n_ver
    conf_of = lambda sel: mean([conf_num(r["confidence"]) for r in rows if sel(r)])
    by_q = {q: conf_of(lambda r, q=q: r["evidence_quality"] == q) for q in ("high", "medium", "low")}
    with_issue = sum(bool(r["contradictions"]) for r in rows)
    metrics = {
        "decision_status": dec.get("status"), "targeted_actions": len(dec.get("targeted_actions") or []),
        "findings": len(rows), "verified": n_ver, "not_verified": n_not, "verified_pct": pct(n_ver, len(rows)),
        "false_verification_pct": pct(fp, len(ver)), "verified_but_unfaithful": fp,
        "missed_valid_pct": pct(fn, len(rej)), "rejected_but_faithful": fn,
        "confidence_distinct_values": len(set(confs)), "confidence_stdev": stdev(confs), "avg_confidence": mean(confs),
        "avg_confidence_verified": conf_of(lambda r: r["verification_status"] in POSITIVE),
        "avg_confidence_not_verified": conf_of(lambda r: r["verification_status"] not in POSITIVE),
        "avg_confidence_high_quality": by_q["high"], "avg_confidence_medium_quality": by_q["medium"],
        "avg_confidence_low_quality": by_q["low"],
        "verified_on_synthetic_only": sum("verified_on_synthetic_only" in r["flags"] for r in rows),
        "near_duplicate_findings": sum("near_duplicate" in r["flags"] for r in rows),
        "rewritten_vs_worker": sum(r["rewritten_vs_worker"] for r in rows),
        "findings_with_contradictions": with_issue,
        "control_gaps": len(control.get("missing_information") or []),
        "control_conflicts": len(control.get("conflicts") or []),
        "control_failures": len(control.get("failures") or []),
        **llm_stats(rows, cfg),
    }
    worker_ids, ver_ids = set(ctx.worker_findings), {r["unit_id"] for r in rows}
    summary = str(dec.get("summary") or "")
    m_sum = re.search(r"(\d+)\s+of\s+(\d+)", summary)
    expected = None
    if m_sum:
        expected = with_issue if "conflict" in summary.lower() else n_ver
    q_order = [by_q[q] for q in ("high", "medium", "low") if by_q[q] is not None]
    checks = [
        count_check("findings", meta, ("finding_count",), len(rows)),
        count_check("verified findings", meta, ("verified_count",), n_ver),
        count_check("insufficient findings", meta, ("insufficient_count",), n_not),
        check("replan_required comes with targeted actions; verified comes without",
              bool(dec.get("targeted_actions")) == (str(dec.get("status")).lower() == "replan_required"),
              f"status={dec.get('status')} targeted_actions={len(dec.get('targeted_actions') or [])}")
        if dec.get("status") is not None else None,
        check("decision.summary numbers match the findings", int(m_sum.group(2)) == len(rows) and int(m_sum.group(1)) == expected,
              f"summary='{summary[:70]}' findings={len(rows)} verified={n_ver} with_contradictions={with_issue}")
        if m_sum else None,
        check("every worker finding was verified", worker_ids <= ver_ids, f"{len(worker_ids - ver_ids)} missing")
        if worker_ids else None,
        check("no unknown finding ids", ver_ids <= worker_ids, f"{len(ver_ids - worker_ids)} ids not in worker output")
        if worker_ids else None,
        check("every cited evidence id resolves", all(r["refs_resolved"] == r["refs"] for r in rows)),
        check("verified findings have no open contradictions/gaps",
              not any("verified_with_open_issues" in r["flags"] for r in rows)),
        check("no finding is verified on low-quality evidence",
              not any("verified_on_low_quality_evidence" in r["flags"] for r in rows), severity="warn"),
        check("confidence discriminates between findings (>=3 distinct values)", len(set(confs)) >= 3,
              f"{len(set(confs))} distinct, stdev={stdev(confs)}", "warn") if len(rows) >= 3 else None,
        check("confidence is calibrated to the label (verified > not verified)",
              metrics["avg_confidence_verified"] > metrics["avg_confidence_not_verified"],
              f"verified={metrics['avg_confidence_verified']} not_verified={metrics['avg_confidence_not_verified']}", "warn")
        if n_ver and n_not and metrics["avg_confidence_verified"] is not None
        and metrics["avg_confidence_not_verified"] is not None else None,
        check("confidence follows evidence quality (high >= medium >= low)", q_order == sorted(q_order, reverse=True),
              f"{by_q}", "warn") if len(q_order) >= 2 else None,
        check("no finding's confidence contradicts its label", not any("confidence_contradicts_status" in r["flags"] for r in rows),
              severity="warn"),
        check("control conflicts match findings with contradictions", metrics["control_conflicts"] == with_issue,
              f"control={metrics['control_conflicts']} findings={with_issue}", "warn"),
        check("control gaps are not just a copy of next_research_targets",
              control.get("missing_information") != control.get("next_research_targets")
              or not control.get("missing_information")),
        check("no near-duplicate findings", metrics["near_duplicate_findings"] == 0,
              f"{metrics['near_duplicate_findings']} duplicates", "warn"),
    ]
    return _result(rows, metrics, checks, cfg)


# ------------------------------------------------------------------ cross-signal agent
def s_ids(s: dict) -> list[str]:
    return [str(x.get("finding_id") if isinstance(x, dict) else x) for x in s.get("supporting_findings") or []]


@evaluator("cross_signal")
def eval_cross_signal(path, d, ctx, judge, cfg):
    cfg = cfg.for_agent("cross_signal")
    stats = d.get("input_statistics") or {}
    signals = dict_items(d.get("signals"))
    entities = [e for e in ctx.competitors if e]      # the company itself is the implicit subject
    have_status = any(v.get("verification_status") for v in ctx.verified_findings.values())
    rows = []

    for i, s in enumerate(signals, 1):
        sup = list(s.get("supporting_findings") or [])
        ids = [str(x.get("finding_id") if isinstance(x, dict) else x) for x in sup]
        found = [(fid, ctx.finding(fid)) for fid in ids]
        found = [(fid, f) for fid, f in found if f]
        texts = [f["statement"] for _, f in found][:MAX_CTX_ITEMS]
        statement = s.get("signal", "")
        workers = {f.get("worker") for _, f in found if f.get("worker")}
        domains = {str(x) for x in s.get("domains_involved") or []}
        sup_conf = [conf_num(f.get("confidence")) for _, f in found if conf_num(f.get("confidence")) is not None]
        blob = " ".join(texts).lower()
        missing_ent = [e for e in entities if e.lower() in statement.lower() and e.lower() not in blob]
        unverified = [fid for fid, _ in found if have_status and ctx.verified_findings.get(fid, {}).get("verification_status") != "verified"]
        conf = conf_num(s.get("confidence"))
        long_text = " ".join(x for x in (s.get("title"), statement, "Interpretation: " + str(s.get("interpretation") or ""),
                                         "Implications: " + "; ".join(map(str, s.get("implications") or []))) if x)
        row = {
            "unit_type": "signal", "unit_index": i, "unit_id": s.get("signal_id", ""),
            "signal_type": s.get("signal_type", ""), "statement": statement, "confidence": s.get("confidence", ""),
            "evidence_quality": s.get("evidence_quality", ""), "urgency": s.get("urgency", ""),
            "refs": len(ids), "refs_resolved": len(found),
            "supporting_workers": sorted(workers), "domains_involved": sorted(domains),
            "domains_not_in_support": sorted(domains - workers) if workers else [],
            "max_support_confidence": max(sup_conf) if sup_conf else None,
            "unverified_support": unverified,
            "support_without_relevance_note": sum(1 for x in sup if not (isinstance(x, dict) and x.get("relevance"))),
            "implications": len(s.get("implications") or []), "evidence_gaps": len(s.get("evidence_gaps") or []),
            "entities_missing_from_support": missing_ent, "ungrounded_numbers": ungrounded_numbers(statement, texts),
        }
        row.update(judge_unit(judge, cfg, ctx.goal, statement, texts,
                              outputs={"insight": long_text}))
        flags = base_flags(row, len(texts), cfg)
        if len(ids) < 2:
            flags.append("thin_support")
        if str(s.get("signal_type")).lower() == "convergence" and len(workers) < 2:
            flags.append("convergence_from_single_worker")
        if missing_ent:
            flags.append("entity_not_in_support")
        if conf is not None and sup_conf and conf > max(sup_conf) + 1e-9:
            flags.append("confidence_exceeds_support")
        if unverified:
            flags.append("support_not_verified")
        if row["domains_not_in_support"]:
            flags.append("domain_not_in_support")
        if row["support_without_relevance_note"]:
            flags.append("support_without_relevance_note")
        row["flags"] = flags
        rows.append(row)

    unresolved = sum(r["refs"] - r["refs_resolved"] for r in rows)
    block = d.get("decision_ready_context") or {}
    types = Counter(r["signal_type"] for r in rows)
    n_verified = sum(v.get("verification_status") == "verified" for v in ctx.verified_findings.values())
    used = {x for sg in signals for x in s_ids(sg)}
    metrics = {
        "status": d.get("status"), "signals": len(rows), "avg_supporting_findings": mean([r["refs"] for r in rows]),
        "support_resolution_pct": pct(sum(r["refs_resolved"] for r in rows), sum(r["refs"] for r in rows)),
        "verified_findings_used": len(used), "verified_findings_available": n_verified or None,
        "finding_usage_pct": pct(len(used), n_verified) if n_verified else None,
        "signals_single_worker_support": sum(len(r["supporting_workers"]) < 2 for r in rows),
        "signals_on_unverified_findings": sum(bool(r["unverified_support"]) for r in rows),
        "signals_entity_not_in_support": sum(bool(r["entities_missing_from_support"]) for r in rows),
        "signals_confidence_exceeds_support": sum("confidence_exceeds_support" in r["flags"] for r in rows),
        "signals_with_evidence_gaps": sum(bool(r["evidence_gaps"]) for r in rows),
        "avg_signal_confidence": mean([conf_num(r["confidence"]) for r in rows]),
        "signal_types": ", ".join(f"{t}={n}" for t, n in sorted(types.items())),
        "discarded_missing_support": stats.get("discarded_missing_support"),
        "discarded_insufficient_support": stats.get("discarded_insufficient_support"),
        **llm_stats(rows, cfg),
    }
    questions = [str(r) for r in block.get("decision_questions") or []]
    checks = [
        count_check("signals", stats, ("final_signals", "final_signal_count"), len(rows)),
        check("input_statistics.verified_findings matches the loaded verification",
              stats.get("verified_findings") == n_verified, f"stats={stats.get('verified_findings')} loaded={n_verified}")
        if have_status and "verified_findings" in stats else None,
        check("every supporting finding id resolves", unresolved == 0, f"{unresolved} unresolved")
        if (ctx.worker_findings or ctx.verified_findings) else None,
        check("pipeline reported no unresolved support references", not stats.get("support_references_unresolved"),
              f"{stats.get('support_references_unresolved')}") if "support_references_unresolved" in stats else None,
        check("no signal rests on findings the verifier did not verify", metrics["signals_on_unverified_findings"] == 0,
              f"{metrics['signals_on_unverified_findings']} signals") if have_status else None,
        check("decision_ready_context.key_signals matches signals", len(block.get("key_signals", [])) == len(rows))
        if "key_signals" in block else None,
        check("decision_question_count matches decision_ready_context", stats.get("decision_question_count") == len(questions),
              f"stats={stats.get('decision_question_count')} actual={len(questions)}")
        if "decision_question_count" in stats else None,
        check("every signal's decision_question is listed in decision_ready_context",
              all(str(s.get("decision_question")) in questions for s in signals if s.get("decision_question")), severity="warn")
        if questions else None,
        check("no signal names an entity absent from its support", metrics["signals_entity_not_in_support"] == 0,
              f"{metrics['signals_entity_not_in_support']} signals", "warn"),
        check("no convergence signal rests on a single worker",
              not any("convergence_from_single_worker" in r["flags"] for r in rows), severity="warn"),
        check("signal domains_involved are backed by its supporting findings",
              not any("domain_not_in_support" in r["flags"] for r in rows), severity="warn"),
        check("signal confidence never exceeds its support", metrics["signals_confidence_exceeds_support"] == 0, severity="warn"),
        check("warnings list is empty", not d.get("warnings"), "; ".join(map(str, d.get("warnings") or []))[:200], "warn"),
    ]
    return _result(rows, metrics, checks, cfg)


# ------------------------------------------------------------------ decision agent
@evaluator("decision")
def eval_decision(path, d, ctx, judge, cfg):
    cfg = cfg.for_agent("decision")
    meta = d.get("metadata") or {}
    valid = set(map(str, meta.get("valid_cross_signal_ids") or [])) | ctx.valid_signal_ids
    qrs = dict_items(d.get("question_recommendations"))
    opps, risks = dict_items(d.get("opportunities")), dict_items(d.get("risks"))
    actions, prios = dict_items(d.get("recommended_actions")), dict_items(d.get("priorities"))
    options = dict_items(d.get("options"))
    ref_year = ctx.ref_year
    rows = []

    for i, qr in enumerate(qrs, 1):
        rec = qr.get("recommendation") or {}
        ids = [str(x) for x in rec.get("supporting_signal_ids") or []]
        texts = ctx.signal_texts(ids)
        statement = " ".join(x for x in (rec.get("decision"), rec.get("rationale")) if x)
        action_txt = "; ".join(map(str, rec.get("actions") or []))
        crit_txt = "; ".join(map(str, rec.get("success_criteria") or []))
        full = f"{statement} Actions: {action_txt}. Success criteria: {crit_txt}. Limitations: {'; '.join(map(str, rec.get('limitations') or []))}"
        question = qr.get("question") or d.get("business_goal") or ctx.goal
        new_nums = ungrounded_numbers(full, texts)
        row = {
            "unit_type": "recommendation", "unit_index": i, "unit_id": f"q{i}", "question": question,
            "statement": statement, "confidence": rec.get("confidence", ""), "refs": len(ids),
            "refs_resolved": sum(x in valid for x in ids), "actions": len(rec.get("actions") or []),
            "risks": len(rec.get("risks") or []), "opportunities": len(rec.get("opportunities") or []),
            "success_criteria": len(rec.get("success_criteria") or []), "limitations": len(rec.get("limitations") or []),
            "new_numbers_not_in_signals": new_nums, "past_years_mentioned": past_years(full, ref_year),
        }
        row.update(judge_unit(judge, cfg, question, statement, texts, outputs={"actionability": full}))
        flags = base_flags(row, len(texts), cfg)
        if new_nums:
            flags.append("new_numbers_not_in_signals")      # targets such as '95% on-time' are new, not sourced
        if row["past_years_mentioned"]:
            flags.append("timeline_in_past")
        if not row["limitations"]:
            flags.append("no_limitations_stated")
        if not row["success_criteria"]:
            flags.append("no_success_criteria")
        row["flags"] = flags
        rows.append(row)

    recs = [r for r in rows if r["unit_type"] == "recommendation"]
    if d.get("executive_summary"):    # the summary must not say more than the recommendations
        rec_ctx = [r["statement"] for r in recs][:MAX_CTX_ITEMS]
        row = {"unit_type": "executive_summary", "unit_index": 0, "unit_id": "executive_summary",
               "statement": d["executive_summary"], "refs": 0, "refs_resolved": 0,
               "ungrounded_numbers": ungrounded_numbers(d["executive_summary"], rec_ctx)}
        row.update(judge_unit(judge, cfg, d.get("business_goal") or ctx.goal, d["executive_summary"], rec_ctx,
                              only=("faithfulness", "relevancy")))
        row["flags"] = [x for x in base_flags(row, len(rec_ctx), cfg) if x != "no_refs"]
        rows.append(row)

    if cfg.deep:  # also judge every recommended action
        for i, a in enumerate(actions, 1):
            ids = [str(x) for x in a.get("supporting_signal_ids") or []]
            texts = ctx.signal_texts(ids)
            statement = " ".join(x for x in (a.get("description"), a.get("intended_outcome")) if x)
            full = f"{statement} Success indicators: {'; '.join(map(str, a.get('success_indicators') or []))}"
            row = {"unit_type": "action", "unit_index": i, "unit_id": a.get("action_id", ""), "statement": statement,
                   "confidence": a.get("confidence", ""), "refs": len(ids), "refs_resolved": sum(x in valid for x in ids),
                   "requires_human_approval": a.get("requires_human_approval"),
                   "success_indicators": len(a.get("success_indicators") or []),
                   "past_years_mentioned": past_years(full, ref_year), "ungrounded_numbers": ungrounded_numbers(statement, texts)}
            row.update(judge_unit(judge, cfg, ctx.goal, statement, texts, outputs={"actionability": full}))
            row["flags"] = base_flags(row, len(texts), cfg) + (["timeline_in_past"] if row["past_years_mentioned"] else [])
            rows.append(row)

    def bad_ids(items):
        return sum(1 for it in items for x in (it.get("supporting_signal_ids") or []) if str(x) not in valid)

    ids_by_type = {"opportunity": {str(o.get("opportunity_id")) for o in opps}, "risk": {str(r.get("risk_id")) for r in risks},
                   "action": {str(a.get("action_id")) for a in actions}}
    prio_keys = {(str(p.get("item_type")), str(p.get("item_id"))) for p in prios}
    expected_keys = {(t, i) for t, ids in ids_by_type.items() for i in ids}
    undetermined = sum(str(p.get("priority")).lower() == "undetermined" for p in prios)
    approvals = sum(bool(a.get("requires_human_approval")) for a in actions)
    dangling = sum(1 for a in actions for x in a.get("related_opportunity_ids") or [] if str(x) not in ids_by_type["opportunity"]) \
        + sum(1 for a in actions for x in a.get("related_risk_ids") or [] if str(x) not in ids_by_type["risk"])
    used_signals = {x for it in [q.get("recommendation") or {} for q in qrs] + opps + risks + actions
                    for x in map(str, it.get("supporting_signal_ids") or [])}
    options_empty = sum(1 for o in options if not (o.get("what_we_would_do") or o.get("trade_offs")))
    norm_actions = [norm(a.get("title") or a.get("description") or "") for a in actions]
    dup_actions = sum(any(near_dup(x, y) for y in norm_actions[:k]) for k, x in enumerate(norm_actions) if x)
    answered = [norm(q.get("question", "")) for q in qrs]
    missing_q = [q for q in ctx.decision_questions if norm(q) not in answered]
    metrics = {
        "status": d.get("status"), "recommendations": len(recs), "opportunities": len(opps), "risks": len(risks),
        "recommended_actions": len(actions), "priorities": len(prios), "options": len(options),
        "options_without_content_pct": pct(options_empty, len(options)),
        "undetermined_priority_pct": pct(undetermined, len(prios)), "human_approval_pct": pct(approvals, len(actions)),
        "priority_mix": ", ".join(f"{k}={v}" for k, v in sorted(Counter(str(p.get("priority")) for p in prios).items())),
        "actions_with_success_indicators_pct": pct(sum(bool(a.get("success_indicators")) for a in actions), len(actions)),
        "actions_with_prerequisites_pct": pct(sum(bool(a.get("prerequisites")) for a in actions), len(actions)),
        "near_duplicate_actions": dup_actions,
        "avg_signals_per_recommendation": mean([r["refs"] for r in recs]),
        "signal_coverage_pct": pct(len(used_signals & valid), len(valid)),
        "avg_recommendation_confidence": mean([conf_num(r["confidence"]) for r in recs]),
        "recommendations_with_new_numbers": sum(bool(r["new_numbers_not_in_signals"]) for r in recs),
        "recommendations_with_past_timeline": sum(bool(r["past_years_mentioned"]) for r in recs),
        "additional_information_needed": len(d.get("additional_information_needed") or []),
        "limitations_listed": len(d.get("limitations") or []), "warnings": len(d.get("warnings") or []),
        **llm_stats(rows, cfg),
    }
    bad = {"recommendations": bad_ids([q.get("recommendation") or {} for q in qrs]),
           "opportunities": bad_ids(opps), "risks": bad_ids(risks), "actions": bad_ids(actions), "options": bad_ids(options)}
    checks = [
        check("all supporting_signal_ids are valid", sum(bad.values()) == 0,
              ", ".join(f"{k}={v}" for k, v in bad.items() if v)) if valid else None,
        count_check("recommendations", meta, ("recommendation_count",), len(qrs)),
        count_check("opportunities", meta, ("opportunity_count",), len(opps)),
        count_check("risks", meta, ("risk_count",), len(risks)),
        count_check("priorities", meta, ("priority_count",), len(prios)),
        count_check("recommended actions", meta, ("recommended_action_count",), len(actions)),
        check("every opportunity/risk/action has a priority entry (matching type and id)", expected_keys <= prio_keys,
              f"{len(expected_keys - prio_keys)} without priority") if prios else None,
        check("no priority points to an unknown item", prio_keys <= expected_keys, f"{len(prio_keys - expected_keys)} unknown")
        if prios else None,
        check("action links to opportunities/risks resolve", dangling == 0, f"{dangling} dangling"),
        check("valid_cross_signal_ids equals the cross-signal agent's signals",
              set(map(str, meta.get("valid_cross_signal_ids") or [])) == set(ctx.signals),
              f"decision={len(meta.get('valid_cross_signal_ids') or [])} cross_signal={len(ctx.signals)}")
        if ctx.signals and meta.get("valid_cross_signal_ids") else None,
        check("every decision question from the cross-signal agent is answered", not missing_q, f"{len(missing_q)} unanswered")
        if ctx.decision_questions else None,
        check("priorities are determined (<=25% 'undetermined')", (pct(undetermined, len(prios)) or 0) <= 25,
              f"{pct(undetermined, len(prios))}% undetermined", "warn") if prios else None,
        check("no unsupported questions", not meta.get("unsupported_questions")),
        check("every action has success indicators", all(a.get("success_indicators") for a in actions), severity="warn")
        if actions else None,
        check("every cross-signal is used by at least one recommendation", used_signals >= valid,
              f"{len(valid - used_signals)} unused", "warn") if valid else None,
        check("no near-duplicate actions", dup_actions == 0, f"{dup_actions} duplicates", "warn") if actions else None,
        check("options carry content (what we would do / trade-offs)", options_empty == 0,
              f"{options_empty}/{len(options)} empty", "warn") if options else None,
        check("no recommendation has numbers absent from its signals", metrics["recommendations_with_new_numbers"] == 0,
              f"{metrics['recommendations_with_new_numbers']} recommendations", "warn") if ctx.signals else None,
        check("no recommendation schedules work in a year that is already over",
              metrics["recommendations_with_past_timeline"] == 0,
              ", ".join(f"{r['unit_id']}:{r['past_years_mentioned']}" for r in recs if r["past_years_mentioned"]),
              "warn") if ref_year else None,
        check("every action requires human approval", approvals == len(actions), f"{approvals}/{len(actions)}")
        if actions else None,
        check("decision agent reported no warnings", not d.get("warnings"), "; ".join(map(str, d.get("warnings") or []))[:200], "warn"),
    ]
    return _result(rows, metrics, checks, cfg)



# ------------------------------------------------------------------ orchestration / RAG / telemetry evaluators
def _walk_records(value: Any, keys: tuple[str, ...]) -> list[dict]:
    """Find nested record lists without assuming one exact producer schema."""
    found: list[dict] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, list):
                found.extend(x for x in child if isinstance(x, dict))
            else:
                found.extend(_walk_records(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_records(child, keys))
    return found


def _execution_calls(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read one ordered execution trace from worker provenance or evaluator telemetry."""
    trace = payload.get("execution_trace")
    if not isinstance(trace, dict):
        metadata = payload.get("metadata") or {}
        trace = metadata.get("execution_trace")
    if not isinstance(trace, dict):
        trace = (metadata.get("provenance") if isinstance(metadata, dict) else None) or payload.get("provenance")
    trace = trace if isinstance(trace, dict) else payload

    for key in ("calls", "tool_calls", "tool_events", "tools_used"):
        calls = trace.get(key)
        if isinstance(calls, list):
            return [x for x in calls if isinstance(x, dict)], trace

    records = _walk_records(payload, ("calls", "tool_calls", "tool_events", "tools_used"))
    if records:
        return records, trace

    spans = _walk_records(payload, ("spans", "trace_events", "observability_events", "events"))
    spans_by_id = {str(span["span_id"]): span for span in spans if span.get("span_id")}

    def worker_context(span: dict[str, Any]) -> tuple[str, str, Any]:
        current = span
        visited: set[str] = set()
        while current:
            attrs = current.get("attributes") or {}
            worker = attrs.get("edrak.agent.type")
            if worker:
                return str(worker), str(attrs.get("edrak.task.id") or ""), attrs.get("edrak.task.attempt")
            parent_id = str(current.get("parent_span_id") or "")
            if not parent_id or parent_id in visited:
                break
            visited.add(parent_id)
            current = spans_by_id.get(parent_id, {})
        return "", "", None

    calls = []
    for span in spans:
        attrs = span.get("attributes") or {}
        tool = attrs.get("edrak.tool.name") or span.get("tool") or span.get("tool_name")
        if tool:
            argument_types = attrs.get("edrak.tool.argument.types", {})
            if isinstance(argument_types, str):
                try:
                    argument_types = json.loads(argument_types)
                except json.JSONDecodeError:
                    argument_types = {}
            worker, task_id, attempt = worker_context(span)
            calls.append({
                "tool": tool,
                "provider": attrs.get("edrak.tool.provider"),
                "worker": worker,
                "task_id": task_id,
                "attempt": attempt,
                "status": attrs.get("edrak.tool.status") or span.get("status"),
                "error_code": attrs.get("edrak.tool.error_code"),
                "fallback_used": attrs.get("edrak.tool.fallback"),
                "argument_names": attrs.get("edrak.tool.argument.names"),
                "argument_types": argument_types if isinstance(argument_types, dict) else {},
                "argument_values": _json_attr(attrs.get("edrak.tool.argument.values")),
                "result_count": attrs.get("edrak.tool.result.count"),
                "result_summary": _json_attr(attrs.get("edrak.tool.result.summary")),
                "result_payload": _json_attr(attrs.get("edrak.tool.result.payload")),
                "latency_ms": span.get("duration_ms"),
                "branch": attrs.get("edrak.tool.branch"),
                "span_id": span.get("span_id"),
            })
    return calls, trace


def _json_attr(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _call_query(call: dict[str, Any]) -> str:
    """Return the recorded query used to correlate an individual RAG retrieval."""
    attrs = call.get("attributes") or {}
    arguments = call.get("args", call.get("arguments"))
    if not isinstance(arguments, dict):
        arguments = _json_attr(call.get("argument_values") or attrs.get("edrak.tool.argument.values"))
    query = arguments.get("query") if isinstance(arguments, dict) else None
    return str(query).strip() if query is not None else ""


def _golden_answer_for_rag(
    rag_payload: dict[str, Any],
    loaded: list[tuple[Path, str, dict[str, Any]]],
    ground_truth_root: Path | None = None,
) -> tuple[str, str] | None:
    """Find an independent expected answer for this RAG worker in its use-case golden dataset."""
    worker = str(rag_payload.get("worker") or "").strip()
    if not worker or not re.fullmatch(r"[A-Za-z0-9_-]+", worker):
        return None
    use_case = ""
    for _, kind, data in loaded:
        if kind != "input":
            continue
        business_context = data.get("business_context") or {}
        use_case = str(business_context.get("use_case") or data.get("use_case") or "").strip().casefold()
        if use_case:
            break
    if not use_case:
        return None

    root = ground_truth_root or Path(__file__).resolve().parents[2] / "golden" / "ground_truth"
    matches = []
    for input_path in sorted(root.glob("*/inputs/initial_input.json")):
        golden_input = read_json(input_path)
        business_context = golden_input.get("business_context") or {}
        golden_use_case = str(
            business_context.get("use_case") or golden_input.get("use_case") or ""
        ).strip().casefold()
        if golden_use_case == use_case:
            matches.append(input_path.parent.parent)
    if len(matches) > 1:
        raise ValueError(f"Multiple golden datasets match use case {use_case!r}.")
    if not matches:
        return None

    dataset = matches[0]
    worker_path = dataset / "inputs" / f"worker_{worker}.json"
    if not worker_path.is_file():
        return None
    worker_data = read_json(worker_path)
    expected_output = worker_data.get("expected_output")
    if isinstance(expected_output, str) and expected_output.strip():
        source = (Path("golden") / "ground_truth" / dataset.name / "inputs" / worker_path.name).as_posix()
        return expected_output.strip(), source
    findings = [
        str(finding.get("statement") or "").strip()
        for finding in dict_items(worker_data.get("findings"))
        if str(finding.get("statement") or "").strip()
    ]
    if not findings:
        return None
    source = (Path("golden") / "ground_truth" / dataset.name / "inputs" / worker_path.name).as_posix()
    return "\n".join(f"- {finding}" for finding in findings), source


def _observability_entry(result: dict[str, Any], per_call: bool = True) -> dict[str, Any]:
    """Tool-trace view of one file; metrics and failed checks live in the per-agent report."""
    quality = result.get("execution_quality") or {}
    entry: dict[str, Any] = {"file": result["file"], "status": quality.get("status", "not_assessable")}
    if entry["status"] == "not_assessable":
        return entry
    metrics = dict(quality.get("metrics") or {})
    metrics.pop("status", None)
    metrics.pop("dimensions_assessable", None)
    tools = metrics.pop("tools", None)
    if not per_call:
        metrics.pop("call_details", None)
    if tools:
        metrics["tool_counts"] = dict(Counter(tools))
    entry["metrics"] = metrics
    entry["dimensions"] = {
        name: {key: value for key, value in dim.items() if key in {"status", "score", "reason"}}
        for name, dim in (quality.get("dimensions") or {}).items()
        if dim.get("status") != "not_assessable"
    }
    return entry


def _drop_nulls(value: Any) -> Any:
    """Remove null-valued keys so reports list only what was actually measured."""
    if isinstance(value, dict):
        return {key: _drop_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_nulls(item) for item in value]
    return value


def _secret_redacted(value: Any) -> Any:
    """Remove credential-like values before optional tool payloads reach the model judge."""
    secret_key = re.compile(r"(?:token|secret|password|api[_-]?key|authorization|cookie|credential)", re.I)
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if secret_key.search(str(key)) else _secret_redacted(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_secret_redacted(item) for item in value[:MAX_CTX_ITEMS]]
    if isinstance(value, str):
        return value[:MAX_CTX_CHARS]
    return value


def _execution_summary(call: dict[str, Any], include_payloads: bool) -> dict[str, Any]:
    attrs = call.get("attributes") or {}
    args = call.get("args", call.get("arguments"))
    argument_types = call.get("argument_types") or attrs.get("edrak.tool.argument.types") or {}
    if isinstance(argument_types, str):
        try:
            argument_types = json.loads(argument_types)
        except json.JSONDecodeError:
            argument_types = {}
    recorded_argument_names = call.get("argument_names") or attrs.get("edrak.tool.argument.names")
    if isinstance(recorded_argument_names, str):
        recorded_argument_names = [name for name in recorded_argument_names.split(",") if name]
    result = call.get("result_summary", call.get("tool_result_summary"))
    if result is None:
        result = attrs.get("edrak.tool.result.summary")
    summary = {
        "tool": call.get("tool") or call.get("tool_name") or attrs.get("edrak.tool.name") or call.get("name"),
        "provider": call.get("provider") or attrs.get("edrak.tool.provider"),
        "worker": call.get("worker"),
        "task_id": call.get("task_id"),
        "attempt": call.get("attempt"),
        "status": call.get("status") or attrs.get("edrak.tool.status"),
        "branch": call.get("branch") or attrs.get("edrak.tool.branch"),
        "argument_names": (
            sorted(map(str, args.keys())) if isinstance(args, dict)
            else sorted(map(str, recorded_argument_names or []))
        ),
        "argument_types": (
            {str(k): type(v).__name__ for k, v in args.items()}
            if isinstance(args, dict)
            else {str(k): str(v) for k, v in argument_types.items()}
            if isinstance(argument_types, dict)
            else {}
        ),
        "result_count": call.get("count", call.get("result_count", attrs.get("edrak.tool.result.count"))),
        "latency_ms": call.get("latency_ms", call.get("duration_ms")),
        "fallback_used": call.get("fallback_used", attrs.get("edrak.tool.fallback")),
        "error_code": call.get("error_code") or attrs.get("edrak.tool.error_code"),
    }
    safe_result_summary = _json_attr(result)
    if safe_result_summary is not None:
        summary["result_summary"] = _secret_redacted(safe_result_summary)
        if (
            summary["result_count"] is None
            and isinstance(safe_result_summary, dict)
            and safe_result_summary.get("type") == "array"
            and is_num(safe_result_summary.get("count"))
        ):
            summary["result_count"] = int(safe_result_summary["count"])
    if include_payloads:
        argument_values = call.get("argument_values") or attrs.get("edrak.tool.argument.values")
        if argument_values is not None:
            summary["argument_values"] = _secret_redacted(_json_attr(argument_values))
        if isinstance(args, dict):
            summary["arguments"] = _secret_redacted(args)
        result_payload = call.get("result_payload") or attrs.get("edrak.tool.result.payload")
        if result_payload is None:
            result_payload = call.get("result")
        if result_payload is not None:
            summary["result"] = _secret_redacted(_json_attr(result_payload))
    return summary


def _schema_argument_errors(call: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str] | None:
    """Check argument shape from either a call schema or a known tool schema."""
    args = call.get("args", call.get("arguments"))
    schema = schema or call.get("argument_schema") or call.get("input_schema")
    if not isinstance(schema, dict):
        return None
    argument_types = call.get("argument_types") or (call.get("attributes") or {}).get(
        "edrak.tool.argument.types"
    ) or {}
    if isinstance(argument_types, str):
        try:
            argument_types = json.loads(argument_types)
        except json.JSONDecodeError:
            argument_types = {}
    if not isinstance(argument_types, dict):
        argument_types = {}
    argument_names = call.get("argument_names") or (call.get("attributes") or {}).get(
        "edrak.tool.argument.names"
    )
    if isinstance(argument_names, str):
        argument_names = [name.strip() for name in argument_names.split(",") if name.strip()]
    if not isinstance(args, dict) and not argument_names and not argument_types:
        return None
    observed_names = set(map(str, args.keys())) if isinstance(args, dict) else set(map(str, argument_names or []))
    observed_names.update(map(str, argument_types))
    errors = []
    for key in schema.get("required", []):
        if key not in observed_names:
            errors.append(f"missing {key}")
    expected_types = {
        "string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list,
        "null": type(None),
    }
    observed_type_names = {
        "string": {"str", "string"},
        "integer": {"int", "integer"},
        "number": {"int", "integer", "float", "number"},
        "boolean": {"bool", "boolean"},
        "object": {"dict", "object"},
        "array": {"list", "tuple", "array"},
        "null": {"nonetype", "null", "none"},
    }
    for key, definition in (schema.get("properties") or {}).items():
        if key not in observed_names or not isinstance(definition, dict):
            continue
        expected = definition.get("type")
        choices = expected if isinstance(expected, list) else [expected]
        types = tuple(expected_types[x] for x in choices if x in expected_types)
        if isinstance(args, dict) and key in args:
            value = args[key]
            if types and (
                isinstance(value, bool) and (int in types or float in types)
                or not isinstance(value, types)
            ):
                errors.append(f"invalid type for {key}")
        elif key in argument_types and not any(
            str(argument_types[key]).lower() in observed_type_names.get(choice, {choice})
            for choice in choices if choice in observed_type_names
        ):
            errors.append(f"invalid type for {key}")
    return errors


def eval_execution_trace(payload: dict[str, Any], goal: str, output: str, judge: Any,
                         include_payloads: bool = False) -> dict[str, Any]:
    """Assess execution only against explicit plans and recorded tool-call evidence."""
    calls, trace = _execution_calls(payload)
    summaries = [_execution_summary(call, include_payloads) for call in calls]
    tool_names = [str(x.get("tool") or "") for x in summaries]
    tool_names = [x for x in tool_names if x]
    declared_tools = trace.get("allowed_tools") or trace.get("expected_tools")
    available_tools = trace.get("available_tools")
    allowed = declared_tools or available_tools
    worker = str(payload.get("worker") or "")
    worker_policy = WORKER_ALLOWED_TOOLS.get(worker)
    if not allowed and worker_policy:
        allowed = list(worker_policy)
    allowed_by_branch = trace.get("allowed_tools_by_branch") or {}
    allowed_by_stage = trace.get("allowed_tools_by_stage") or {}
    if not isinstance(allowed_by_branch, dict):
        allowed_by_branch = {}
    if not isinstance(allowed_by_stage, dict):
        allowed_by_stage = {}
    expected_sequence = trace.get("expected_sequence") or trace.get("workflow_sequence")
    if isinstance(declared_tools, str):
        declared_tools = [declared_tools]
    if isinstance(available_tools, str):
        available_tools = [available_tools]
    if isinstance(allowed, str):
        allowed = [allowed]
    if isinstance(expected_sequence, str):
        expected_sequence = [expected_sequence]

    selection_errors = []
    selection_checkable = bool(allowed or allowed_by_branch or allowed_by_stage)
    task_policy_complete = bool(calls)
    selection_unassessed = 0
    for call, summary in zip(calls, summaries):
        branch, stage = call.get("branch"), call.get("stage")
        call_worker = str(call.get("worker") or worker)
        call_policy = WORKER_ALLOWED_TOOLS.get(call_worker)
        options = allowed_by_branch.get(branch) if branch else None
        task_specific_options = bool(options)
        if not options and stage:
            options = allowed_by_stage.get(stage)
            task_specific_options = bool(options)
        options = options or allowed or call_policy
        if options:
            selection_checkable = True
        tool = summary.get("tool")
        if not tool:
            selection_errors.append("call without tool name")
        elif options and str(tool) not in set(map(str, options)):
            selection_errors.append(str(tool))
        elif not options:
            selection_checkable = False
            selection_unassessed += 1
        if not task_specific_options and not call_policy:
            task_policy_complete = False
    selection_errors = sorted(set(selection_errors))
    schema_results = [
        _schema_argument_errors(
            call,
            TOOL_ARGUMENT_SCHEMAS.get(str(call.get("tool") or call.get("tool_name") or "")),
        )
        for call in calls
    ]
    schema_checked = [result for result in schema_results if result is not None]
    schema_errors = [f"call {index + 1}: {error}" for index, errors in enumerate(schema_results)
                     if errors for error in errors]

    sequence_missing: list[str] = []
    if expected_sequence:
        actual_iter = iter(tool_names)
        actual = list(actual_iter)
        cursor = 0
        for expected in expected_sequence:
            try:
                cursor = actual.index(str(expected), cursor) + 1
            except ValueError:
                sequence_missing.append(str(expected))

    errors = [
        (index, call) for index, call in enumerate(calls)
        if str(call.get("status") or (call.get("attributes") or {}).get("edrak.tool.status", "")).lower()
        in {"error", "failed", "failure", "timeout"}
        or call.get("error") or call.get("error_code")
    ]
    def recovered_by_retry(index: int, failed_call: dict[str, Any]) -> bool:
        call_id = failed_call.get("call_id") or failed_call.get("id") or failed_call.get("span_id")
        if not call_id:
            return False
        return any(
            later.get("retry_of") == call_id
            and str(later.get("status", "")).lower() in {"ok", "success", "completed"}
            for later in calls[index + 1:]
        )

    handling_recorded = [
        call.get("error_handled") is True
        or call.get("fallback_used") is True
        or recovered_by_retry(index, call)
        for index, call in errors
    ]
    signatures = []
    for call, summary in zip(calls, summaries):
        args = call.get("args", call.get("arguments"))
        if isinstance(args, dict):
            signatures.append(json.dumps([summary.get("tool"), args], sort_keys=True, ensure_ascii=False))
        elif call.get("argument_fingerprint"):
            signatures.append(json.dumps([summary.get("tool"), call["argument_fingerprint"]]))
        else:
            argument_values = _json_attr(call.get("argument_values") or (call.get("attributes") or {}).get(
                "edrak.tool.argument.values"
            ))
            if isinstance(argument_values, (dict, list)):
                fingerprint = hashlib.sha256(
                    json.dumps(argument_values, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                signatures.append(json.dumps([summary.get("tool"), fingerprint]))
            else:
                signatures.append(None)
    duplicate_counts = Counter(signature for signature in signatures if signature is not None)
    repeated_calls = sum(count - 1 for count in duplicate_counts.values() if count > 1)
    complete_argument_trace = bool(calls) and all(signature is not None for signature in signatures)
    has_result_summaries = any(
        call.get("result_summary") is not None or call.get("tool_result_summary") is not None
        or call.get("result") is not None
        for call in calls
    )
    complete = trace.get("complete") is True or trace.get("trace_complete") is True
    outcome_summary = {
        "calls": len(calls),
        "tools": tool_names,
        "allowed_tools": declared_tools,
        "available_tools": available_tools,
        "expected_sequence": expected_sequence,
        "sequence_missing": sequence_missing,
        "schema_checks": len(schema_checked),
        "schema_errors": schema_errors,
        "errors": len(errors),
        "repeated_calls": repeated_calls,
        "trace_complete": complete,
        "call_details": [
            {key: value for key, value in summary.items()
             if key in {"tool", "provider", "worker", "task_id", "attempt", "status", "argument_names",
                            "argument_types", "argument_values", "result_count", "result_summary", "latency_ms",
                            "error_code", "fallback_used"}
             and value not in (None, "", [], {})}
            for summary in summaries
        ],
    }

    dimensions: dict[str, dict[str, Any]] = {
        name: {"status": "not_assessable", "score": None, "reason": ""}
        for name in EXECUTION_GEVAL
    }
    checks = []
    if not calls:
        for dimension in dimensions.values():
            dimension["reason"] = "No structured tool-call trace was supplied for this stage."
        checks.append(check(
            "execution quality has structured trace evidence", False,
            "Execution quality has no structured trace evidence for this stage; tool selection, arguments, result use, sequence, error handling, and efficiency are not assessable.",
            "warn",
        ))
    else:
        dimensions["tool_selection"]["status"] = (
            "assessable" if selection_checkable and (declared_tools or task_policy_complete) else "partial"
        )
        dimensions["tool_selection"]["reason"] = (
            "Only registered tool availability is known; task-specific expected tools are absent."
            if not task_policy_complete and not declared_tools and (available_tools or allowed_by_branch or allowed_by_stage) else
            "No allowed/expected tool set; use judgment only."
            if not selection_checkable else
            f"{len(selection_errors)} call(s) used tools outside the declared set." if selection_errors else ""
        )
        dimensions["tool_arguments"]["status"] = (
            "assessable" if schema_checked else
            "partial" if any(summary["argument_names"] for summary in summaries) else
            "not_assessable"
        )
        dimensions["tool_arguments"]["reason"] = (
            f"{len(schema_errors)} schema violation(s)." if schema_errors else
            "No per-call input schema; value suitability requires opt-in payload judging."
            if not include_payloads else ""
        )
        dimensions["tool_result_use"]["status"] = (
            "assessable" if include_payloads and has_result_summaries and output.strip() else "not_assessable"
        )
        dimensions["tool_result_use"]["reason"] = (
            "Requires tool result summaries, final output, and --include-tool-payloads."
            if dimensions["tool_result_use"]["status"] == "not_assessable" else ""
        )
        dimensions["workflow_sequence"]["status"] = "assessable" if expected_sequence else "not_assessable"
        dimensions["workflow_sequence"]["reason"] = (
            f"Missing expected ordered steps: {sequence_missing}." if sequence_missing else
            "No expected workflow sequence was supplied." if not expected_sequence else ""
        )
        dimensions["error_handling"]["status"] = (
            "assessable" if errors and all(handling_recorded)
            else "partial" if errors else "not_assessable"
        )
        dimensions["error_handling"]["reason"] = (
            "Trace has no failures." if not errors else
            "Failure recovery/outcome fields are incomplete." if dimensions["error_handling"]["status"] == "partial" else ""
        )
        dimensions["efficiency"]["status"] = "assessable" if complete_argument_trace else "partial"
        dimensions["efficiency"]["reason"] = f"{repeated_calls} exact repeated call(s) with the same tool and arguments." if repeated_calls else ""
        if not complete_argument_trace and not repeated_calls:
            dimensions["efficiency"]["reason"] = "Call arguments/fingerprints are incomplete; exact duplicate calls cannot be established."

        if selection_checkable:
            selection_detail = []
            if selection_errors:
                selection_detail.append(", ".join(selection_errors))
            if selection_unassessed:
                selection_detail.append(f"{selection_unassessed} call(s) have no applicable tool policy")
            checks.append(check("tool calls stay within declared/available tools",
                                not selection_errors and not selection_unassessed,
                                "; ".join(selection_detail),
                                "error" if declared_tools or task_policy_complete else "warn"))
        else:
            checks.append(check("tool choice is constrained by a declared allowed tool set", False,
                                "no allowed_tools/expected_tools supplied", "warn"))
        if schema_checked:
            checks.append(check("tool arguments satisfy supplied schemas", not schema_errors,
                                "; ".join(schema_errors)))
        else:
            checks.append(check("tool arguments can be validated against schemas", False,
                                "no per-call argument schema supplied", "warn"))
        if expected_sequence:
            checks.append(check("observed tools follow the expected sequence", not sequence_missing,
                                ", ".join(sequence_missing)))
        else:
            checks.append(check("workflow sequence is assessable", False,
                                "no expected_sequence/workflow_sequence supplied", "warn"))
        if errors:
            unhandled = [
                str(call.get("tool") or call.get("tool_name") or "unknown")
                for index, call in errors
                if call.get("error_handled") is not True
                and call.get("fallback_used") is not True
                and not recovered_by_retry(index, call)
            ]
            unhandled_by_tool = Counter(unhandled)
            detail = f"{len(unhandled)} unhandled failure(s)"
            if unhandled_by_tool:
                detail += " across " + ", ".join(
                    f"{tool}={count}" for tool, count in sorted(unhandled_by_tool.items())
                )
            checks.append(check("tool failures are handled or explicitly surfaced", not unhandled,
                                detail if unhandled else "", "warn"))
        else:
            checks.append(check("trace records tool failure handling", complete,
                                "no errors observed, but trace completeness is not declared", "warn"))
        checks.append(check("trace avoids duplicate tool work", repeated_calls == 0,
                            f"{repeated_calls} repeated call(s)", "warn"))

        if judge and getattr(judge, "enabled", False) and goal:
            public_context = [
                json.dumps({
                    "available_tools": available_tools or allowed,
                    "allowed_tools_by_branch": allowed_by_branch,
                    "allowed_tools_by_stage": allowed_by_stage,
                    "expected_sequence": expected_sequence,
                    "argument_schemas": [call.get("argument_schema") or call.get("input_schema")
                                         for call in calls if call.get("argument_schema") or call.get("input_schema")],
                }, ensure_ascii=False, default=str)[:MAX_CTX_CHARS]
            ]
            trace_text = json.dumps(summaries, ensure_ascii=False, default=str)[:MAX_CTX_CHARS]
            deterministic_selection = selection_checkable and (declared_tools or task_policy_complete)
            judge_metrics = () if deterministic_selection else ("tool_selection",)
            if include_payloads and any("arguments" in item for item in summaries):
                judge_metrics += ("tool_arguments",)
            if include_payloads and complete_argument_trace:
                judge_metrics += ("efficiency",)
            if include_payloads and has_result_summaries and output.strip():
                judge_metrics += ("tool_result_use",)
            if expected_sequence:
                judge_metrics += ("workflow_sequence",)
            if errors:
                judge_metrics += ("error_handling",)
            for metric in judge_metrics:
                context = public_context
                actual = trace_text
                if metric == "tool_result_use":
                    context = [
                        json.dumps([{"tool": item.get("tool"),
                                     "result_summary": item.get("result_summary"),
                                     "result": item.get("result")}
                                    for item in summaries], ensure_ascii=False, default=str)[:MAX_CTX_CHARS]
                    ]
                    actual = output[:MAX_CTX_CHARS]
                elif metric == "workflow_sequence":
                    context = [json.dumps({"expected": expected_sequence, "observed": tool_names})]
                result = judge.run(
                    metric, goal, actual, context, 0.7, spec=EXECUTION_GEVAL[metric]
                )
                dimensions[metric].update({
                    "status": "assessable" if result.get("score") is not None else "pending_judge",
                    "score": result.get("score"),
                    "reason": result.get("reason") or result.get("error") or dimensions[metric]["reason"],
                })

    assessable = sum(value["status"] == "assessable" for value in dimensions.values())
    status = "not_assessable" if not calls else "assessable" if assessable == len(dimensions) else "partial"
    outcome_summary.update({"status": status, "dimensions_assessable": assessable})
    return {"status": status, "metrics": outcome_summary, "dimensions": dimensions, "checks": checks}


@evaluator("orchestration")
def eval_orchestration(path, d, ctx, judge, cfg):
    plan = d.get("plan") or {}
    tasks = dict_items(plan.get("tasks"))
    results = dict_items(d.get("results"))
    task_key = lambda item: str(item.get("task_id") or item.get("worker") or item.get("name") or "")
    planned = {task_key(task) for task in tasks if task_key(task)}
    result_by_name = {
        task_key(result): result
        for result in results
        if task_key(result)
    }
    missing = sorted(planned - set(result_by_name))
    unexpected = sorted(set(result_by_name) - planned) if planned else []
    success_statuses = {"completed", "success", "succeeded", "ok", "partial"}
    failed_statuses = {"failed", "error", "timeout"}

    def has_error(result: dict[str, Any]) -> bool:
        error = result.get("error")
        return error is not None and str(error).strip().casefold() not in {"", "none", "null"}

    def has_generated_output(result: dict[str, Any]) -> bool:
        output_fields = ("findings", "evidence", "signals", "gaps", "conflicts", "output", "result", "summary")
        return any(
            bool(value.strip()) if isinstance(value, str) else bool(value)
            for key in output_fields
            if (value := result.get(key)) is not None
        )

    successful = {
        name for name in planned
        if name in result_by_name
        and (
            has_generated_output(result_by_name[name])
            or (
                str(result_by_name[name].get("status") or "").strip().casefold() in success_statuses
                and not has_error(result_by_name[name])
            )
        )
    }
    failed_names = set(missing) | {
        name for name, result in result_by_name.items()
        if name in planned
        and not has_generated_output(result)
        and (
            str(result.get("status") or "").strip().casefold() in failed_statuses
            or has_error(result)
        )
    }
    failed = [result_by_name[name] for name in failed_names if name in result_by_name]
    partial_tasks = sum(
        1 for name in planned
        if name in result_by_name
        and str(result_by_name[name].get("status") or "").strip().casefold() == "partial"
        and name in successful
    )
    dependency_edges = []
    dependency_violations = 0
    dependency_unassessable = 0
    deps_missing = []
    for task in tasks:
        name = str(task.get("worker") or task.get("name") or task.get("task_id") or "")
        deps = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(deps, str): deps = [deps]
        for dep in deps:
            dep_name = str(dep.get("worker") or dep.get("task_id") or dep) if isinstance(dep, dict) else str(dep)
            dependency_edges.append((dep_name, name))
            if dep_name not in planned:
                deps_missing.append(f"{name}->{dep_name}")
                dependency_violations += 1
                continue
            prerequisite, dependent = result_by_name.get(dep_name), result_by_name.get(name)
            if not prerequisite or dep_name not in successful:
                dependency_violations += 1
                continue
            prerequisite_end = parse_ts(prerequisite.get("completed_at"))
            dependent_start = parse_ts((dependent or {}).get("started_at"))
            if prerequisite_end is None or dependent_start is None:
                dependency_unassessable += 1
            elif prerequisite_end > dependent_start:
                dependency_violations += 1

    retries_from_attempts = sum(
        max(0, int(result.get("attempt") or 1) - 1)
        for result in results
        if str(result.get("attempt") or "1").isdigit()
    )
    retry_records = _walk_records(d, ("events", "trace_events", "spans"))
    retry_events = sum(
        1 for item in retry_records
        if any(
            marker in str(item.get("event") or item.get("name") or item.get("type") or "").lower()
            for marker in ("retry", "replan")
        )
    )
    manifest = getattr(ctx, "run_manifest", {}) or {}
    replan_stages = sum(
        1 for stage in dict_items(manifest.get("stages"))
        if str(stage.get("node") or "").strip().casefold() == "replan"
    )
    explicit_replans = d.get("replan_count") or (d.get("metadata") or {}).get("replan_count") or 0
    try:
        explicit_replans = max(0, int(explicit_replans))
    except (TypeError, ValueError):
        explicit_replans = 0
    retries = max(retries_from_attempts, retry_events, replan_stages, explicit_replans)

    retried_tasks = {
        task_key(item)
        for item in tasks + results
        if task_key(item) and str(item.get("attempt") or "1").isdigit()
        and int(item.get("attempt") or 1) > 1
    }
    retried_workers: set[str] = set()
    workers_before_replan: set[str] = set()
    dispatched_workers: set[str] = set()
    for stage in dict_items(manifest.get("stages")):
        node = str(stage.get("node") or "").strip().casefold()
        if node == "replan":
            workers_before_replan = set(dispatched_workers)
            dispatched_workers = set()
            continue
        if node != "dispatch":
            continue
        for artifact in stage.get("artifacts") or []:
            match = re.search(r"worker_(.+?)_attempt_\d+_", str(artifact))
            if not match:
                continue
            worker = match.group(1)
            if worker in workers_before_replan:
                retried_workers.add(worker)
            dispatched_workers.add(worker)
    retried_tasks.update(
        task_key(task)
        for task in tasks
        if task_key(task) and str(task.get("worker") or "") in retried_workers
    )
    manifest_artifacts = manifest.get("artifacts") or {}
    if isinstance(manifest_artifacts, dict):
        for artifact_key in manifest_artifacts:
            parts = str(artifact_key).split(":")
            if len(parts) >= 3 and parts[0] == "worker" and parts[-1].isdigit() and int(parts[-1]) > 1:
                retried_tasks.add(parts[1])
    successfully_retried_tasks = len(retried_tasks & successful)
    workflow_status = str(d.get("status") or manifest.get("status") or "").strip().casefold()
    start_time = parse_ts(manifest.get("started_at_utc"))
    end_time = parse_ts(manifest.get("completed_at_utc") or d.get("completed_at"))
    if start_time is None:
        task_starts = [parse_ts(r.get("started_at")) for r in results]
        task_starts = [t for t in task_starts if t is not None]
        start_time = min(task_starts) if task_starts else None
    if end_time is None:
        task_ends = [parse_ts(r.get("completed_at")) for r in results]
        task_ends = [t for t in task_ends if t is not None]
        end_time = max(task_ends) if task_ends else None
    execution_duration = (
        round((end_time - start_time).total_seconds(), 3)
        if start_time and end_time and end_time >= start_time else None
    )

    rows = []
    for i, task in enumerate(tasks, 1):
        name = task_key(task) or f"task-{i}"
        result = result_by_name.get(name, {})
        status = str(result.get("status") or ("completed" if result else "missing")).lower()
        rows.append({"unit_type":"orchestration_task","unit_index":i,"unit_id":name,
                     "statement":str(task.get("goal") or task.get("description") or ""),
                     "status":status,"dependencies":task.get("depends_on") or task.get("dependencies") or [],
                     "result_present":bool(result),"flags":(["task_result_missing"] if not result else []) +
                     (["task_failed"] if result in failed else [])})
    tool_records = _walk_records(d, ("tool_calls", "tools_used", "tool_events"))
    completed_pct = pct(len(successful), len(planned))
    workflow_success_pct = (
        completed_pct
        if workflow_status in {"completed", "success", "succeeded", "ok"} else
        0.0
        if workflow_status in {"failed", "error", "partial", "cancelled", "canceled"} else None
    )
    dependency_integrity_pct = (
        pct(len(dependency_edges) - dependency_violations, len(dependency_edges))
        if dependency_edges and not dependency_unassessable else None
    )
    metrics = {"planned_tasks":len(tasks),"result_tasks":len(results),
               "successful_tasks":len(successful),
               "task_completion_pct":completed_pct,
               "workflow_success_pct":workflow_success_pct,
               "dependency_integrity_pct":dependency_integrity_pct,
               "dependency_edges":len(dependency_edges),
               "dependency_violations":dependency_violations,
               "dependency_unassessable":dependency_unassessable,
               "missing_tasks":len(missing),"unexpected_results":len(unexpected),
               "missing_dependencies":len(deps_missing),"failed_tasks":len(failed_names),
               "partial_tasks":partial_tasks,
               "incomplete_tasks":len(planned - successful - failed_names),
               "failure_rate_pct":pct(len(failed_names),len(planned)),
               "tool_calls_observed":len(tool_records),"retry_events":retries,
               "successfully_retried_tasks":successfully_retried_tasks,
               "retry_rate_pct":pct(successfully_retried_tasks, len(planned)),
               "execution_duration_seconds":execution_duration,
               "execution_duration_source":"manifest" if manifest.get("started_at_utc") and manifest.get("completed_at_utc")
               else "task timestamps" if start_time and end_time else None}
    checks = [
        check("planned tasks have results", not missing, ", ".join(missing)),
        check("results correspond to planned tasks", not unexpected, ", ".join(unexpected), "warn") if planned else None,
        check("all declared dependencies reference planned tasks", not deps_missing, ", ".join(deps_missing)),
        check("orchestration reports no failed tasks", not failed, f"{len(failed)} failed", "warn"),
        check("pipeline status is successful", str(d.get("status","completed")).lower() not in {"failed","error","partial"},
              str(d.get("status","unknown")), "warn")
    ]
    return {"rows":rows,"metrics":metrics,"checks":[x for x in checks if x],
            "metrics_used":["orchestration_quality", "task_completion_rate", "workflow_success_rate",
                            "dependency_integrity", "failure_rate", "retry_rate", "execution_duration"],
            "orchestration_quality":metrics}


@evaluator("rag")
def eval_rag(path, d, ctx, judge, cfg):
    docs = d.get("retrieval_results") or d.get("retrieved_documents") or d.get("retrieved_chunks") or []
    docs = dict_items(docs)
    query = str(d.get("query") or d.get("user_query") or d.get("retrieval_query") or "")
    ids = [str(x.get("document_id") or x.get("chunk_id") or x.get("id") or "") for x in docs]
    raw_scores = [rag_document_score(x) for x in docs]
    valid_scores = [
        float(score) for score in raw_scores
        if is_finite_num(score) and 0 <= float(score) <= 1
    ]
    invalid_scores = [
        score for score in raw_scores
        if score is not None and (
            not is_finite_num(score) or not 0 <= float(score) <= 1
        )
    ]
    scores = [float(score) for score in raw_scores if is_finite_num(score)]
    texts = [
        str(x.get("text") or x.get("content") or x.get("page_content")
            or x.get("excerpt") or x.get("extracted_fact") or "")
        for x in docs
    ]
    nonempty = sum(bool(x.strip()) for x in texts)
    normalized = [norm(x) for x in texts if x.strip()]
    duplicate_pairs = sum(1 for i in range(len(normalized)) for j in range(i+1,len(normalized))
                          if near_dup(normalized[i],normalized[j]))
    source_names = [domain(x.get("source_url") or x.get("url") or x.get("source")) for x in docs]
    unique_sources = len({x for x in source_names if x})
    rows = []
    for i, doc in enumerate(docs,1):
        text = str(doc.get("text") or doc.get("content") or doc.get("page_content")
                   or doc.get("excerpt") or doc.get("extracted_fact") or "")
        score = rag_document_score(doc)
        rows.append({"unit_type":"retrieved_chunk","unit_index":i,
                     "unit_id":str(doc.get("chunk_id") or doc.get("document_id") or doc.get("id") or i),
                     "query":query,"statement":text[:500],"retrieval_score":score,
                     "source":doc.get("source_url") or doc.get("url") or doc.get("source"),
                     "flags":(["empty_retrieved_chunk"] if not text.strip() else []) +
                     (["low_retrieval_score"] if is_num(score) and score < 0.3 else []) +
                     (["invalid_retrieval_score"] if score is not None and
                      (not is_finite_num(score) or not 0 <= float(score) <= 1) else [])})
    answer = next(
        (
            str(d[key]).strip()
            for key in ("answer", "response", "actual_output", "output")
            if d.get(key) and str(d[key]).strip()
        ),
        "",
    )
    answer_question = str(d.get("answer_question") or query).strip()
    expected_output = next(
        (
            str(d[key]).strip()
            for key in ("expected_output", "reference_answer", "ground_truth")
            if d.get(key) and str(d[key]).strip()
        ),
        "",
    )
    rag_judges = {
        metric: {"score": None, "reason": "", "error": "not requested"}
        for metric in ("context_precision", "context_recall", "context_relevancy")
    }
    judge_rag_metrics = bool(query and docs and getattr(judge, "enabled", False))
    if judge_rag_metrics:
        context = texts[:MAX_CTX_ITEMS]
        rag_judges["context_relevancy"] = judge.run(
            "context_relevancy",
            query,
            answer or query,
            context,
            getattr(cfg, "thr", {}).get("context_relevancy", 0.5),
        )
        if expected_output:
            for metric in ("context_precision", "context_recall"):
                rag_judges[metric] = judge.run(
                    metric,
                    query,
                    answer or expected_output,
                    context,
                    getattr(cfg, "thr", {}).get(metric, getattr(cfg, "thr", {}).get("context_relevancy", 0.5)),
                    expected_output=expected_output,
                )
        else:
            for metric in ("context_precision", "context_recall"):
                rag_judges[metric]["error"] = "not assessable: expected output/reference answer is missing"
    elif not getattr(judge, "enabled", False):
        for metric in ("context_relevancy",):
            rag_judges[metric]["error"] = "not requested: LLM judge is disabled"
    elif not query or not docs:
        for metric in ("context_relevancy",):
            rag_judges[metric]["error"] = "not assessable: query or retrieved context is missing"
    if not expected_output:
        for metric in ("context_precision", "context_recall"):
            rag_judges[metric]["error"] = "not assessable: expected output/reference answer is missing"
    telemetry_calls = getattr(ctx, "telemetry_calls", [])
    task_id = str(d.get("task_id") or "")
    attempt = d.get("attempt")
    retrieval_calls = [
        call for call in telemetry_calls
        if str(call.get("tool") or "").casefold() == "internal_rag.retrieve"
        and (not task_id or str(call.get("task_id") or "") == task_id)
        and (attempt is None or call.get("attempt") is None or str(call.get("attempt")) == str(attempt))
        and _call_query(call) == query
    ]
    retrieval_latencies = [
        float(value) for call in retrieval_calls
        for value in [call.get("latency_ms", call.get("duration_ms"))]
        if is_finite_num(value) and float(value) >= 0
    ]
    metrics = {"queries":1 if query else 0,"retrieved_chunks":len(docs),
               "nonempty_chunks_pct":pct(nonempty,len(docs)),"mean_retrieval_score":mean(valid_scores),
               "chunks_with_score_pct":pct(sum(score is not None for score in raw_scores),len(docs)),
               "invalid_retrieval_scores":len(invalid_scores),"unique_source_domains":unique_sources,
               "duplicate_chunk_pairs":duplicate_pairs,"empty_chunks":len(docs)-nonempty,
               "context_precision_score":rag_judges["context_precision"].get("score"),
               "context_precision_reason":rag_judges["context_precision"].get("reason"),
               "context_precision_error":rag_judges["context_precision"].get("error"),
               "expected_output_source":(
                   d.get("expected_output_source") or ("RAG record" if expected_output else None)
               ),
               "context_recall_score":rag_judges["context_recall"].get("score"),
               "context_recall_reason":rag_judges["context_recall"].get("reason"),
               "context_recall_error":rag_judges["context_recall"].get("error"),
               "context_relevancy_score":rag_judges["context_relevancy"].get("score"),
               "context_relevancy_reason":rag_judges["context_relevancy"].get("reason"),
               "context_relevancy_error":rag_judges["context_relevancy"].get("error"),
               "answer_source":d.get("answer_source") or ("recorded_answer" if answer else None),
               "retrieval_latency_ms":mean(retrieval_latencies),
               "retrieval_latency_calls":len(retrieval_latencies)}
    if answer:
        rag_row = {
            "unit_type": "rag_answer",
            "unit_index": 1,
            "unit_id": str(d.get("answer_id") or "answer"),
            "query": query,
            "statement": answer[:500],
            "answer_source": d.get("answer_source") or "recorded_answer",
            "answer_question": answer_question,
            "flags": [],
        }
        for metric, result in rag_judges.items():
            score = result.get("score")
            threshold = getattr(cfg, "thr", {}).get(metric, 0.5)
            rag_row[f"{metric}_score"] = score
            rag_row[f"{metric}_pass"] = None if score is None else score >= threshold
            rag_row[f"{metric}_reason"] = result.get("reason", "")
            rag_row[f"{metric}_note"] = result.get("error", "")
            if rag_row[f"{metric}_pass"] is False:
                rag_row["flags"].append(METRICS[metric][2])
        rows.append(rag_row)
    checks = [
        check("retrieval query is present",bool(query),"query missing"),
        check("retrieval returned at least one chunk",bool(docs),"no retrieved documents","warn"),
        check("retrieved chunks contain text",nonempty==len(docs),f"{len(docs)-nonempty} empty","warn") if docs else None,
        check("retrieval scores are available for observability",len(scores)==len(docs),
              f"{len(scores)}/{len(docs)} have scores","warn") if docs else None,
        check("retrieval scores are within [0, 1]",not invalid_scores,
              f"{len(invalid_scores)} invalid score(s): {invalid_scores}","warn") if docs else None,
        check("retrieved chunks are not near-duplicates",duplicate_pairs==0,f"{duplicate_pairs} duplicate pairs","warn")
    ]
    context_score = rag_judges["context_relevancy"].get("score")
    if context_score is not None:
        checks.append(
            check(
                "retrieved context is relevant to the query",
                context_score >= getattr(cfg, "thr", {}).get("context_relevancy", 0.5),
                str(rag_judges["context_relevancy"].get("reason") or context_score),
                "warn",
            )
        )
    for metric in ("context_precision", "context_recall"):
        score = rag_judges[metric].get("score")
        if score is not None:
            checks.append(
                check(
                    f"retrieved context {metric.replace('_', ' ')} meets threshold",
                    score >= getattr(cfg, "thr", {}).get(metric, getattr(cfg, "thr", {}).get("context_relevancy", 0.5)),
                    str(rag_judges[metric].get("reason") or score),
                    "warn",
                )
            )
    rag_metrics_used = ["rag_retrieval_quality"]
    if judge_rag_metrics:
        rag_metrics_used.extend(["context_relevancy"])
        if expected_output:
            rag_metrics_used.extend(["context_precision", "context_recall"])
    if retrieval_latencies:
        rag_metrics_used.append("retrieval_latency")
    return {"rows":rows,"metrics":metrics,"checks":[x for x in checks if x],
            "metrics_used":rag_metrics_used,"rag_quality":metrics}


@evaluator("telemetry")
def eval_telemetry(path, d, ctx, judge, cfg):
    spans = _walk_records(d, ("spans","trace_events","observability_events","tool_traces","events"))
    explicit_tool_calls = _walk_records(d, ("tool_calls","tools_used","tool_events"))
    tool_calls, _ = _execution_calls(d)
    worker_spans = [
        span for span in spans
        if (span.get("attributes") or {}).get("edrak.agent.type") or span.get("name") == "edrak.worker.run"
    ]
    worker_identity_missing = [
        span for span in worker_spans
        if not (span.get("attributes") or {}).get("edrak.agent.type")
        or not (span.get("attributes") or {}).get("edrak.task.id")
    ]
    worker_process_missing = [
        span for span in worker_spans
        if str(span.get("status") or "").lower() not in {"ok", "success", "completed"}
        or not is_num(span.get("duration_ms"))
        or not is_finite_num(span.get("duration_ms"))
        or float(span.get("duration_ms", 0)) < 0
    ]
    tool_context_by_span = {
        str(call.get("span_id")): call for call in tool_calls if call.get("span_id")
    }
    error_records = spans + explicit_tool_calls
    errors = [x for x in error_records if x.get("error") or str(x.get("status","")).lower() in {"error","failed"}]
    durations = []
    for x in spans + explicit_tool_calls:
        value = x.get("duration_ms", x.get("latency_ms"))
        if is_num(value): durations.append(float(value))
        elif is_num(x.get("duration")): durations.append(float(x["duration"])*1000)
    ordered_durations = sorted(durations)
    p95_duration = round(ordered_durations[math.ceil(0.95 * len(ordered_durations)) - 1], 2) if ordered_durations else None
    names = [
        str(x.get("tool") or x.get("tool_name") or (x.get("attributes") or {}).get("edrak.tool.name") or x.get("name") or "")
        for x in tool_calls
    ]
    metrics = {"spans":len(spans),"tool_calls":len(tool_calls),"observed_errors":len(errors),
               "worker_spans":len(worker_spans),
               "worker_task_identity_pct":pct(len(worker_spans)-len(worker_identity_missing),len(worker_spans)),
               "worker_process_recorded_pct":pct(len(worker_spans)-len(worker_process_missing),len(worker_spans)),
               "worker_types":sorted({
                   str((span.get("attributes") or {}).get("edrak.agent.type"))
                   for span in worker_spans
                   if (span.get("attributes") or {}).get("edrak.agent.type")
               }),
               "error_rate_pct":pct(len(errors),len(error_records)),
               "duration_samples":len(durations),
               "mean_duration_ms":mean(durations),"p95_duration_ms":p95_duration,
               "max_duration_ms":round(max(durations), 2) if durations else None,
               "unique_tools":len({n for n in names if n}),"tool_names":sorted({n for n in names if n})}
    rows = []
    for i, x in enumerate(spans + explicit_tool_calls, 1):
        attrs = x.get("attributes") or {}
        tool_context = tool_context_by_span.get(str(x.get("span_id") or ""), {})
        is_worker_span = x in worker_spans
        flags = (["execution_error"] if x.get("error") or str(x.get("status", "")).lower() in {"error", "failed"} else [])
        if is_worker_span:
            if not attrs.get("edrak.agent.type") or not attrs.get("edrak.task.id"):
                flags.append("missing_worker_task_identity")
            if x in worker_process_missing:
                flags.append("incomplete_worker_process_trace")
        rows.append({
            "unit_type": "worker_trace" if is_worker_span else "trace_event",
            "unit_index": i,
            "unit_id": str(x.get("span_id") or x.get("id") or i),
            "worker": attrs.get("edrak.agent.type") or tool_context.get("worker"),
            "task_id": attrs.get("edrak.task.id") or tool_context.get("task_id"),
            "task_attempt": attrs.get("edrak.task.attempt"),
            "statement": str(attrs.get("edrak.tool.name") or x.get("name") or x.get("tool") or x.get("event") or x.get("type") or ""),
            "status": x.get("status"),
            "duration_ms": x.get("duration_ms", x.get("latency_ms")),
            "error": x.get("error"),
            "flags": flags,
        })
    metrics["units"] = len(rows)
    metrics["units_flagged"] = sum(bool(row["flags"]) for row in rows)
    checks = [check("telemetry includes events or spans",bool(spans or explicit_tool_calls),"no recognizable spans/tool calls","warn"),
              check("observed tool and span errors are recorded",not errors,f"{len(errors)} error events","warn")]
    checks.append(
        check(
            "worker spans carry agent and task identity",
            bool(worker_spans) and not worker_identity_missing,
            f"{len(worker_identity_missing)}/{len(worker_spans)} worker span(s) missing agent or task id"
            if worker_spans else "no worker.run spans available",
            "warn",
        )
    )
    checks.append(
        check(
            "worker spans record process status and duration",
            bool(worker_spans) and not worker_process_missing,
            f"{len(worker_process_missing)}/{len(worker_spans)} worker span(s) missing success status or valid duration"
            if worker_spans else "no worker.run spans available",
            "warn",
        )
    )
    execution = eval_execution_trace(
        d, getattr(ctx, "goal", ""), "", judge, getattr(cfg, "include_tool_payloads", False)
    )
    metrics["execution_quality_status"] = execution["status"]
    return {"rows":rows,"metrics":metrics,"checks":[*checks, *execution["checks"]],
            "metrics_used":["observability", "execution_quality"],"execution_quality":execution}


# =============================================================================== 5. pipeline-level checks
def pipeline_checks(ctx: Ctx, per_agent: dict[str, list[dict]], decision_files: list[dict],
                    loaded: list[tuple[Path, str, dict]]) -> dict[str, Any]:
    """End-to-end traceability: decision -> signal -> finding -> evidence. Each check runs only if its stages exist.
    `ctx` is the FINAL state of the run (latest worker attempts, latest verification, latest signals)."""
    checks, detail = [], {}
    findings = ctx.verified_findings or ctx.worker_findings

    if findings and ctx.evidence:
        bad = [fid for fid, f in findings.items() if not any(e in ctx.evidence for e in f.get("evidence_ids") or [])]
        detail["findings_without_resolvable_evidence"] = bad
        checks.append(check("every finding traces to evidence", not bad, f"{len(bad)} untraceable"))

    if ctx.signals and findings:
        bad = [sid for sid, s in ctx.signals.items() if not any(f in findings for f in s.get("support", []))]
        detail["signals_without_findings"] = bad
        checks.append(check("every signal traces to a finding", not bad, f"{len(bad)} untraceable"))
        if any(f.get("verification_status") for f in ctx.verified_findings.values()):
            weak = [sid for sid, s in ctx.signals.items()
                    if any(ctx.verified_findings.get(f, {}).get("verification_status") != "verified" for f in s["support"])]
            detail["signals_resting_on_unverified_findings"] = weak
            checks.append(check("signals only rest on verified findings", not weak, f"{len(weak)} signals", "warn"))

    if decision_files and ctx.signals and findings:
        total = traced = 0
        for dfile in decision_files:
            for qr in dict_items(dfile.get("question_recommendations")):
                ids = [str(x) for x in (qr.get("recommendation") or {}).get("supporting_signal_ids") or []]
                total += 1
                traced += bool(ids) and all(x in ctx.signals and any(f in findings for f in ctx.signals[x]["support"])
                                            for x in ids)
        detail["recommendations_fully_traceable_pct"] = pct(traced, total)
        checks.append(check("every recommendation traces to signal -> finding", traced == total, f"{traced}/{total}"))

    if ctx.worker_findings and ctx.verified_findings:
        miss = set(ctx.worker_findings) - set(ctx.verified_findings)
        checks.append(check("verifier covers all worker findings", not miss, f"{len(miss)} missing"))

    run_ids = {str(d.get("research_run_id")) for _, k, d in loaded if k in ("verification", "cross_signal", "decision")
               and d.get("research_run_id")}
    if run_ids:
        checks.append(check("verification, cross-signal and decision share one research_run_id", len(run_ids) == 1,
                            ", ".join(sorted(run_ids))))

    reruns = rerun_report(loaded)
    for w in reruns["workers"]:
        checks.append(check(f"re-run of {w['worker']} keeps finding ids stable only for unchanged statements",
                            w["ids_reused_with_changed_statement"] == 0,
                            f"{w['ids_reused_with_changed_statement']} of {w['ids_reused']} reused ids now carry a different statement",
                            "warn"))
    for v in reruns["verification"]:
        checks.append(check("second verification pass does not downgrade previously verified findings",
                            v["verified_to_insufficient"] == 0, f"{v['verified_to_insufficient']} downgraded", "warn"))

    detail["synthetic_evidence_pct_overall"] = pct(sum(is_synthetic(e) for e in ctx.evidence.values()), len(ctx.evidence))
    detail["agents_evaluated"] = sorted(per_agent)
    detail["worker_attempts_used_by_final_state"] = ctx.worker_attempt
    return {"checks": checks, "detail": detail, "reruns": reruns}


def _by_id(items: Any) -> dict[str, dict]:
    return {str(f["finding_id"]): f for f in dict_items(items) if f.get("finding_id")}


def rerun_report(loaded: list[tuple[Path, str, dict]]) -> dict[str, Any]:
    """Phase 1 -> phase 2 comparison for every worker / verification pass that ran more than once.
    Finding ids are reused across attempts, so statements (not ids) say what really changed."""
    out: dict[str, list] = {"workers": [], "verification": []}
    by_worker: dict[str, list] = defaultdict(list)
    for p, k, d in loaded:
        if k == "worker":
            by_worker[d["worker"]].append((p, d))
    for w, items in by_worker.items():
        items.sort(key=lambda x: (int(x[1].get("attempt") or 1), file_time("worker", x[1]) or EPOCH))
        for (pa, a), (pb, b) in zip(items, items[1:]):
            fa, fb = _by_id(a.get("findings")), _by_id(b.get("findings"))
            common = set(fa) & set(fb)
            sa, sb = {norm(f["statement"]) for f in fa.values()}, {norm(f["statement"]) for f in fb.values()}
            summ = lambda d, f: {"file": None, "attempt": d.get("attempt"), "status": d.get("status"), "findings": len(f),
                                 "evidence": len(d.get("evidence") or []), "gaps": len(d.get("gaps") or []),
                                 "agent_confidence": conf_num(d.get("confidence")),
                                 "avg_finding_confidence": mean([conf_num(x.get("confidence")) for x in f.values()])}
            out["workers"].append({
                "worker": w, "before": {**summ(a, fa), "file": pa.name}, "after": {**summ(b, fb), "file": pb.name},
                "statements_kept": len(sa & sb), "statements_new": len(sb - sa), "statements_dropped": len(sa - sb),
                "ids_reused": len(common),
                "ids_reused_with_changed_statement": sum(norm(fa[i]["statement"]) != norm(fb[i]["statement"]) for i in common)})
    ver = sorted([(p, d) for p, k, d in loaded if k == "verification"],
                 key=lambda x: file_time("verification", x[1]) or EPOCH)
    for (pa, a), (pb, b) in zip(ver, ver[1:]):
        fa, fb = _by_id(a.get("findings")), _by_id(b.get("findings"))
        common = set(fa) & set(fb)
        same = [i for i in common if norm(fa[i]["statement"]) == norm(fb[i]["statement"])]
        st = lambda f, i: str(f[i].get("verification_status")).lower()
        stats = lambda d, f: {"status": (d.get("decision") or {}).get("status"), "findings": len(f),
                              "verified": sum(str(x.get("verification_status")).lower() == "verified" for x in f.values()),
                              "conflicts": len((d.get("control_summary") or {}).get("conflicts") or []),
                              "gaps": len((d.get("control_summary") or {}).get("missing_information") or []),
                              "avg_confidence": mean([conf_num(x.get("confidence")) for x in f.values()])}
        sa, sb = stats(a, fa), stats(b, fb)
        out["verification"].append({
            "before": {**sa, "file": pa.name, "verified_pct": pct(sa["verified"], sa["findings"])},
            "after": {**sb, "file": pb.name, "verified_pct": pct(sb["verified"], sb["findings"])},
            "findings_dropped": len(set(fa) - set(fb)), "findings_added": len(set(fb) - set(fa)),
            "ids_reused_with_changed_statement": len(common) - len(same),
            "insufficient_to_verified": sum(st(fa, i) != "verified" and st(fb, i) == "verified" for i in same),
            "verified_to_insufficient": sum(st(fa, i) == "verified" and st(fb, i) != "verified" for i in same)})
    return out


# =============================================================================== 5b. optional LLM narrative
NARRATIVE_PROMPT = (
    "You review the evaluation results of a multi-agent competitive-intelligence pipeline. "
    "You get a JSON digest of integrity checks, flags and metrics per agent. Write a concise diagnosis: "
    "(1) the 3-5 most serious problems, most serious first, naming the agent; "
    "(2) for each, the likely cause and one concrete fix. "
    "Use ONLY facts and numbers present in the digest. Do not invent or recompute figures. Under 250 words."
)


def digest(per_agent: dict[str, list[dict]], pipeline: dict) -> dict:
    """Computed numbers only: this is all the narrative LLM is allowed to talk about."""
    out: dict[str, Any] = {"agents": {}, "pipeline_checks": pipeline.get("checks", [])}
    for agent, results in per_agent.items():
        out["agents"][agent] = [{
            "file": r["file"],
            "checks_failed": [{"check": c["check"], "detail": c["detail"]} for c in r["checks"] if not c["passed"]],
            "checks_total": len(r["checks"]),
            "flag_counts": dict(Counter(f for u in r["rows"] for f in u.get("flags", []))),
            "metrics": {k: v for k, v in r["metrics"].items() if isinstance(v, (int, float, str))},
        } for r in results]
    return out


def write_narrative(model: str, per_agent: dict, pipeline: dict) -> str:
    """Ask the LLM for a short diagnosis; flag any figure it states that is not in the computed digest."""
    from openai import OpenAI
    dg = json.dumps(digest(per_agent, pipeline), ensure_ascii=False, default=str)
    resp = OpenAI().chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": NARRATIVE_PROMPT}, {"role": "user", "content": dg}])
    text = (resp.choices[0].message.content or "").strip()
    stray = ungrounded_numbers(text, [dg])
    if stray:
        text += f"\n\n_Note: figures not found in the computed digest, treat with caution: {', '.join(stray)}_"
    return text


# =============================================================================== 6. the evaluation agent (runner + reports)
class EvalAgent:
    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.cfg = Cfg(args)
        self.judge = Judge(
            args.model, Path(args.cache), enabled=not args.dry_run,
            retries=getattr(args, "retries", 2), workers=getattr(args, "workers", 1),
        )
        self.loaded: list[tuple[Path, str, dict]] = []
        self.errors: list[dict] = []
        self.per_agent: dict[str, list[dict]] = {}
        self.pipeline: dict = {"checks": [], "detail": {}}
        self.chain: list[dict] = []
        self.narrative = ""

    @staticmethod
    def expand(patterns: list[str]) -> list[Path]:
        out = []
        for p in patterns:  # Windows shells do not expand globs
            hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
            out.extend(Path(h) for h in hits)
        return out

    def load(self) -> bool:
        for p in self.expand(self.a.inputs):
            try:
                d = read_json(p)
                self.loaded.append((p, detect(d), d))
                kind = self.loaded[-1][1]
                label = f"worker ({d['worker']}, attempt {d.get('attempt', 1)})" if kind == "worker" else kind
                print(f"  {p.name}: {label}")
            except Exception as exc:
                self.errors.append({"file": str(p), "error": f"{type(exc).__name__}: {exc}"})
                print(f"  ERROR {p}: {exc}", file=sys.stderr)
        return bool(self.loaded)

    def keep_final(self) -> None:
        """Keep worker outputs referenced by the final orchestration result and the last downstream stages."""
        latest: dict[str, Path] = {}
        best: dict[str, tuple] = {}
        orchestration = next(
            (
                d
                for _, kind, d in reversed(self.loaded)
                if kind == "orchestration" and isinstance(d.get("results"), list)
            ),
            None,
        )
        worker_paths: set[Path] = set()

        for p, kind, d in self.loaded:
            if kind == "worker":
                if orchestration is not None:
                    continue
                key, rank = f"worker:{d.get('worker')}", (
                    int(d.get("attempt") or 1),
                    file_time(kind, d) or EPOCH,
                    p.name,
                )
            elif kind in {"verification", "cross_signal", "decision", "metrics"}:
                key, rank = kind, (0, file_time(kind, d) or EPOCH, p.name)
            else:
                continue
            if key not in best or rank > best[key]:
                best[key], latest[key] = rank, p

        if orchestration is not None:
            final_worker_results = {
                str(result.get("worker")): (
                    str(result.get("task_id")),
                    str(result["attempt"]) if result.get("attempt") is not None else None,
                )
                for result in dict_items(orchestration.get("results"))
                if result.get("worker") and result.get("task_id")
            }
            for p, kind, d in self.loaded:
                if kind != "worker":
                    continue
                expected = final_worker_results.get(str(d.get("worker")))
                if expected is None:
                    continue
                task_id, attempt = expected
                if str(d.get("task_id")) != task_id:
                    continue
                if attempt is not None and str(d.get("attempt") or 1) != attempt:
                    continue
                rank = (file_time(kind, d) or EPOCH, p.name)
                key = str(d.get("worker"))
                if key not in best or rank > best[key]:
                    best[key], latest[key] = rank, p
            worker_paths = {latest[key] for key in final_worker_results if key in latest}
        else:
            worker_paths = {path for key, path in latest.items() if key.startswith("worker:")}

        final_tasks = {
            str(d.get("task_id"))
            for p, kind, d in self.loaded
            if kind == "worker" and p in worker_paths and d.get("task_id")
        }
        kept = []
        for p, kind, d in self.loaded:
            if kind == "worker" and p not in worker_paths:
                continue
            if kind in {"verification", "cross_signal", "decision", "metrics"} and latest.get(kind) != p:
                continue
            if kind == "rag" and d.get("task_id") and str(d["task_id"]) not in final_tasks:
                continue
            kept.append((p, kind, d))
        print(f"Final-only: evaluating {len(kept)} of {len(self.loaded)} files "
              f"({len(self.loaded) - len(kept)} superseded files skipped).")
        self.loaded = kept

    def not_evaluated(self) -> list[str]:
        """Downstream agents with no evaluated file. Without them insight (cross_signal) and the recommendation-level
        actionability (decision) are not scored, and the end-to-end trace is incomplete."""
        if self.loaded and all(kind == "telemetry" for _, kind, _ in self.loaded):
            return []
        wanted = {"verification", "cross_signal", "decision"}
        if self.a.agents:
            wanted &= set(self.a.agents)
        return sorted(wanted - set(self.per_agent))

    def run(self) -> int:
        try:
            return self._run()
        finally:
            self.judge.flush()   # keep judged results even if the run is interrupted

    def _run(self) -> int:
        final = build_ctx(self.loaded, self.a.task)
        has_output_evaluation = any(
            kind in {"worker", "verification", "cross_signal", "decision"}
            for _, kind, _ in self.loaded
        )
        if has_output_evaluation and not final.goal:
            print("WARNING: no research goal found (include the initial-input file or pass --task); "
                  "relevancy scores will be weak.", file=sys.stderr)
        # evaluate in pipeline order, so the console reads like the run
        order = {"worker": 0, "metrics": 1, "verification": 2, "cross_signal": 3, "decision": 4}
        for p, kind, d in sorted(self.loaded, key=lambda x: (order.get(x[1], 9), file_time(x[1], x[2]) or EPOCH)):
            if kind == "input":
                continue
            if kind == "metrics":
                print(f"Skipped {p.name}: metrics-stage evaluation is disabled")
                continue
            if kind not in EVALUATORS:
                print(f"Skipped {p.name}: unrecognised schema")
                continue
            label = d["worker"] if kind == "worker" else kind
            if self.a.agents and kind not in self.a.agents and label not in self.a.agents:
                continue
            ctx = build_ctx(self.loaded, self.a.task, as_of=file_time(kind, d))   # what existed when this file was made
            evaluation_data = d
            if kind == "rag" and not any(
                d.get(key) and str(d[key]).strip()
                for key in ("expected_output", "reference_answer", "ground_truth")
            ):
                golden_answer = _golden_answer_for_rag(d, self.loaded)
                if golden_answer:
                    expected_output, source = golden_answer
                    evaluation_data = {
                        **evaluation_data,
                        "expected_output": expected_output,
                        "expected_output_source": source,
                    }
            try:
                res = EVALUATORS[kind](p, evaluation_data, ctx, self.judge, self.cfg)
            except Exception as exc:  # one broken file must not stop the others
                self.errors.append({"file": p.name, "error": f"evaluator crashed: {type(exc).__name__}: {exc}"})
                print(f"  ERROR evaluating {p.name}: {exc}", file=sys.stderr)
                continue
            if kind not in {"telemetry", "metrics", *EXECUTION_QUALITY_EXCLUDED}:
                metadata = evaluation_data.get("metadata") or {}
                goal = (ctx.task_goals.get(str(evaluation_data.get("worker") or ""))
                        or metadata.get("research_goal") or ctx.goal or "")
                output_parts = [
                    str(item.get(key) or "")
                    for key in ("findings", "signals", "question_recommendations")
                    for item in dict_items(evaluation_data.get(key))
                ]
                output_parts.extend(
                    str(evaluation_data.get(key) or "")
                    for key in ("answer", "response", "output", "final_output", "summary")
                    if isinstance(evaluation_data.get(key), str)
                )
                exec_payload = evaluation_data
                if kind != "orchestration" and not _execution_calls(evaluation_data)[0]:
                    telemetry_calls = self.telemetry_calls_for(evaluation_data)
                    if telemetry_calls:
                        trace = evaluation_data.get("execution_trace")
                        if not isinstance(trace, dict):
                            metadata = evaluation_data.get("metadata") or {}
                            trace = metadata.get("execution_trace")
                            if not isinstance(trace, dict):
                                trace = metadata.get("provenance") or evaluation_data.get("provenance")
                        trace = trace if isinstance(trace, dict) else {}
                        exec_payload = {**d, "execution_trace": {**trace, "calls": telemetry_calls}}
                execution = eval_execution_trace(
                    exec_payload, goal, "\n".join(part for part in output_parts if part),
                    self.judge, self.cfg.include_tool_payloads,
                )
                res["execution_quality"] = execution
                res["metrics"]["execution_quality_status"] = execution["status"]
                res["checks"].extend(execution["checks"])
                res["metrics_used"] = list(dict.fromkeys([*res.get("metrics_used", []), "execution_quality"]))
            res["rows"] = [{"file": p.name, "agent": label, **r} for r in res["rows"]]
            res["file"] = p.name
            self.per_agent.setdefault(label, []).append(res)
            if kind not in {"worker", "telemetry"}:
                self.chain.append({"file": p.name, "agent": kind, "worker_attempts_seen": ctx.worker_attempt,
                                   "verification_seen": ctx.verification_file, "cross_signal_seen": ctx.cross_signal_file})
            err = sum(c["passed"] for c in res["checks"] if c["severity"] == "error")
            n_err = sum(c["severity"] == "error" for c in res["checks"])
            warn = sum(not c["passed"] for c in res["checks"] if c["severity"] == "warn")
            if kind == "telemetry":
                metrics = res["metrics"]
                print(
                    f"[{label}] {p.name}: {metrics['spans']} spans, {metrics['tool_calls']} tool calls, "
                    f"{metrics['observed_errors']} observed errors, execution quality "
                    f"{metrics['execution_quality_status']}, {warn} warnings "
                    "(integrity checks: n/a)"
                )
            else:
                integrity = f"{err}/{n_err} integrity checks passed" if n_err else "integrity checks: n/a (none defined)"
                print(f"[{label}] {p.name}: {len(res['rows'])} units, {integrity}, {warn} warnings")
        if not self.per_agent:
            print("ERROR: nothing to evaluate.", file=sys.stderr)
            return 2
        missing = self.not_evaluated()
        if missing:
            print(f"NOTE: not evaluated (no input files): {', '.join(missing)}. "
                  f"Signal 'insight' and recommendation 'actionability' need the cross-signal and decision JSONs.")
        self.pipeline = pipeline_checks(final, self.per_agent, [d for _, k, d in self.loaded if k == "decision"], self.loaded)
        self.pipeline["detail"]["stage_inputs"] = self.chain
        self.pipeline["detail"]["not_evaluated"] = missing
        if self.a.narrative and not self.a.dry_run:
            try:
                self.narrative = write_narrative(self.a.model, self.per_agent, self.pipeline)
            except Exception as exc:
                print(f"narrative failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        self.write_all()
        integrity_failures = [
            check_result
            for results in self.per_agent.values()
            for result in results
            for check_result in result["checks"]
            if check_result["severity"] == "error" and not check_result["passed"]
        ]
        integrity_failures.extend(
            item for item in self.pipeline.get("checks", [])
            if item["severity"] == "error" and not item["passed"]
        )
        if integrity_failures:
            print(
                f"ERROR: {len(integrity_failures)} deterministic integrity check(s) failed.",
                file=sys.stderr,
            )
        return 1 if self.errors or integrity_failures else 0

    # -- reports
    def telemetry_calls_for(self, worker_payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Tool calls the runtime telemetry recorded for this task attempt."""
        task_id = str(worker_payload.get("task_id") or "")
        attempt = worker_payload.get("attempt")
        if not task_id:
            return []
        retrieval_query = ""
        if detect(worker_payload) == "rag":
            retrieval_query = str(
                worker_payload.get("query")
                or worker_payload.get("user_query")
                or worker_payload.get("retrieval_query")
                or ""
            ).strip()
        matched: list[dict[str, Any]] = []
        for _, kind, payload in self.loaded:
            if kind != "telemetry":
                continue
            for call in _execution_calls(payload)[0]:
                if str(call.get("task_id") or "") != task_id:
                    continue
                if (attempt is not None and call.get("attempt") is not None
                        and str(call["attempt"]) != str(attempt)):
                    continue
                if retrieval_query and (
                    str(call.get("tool") or "").casefold() != "internal_rag.retrieve"
                    or _call_query(call) != retrieval_query
                ):
                    continue
                matched.append(call)
        return matched

    def metrics_in_use(self) -> list[str]:
        used = [m for res in sum(self.per_agent.values(), []) for m in res.get("metrics_used", [])]
        return [m for m in METRICS if m in used]

    def write_all(self) -> None:
        out = Path(self.a.out)
        out.mkdir(parents=True, exist_ok=True)
        for obsolete in ("metrics_report.json", "metrics_units.csv"):
            stale_report = out / obsolete
            if stale_report.is_file():
                stale_report.unlink()
        overview = []
        for agent, results in self.per_agent.items():
            write_csv(out / f"{agent}_units.csv", [r for res in results for r in res["rows"]])
            used = {m for res in results for m in res.get("metrics_used", [])}    # only thresholds that were applied
            report = {
                "agent": agent, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "model": None if self.a.dry_run else self.a.model, "dry_run": self.a.dry_run,
                "thresholds": {m: t for m, t in self.cfg.thr.items() if m in used},
                "files": [{"file": res["file"], "metrics_used": res.get("metrics_used"), "metrics": res["metrics"],
                           **({"execution_quality": res["execution_quality"]}
                              if res.get("execution_quality") is not None else {}),
                           "checks": res["checks"],
                           "flagged_units": [{k: r.get(k) for k in ("unit_type", "unit_id", "statement", "flags")}
                                             for r in res["rows"] if r.get("flags")]} for res in results]}
            (out / f"{agent}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                      encoding="utf-8")
            for res in results:
                bad = [c for c in res["checks"] if not c["passed"]]
                overview.append({"agent": agent, "file": res["file"],
                                 "integrity_passed": sum(c["passed"] for c in res["checks"] if c["severity"] == "error"),
                                 "integrity_total": sum(c["severity"] == "error" for c in res["checks"]),
                                 "warnings": sum(c["severity"] == "warn" for c in bad),
                                 "failed_checks": "; ".join(c["check"] for c in bad if c["severity"] == "error"),
                                 "warning_checks": "; ".join(c["check"] for c in bad if c["severity"] == "warn"),
                                 **{k: v for k, v in res["metrics"].items() if not isinstance(v, (dict, list))}})
        write_csv(out / "overview.csv", overview)
        (out / "pipeline_report.json").write_text(json.dumps(self.pipeline, ensure_ascii=False, indent=2, default=str),
                                                  encoding="utf-8")
        obs = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "judge": {"calls": self.judge.calls, "successful_calls": self.judge.successes,
                         "cache_hits": self.judge.hits, "errors": self.judge.errors,
                         "skipped": self.judge.skipped, "rate_limit_circuit_open": self.judge.rate_limit_open,
                         "total_latency_seconds": round(self.judge.total_latency_seconds, 4),
                         "calls_by_metric": dict(self.judge.metric_calls),
                         "failures_by_metric": dict(self.judge.metric_failures),
                         "skipped_by_metric": dict(self.judge.metric_skips)},
               "agent_files": {a: [_observability_entry(r, per_call=a != "telemetry") for r in rs]
                               for a, rs in self.per_agent.items()},
               "errors": self.errors}
        (out / "observability.json").write_text(
            json.dumps(_drop_nulls(obs), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (out / "summary.md").write_text(self.markdown(), encoding="utf-8")
        print(f"\nDone. Judge requests: {self.judge.calls}, successful scores: {self.judge.successes}, "
              f"cache hits: {self.judge.hits}, failed metrics: {self.judge.errors}, skipped: {self.judge.skipped}."
              f" Reports in {out}/ (start with summary.md)")

    def markdown(self) -> str:
        m_cols = self.metrics_in_use()
        L = ["# EDRAK evaluation summary", "",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} | "
             f"{'dry-run (no LLM scores)' if self.a.dry_run else 'judge model: ' + self.a.model}", "",
             f"**Agents present in this run:** {', '.join(sorted(self.per_agent))}", ""]
        missing = self.not_evaluated()
        if missing:
            L += [f"**Not evaluated (no input files):** {', '.join(missing)}", ""]
        if self.narrative:
            L += ["## Diagnosis (LLM-written from the computed results)", "", self.narrative, ""]
        L += ["## Scores per file", "",
              "| Agent | File | Execution quality | Integrity checks | Warnings | Units | Flagged | " + " | ".join(f"{m} avg" for m in m_cols) + " |",
              "|---|---|---|---|---|---|---|" + "---|" * len(m_cols)]
        for agent, results in self.per_agent.items():
            for r in results:
                m = r["metrics"]
                errs = [c for c in r["checks"] if c["severity"] == "error"]
                warn = sum(not c["passed"] for c in r["checks"] if c["severity"] == "warn")
                cell = lambda x: "-" if m.get(f"{x}_avg") is None else str(m[f"{x}_avg"])
                execution_status = (r.get("execution_quality") or {}).get("status", "-")
                integrity = f"{sum(c['passed'] for c in errs)}/{len(errs)}" if errs else "n/a"
                L.append(f"| {agent} | {r['file']} | {execution_status} | {integrity} | {warn} | {m.get('units')} | "
                         f"{m.get('units_flagged')} | " + " | ".join(cell(x) if x in r.get("metrics_used", []) else "n/a" for x in m_cols) + " |")
        L += ["", "## RAG / orchestration / observability", "",
              "- RAG inputs are checked for retrieval score coverage and range, empty/duplicate chunks; source-domain coverage is recorded. DeepEval scores contextual relevance and, when an answer is supplied, answer relevancy and faithfulness unless `--dry-run` is selected.",
              "- Orchestration inputs are evaluated for planned-task completion, dependency integrity, failed tasks, retries, and observed tool calls.",
              "- Worker and downstream-agent quality metrics use DeepEval unless `--dry-run` is selected; `--deep` also judges every recommended action.",
              "- Telemetry checks worker-run spans for agent/task identity and successful status/duration, and correlates tool spans with their worker when trace parentage is present.",
              "- Execution quality is reported for tool-using workers and RAG stages. Verification, cross-signal, decision, and orchestration stages are excluded because they are not judged as tool-execution stages.",
              "- Tool argument values and result summaries are excluded from DeepEval by default. Use `--include-tool-payloads` to send redacted payloads for argument and result-use judgments.",
              "- `observability.json` records judge calls, cache hits, judge failures, metric-level failures, evaluation latency, and per-file tool traces (provider, argument names/types/values, latency, results). Per-file metrics and failed checks are in each agent report.",
              "- The backend exports OpenTelemetry spans as evaluator-readable `spans` JSON. Set `EDRAK_TELEMETRY_EXPORT_PATH` to choose the file; set `OTEL_EXPORTER_OTLP_ENDPOINT` to additionally export to an OTLP collector.",
              "- Request, orchestration, RAG retrieval, document indexing, and customer-trends tool spans export timing/status metadata, plus safe tool names, providers, and argument names/types/values (secret-named keys redacted and long values truncated; set `EDRAK_TELEMETRY_ARGUMENT_VALUES=0` to omit values); document text and credentials are not exported.", ""]
        L += ["", "## Failed integrity checks (errors)", ""]
        fails = [f"- **{a}** / {r['file']}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
                 for a, rs in self.per_agent.items() for r in rs for c in r["checks"]
                 if not c["passed"] and c["severity"] == "error"]
        L += fails or ["None."]
        L += ["", "## Quality warnings", ""]
        warns = [f"- **{a}** / {r['file']}: {c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
                 for a, rs in self.per_agent.items() for r in rs for c in r["checks"]
                 if not c["passed"] and c["severity"] == "warn"]
        L += warns or ["None."]
        L += ["", "## Pipeline-level checks", ""]
        L += [f"- {'PASS' if c['passed'] else 'FAIL'}{'' if c['severity'] == 'error' else ' (warn)'}: {c['check']}"
              + (f" ({c['detail']})" if c["detail"] else "") for c in self.pipeline["checks"]] \
            or ["None applicable (not enough stages in this use case)."]
        rr = self.pipeline.get("reruns") or {}
        if rr.get("workers") or rr.get("verification"):
            L += ["", "## Re-run comparison (phase 1 -> phase 2)", ""]
            for w in rr.get("workers", []):
                b, a = w["before"], w["after"]
                L.append(f"- **{w['worker']}** attempt {b['attempt']} -> {a['attempt']}: findings {b['findings']} -> {a['findings']}, "
                         f"evidence {b['evidence']} -> {a['evidence']}, gaps {b['gaps']} -> {a['gaps']}, "
                         f"avg finding confidence {b['avg_finding_confidence']} -> {a['avg_finding_confidence']}; "
                         f"statements kept {w['statements_kept']}, new {w['statements_new']}, dropped {w['statements_dropped']}; "
                         f"{w['ids_reused_with_changed_statement']}/{w['ids_reused']} reused finding ids now mean something else")
            for v in rr.get("verification", []):
                b, a = v["before"], v["after"]
                L.append(f"- **verification** {b['file']} -> {a['file']}: status {b['status']} -> {a['status']}, "
                         f"verified {b['verified']}/{b['findings']} ({b['verified_pct']}%) -> {a['verified']}/{a['findings']} ({a['verified_pct']}%), "
                         f"conflicts {b['conflicts']} -> {a['conflicts']}, gaps {b['gaps']} -> {a['gaps']}; "
                         f"insufficient->verified {v['insufficient_to_verified']}, verified->insufficient {v['verified_to_insufficient']}")
        stages = self.pipeline.get("detail", {}).get("stage_inputs") or []
        if stages:
            L += ["", "## Which phase each downstream file was evaluated against", ""]
            L += [f"- {s['file']} ({s['agent']}): worker attempts {s['worker_attempts_seen']}, verification {s['verification_seen'] or '-'}, "
                  f"cross-signal {s['cross_signal_seen'] or '-'}" for s in stages]
        L += ["", "## Flagged units", ""]
        for agent, results in self.per_agent.items():
            for r in results:
                for u in r["rows"]:
                    if u.get("flags"):
                        s = (u.get("statement") or "")[:110].replace("\n", " ")
                        L.append(f"- [{agent}] {r['file']} {u.get('unit_type')} {u.get('unit_id')}: {', '.join(u['flags'])} — {s}")
        if self.errors:
            L += ["", "## Errors", ""] + [f"- {e['file']}: {e['error']}" for e in self.errors]
        return "\n".join(L) + "\n"


# =============================================================================== 7. CLI
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--inputs", nargs="+", help="JSON files or globs of ONE use case (all phases together)")
    source.add_argument("--run-bundle", help="Evaluate one backend run bundle created under artifacts/evaluation_runs")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--agents", nargs="+", help="Only evaluate these (kind or worker name, e.g. verification decision)")
    ap.add_argument("--task", default="", help="Override the research goal text")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), help="DeepEval judge model")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel judge calls per unit (1 = sequential)")
    ap.add_argument("--retries", type=int, default=2,
                    help="Attempts per judge metric before recording a failure")
    ap.add_argument("--faithfulness-threshold", type=float, default=0.7)
    ap.add_argument("--relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--specificity-threshold", type=float, default=0.5)
    ap.add_argument("--insight-threshold", type=float, default=0.5)
    ap.add_argument("--actionability-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy", action="store_true", help="Also judge whether cited evidence is relevant to the goal")
    metric_group = ap.add_mutually_exclusive_group()
    metric_group.add_argument("--extra-metrics", dest="extra_metrics", action="store_true",
                              help="Also run specificity, insight, and actionability LLM judges")
    metric_group.add_argument("--no-extra-metrics", dest="extra_metrics", action="store_false",
                              help=argparse.SUPPRESS)
    ap.set_defaults(extra_metrics=False)
    ap.add_argument("--cache", default="edrak_judge_cache.json")
    ap.add_argument("--dry-run", action="store_true", help="No LLM calls; deterministic checks only")
    ap.add_argument("--deep", action="store_true", help="Also judge each recommended action")
    ap.add_argument("--narrative", action="store_true", help="Add an LLM-written diagnosis to summary.md")
    ap.add_argument(
        "--include-tool-payloads",
        action="store_true",
        help="Send redacted tool arguments/results to DeepEval for argument and result-use judgments",
    )
    ap.add_argument("--all-attempts", action="store_true",
                    help="Evaluate every attempt and verification pass, not just the final one (default for --run-bundle is final only)")
    ap.add_argument("--compare-to", help="Compare the completed run bundle with this golden/ground_truth folder")
    ap.add_argument("--compare-out", help="Write the comparison report to this path (defaults under the evaluation output directory)")
    ap.add_argument("--compare-no-llm", action="store_true", help="Use word overlap instead of the LLM comparison judge")
    a = ap.parse_args()

    if a.compare_to and not a.run_bundle:
        ap.error("--compare-to requires --run-bundle")

    if a.run_bundle:
        bundle = Path(a.run_bundle)
        manifest_path = bundle / "manifest.json"
        inputs_path = bundle / "inputs"
        if not manifest_path.is_file() or not inputs_path.is_dir():
            print(f"ERROR: {bundle} is not an EDRAK evaluation run bundle.", file=sys.stderr)
            return 2
        a.inputs = [
            str(inputs_path / "*.json"),
            str(bundle / "outputs" / "metrics_*.json"),
        ]
        if a.out == "reports":
            a.out = str(bundle / "evaluation")

    if not a.dry_run:
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass
        if not os.getenv("OPENAI_API_KEY"):
            print("ERROR: set OPENAI_API_KEY for the DeepEval judge (or use --dry-run).", file=sys.stderr)
            return 2
        try:
            import deepeval  # noqa: F401
        except ImportError:
            print("ERROR: pip install deepeval (or use --dry-run).", file=sys.stderr)
            return 2

    agent = EvalAgent(a)
    if not agent.load():
        print(f"ERROR: no readable files matched {a.inputs} (cwd: {Path.cwd()})", file=sys.stderr)
        return 2
    if a.run_bundle and not a.all_attempts:
        agent.keep_final()
    evaluation_status = agent.run()

    if a.compare_to:
        comparison_out = Path(a.compare_out) if a.compare_out else (
            Path(os.getenv("EDRAK_EVAL_REPORTS_DIR") or Path(__file__).resolve().parents[2] / "reports")
            / "compare"
            / f"{bundle.name}_{a.compare_to}.json"
        )
        compare_command = [
            sys.executable,
            str(Path(__file__).with_name("compare.py")),
            a.compare_to,
            str(bundle),
            "--out",
            str(comparison_out),
        ]
        if a.compare_no_llm or a.dry_run:
            compare_command.append("--no-llm")
        try:
            comparison = subprocess.run(compare_command, check=False)
        except OSError as exc:
            print(f"ERROR: could not start ground-truth comparison: {exc}", file=sys.stderr)
            return 2
        if comparison.returncode:
            print(f"ERROR: ground-truth comparison failed with exit code {comparison.returncode}.", file=sys.stderr)
            return comparison.returncode

    return evaluation_status


if __name__ == "__main__":
    raise SystemExit(main())