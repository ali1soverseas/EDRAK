from __future__ import annotations

import argparse
import copy
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
import threading
import time
import unicodedata
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
# Custom (GEval) metrics: name -> (criteria, uses retrieval context).
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


# =============================================================================== 3. LLM judge (DeepEval)
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
    """Thin wrapper around DeepEval metrics."""

    def __init__(self, model: str, enabled: bool, retries: int = 2, workers: int = 1) -> None:
        self.model, self.enabled, self.retries = model, enabled, retries
        self.workers = max(1, workers)
        self.calls = self.successes = self.errors = 0
        self.skipped = 0
        self.total_latency_seconds = 0.0
        self.metric_calls: Counter[str] = Counter()
        self.metric_failures: Counter[str] = Counter()
        self.metric_skips: Counter[str] = Counter()
        self.next_allowed_at = 0.0
        self.rate_limit_open = False
        self._lock = threading.RLock()

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


def dashboard_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    visible = {
        "status",
        "units",
        "units_flagged",
        "planned_tasks",
        "result_tasks",
        "successful_tasks",
        "task_completion_pct",
        "workflow_success_pct",
        "context_relevancy_score",
        "context_relevancy_error",
        "context_precision_score",
        "context_precision_error",
        "context_recall_score",
        "context_recall_error",
    }
    families: dict[str, set[str]] = defaultdict(set)
    for key in metrics:
        match = re.fullmatch(r"(.+)_(tested|avg|pass_pct)", key)
        if match:
            families[match.group(1)].add(match.group(2))
    for family, fields in families.items():
        if len(fields) >= 2:
            visible.update(f"{family}_{field}" for field in fields)

    for key, value in metrics.items():
        if key.startswith("judge_") or isinstance(value, (dict, list)):
            visible.add(key)
        elif isinstance(value, str) and re.fullmatch(r"[^=,]+=[^=,]+(?:,\s*[^=,]+=[^=,]+)*", value):
            visible.add(key)
    return {key: value for key, value in metrics.items() if key in visible}


# =============================================================================== 4. per-agent evaluators
# Evaluators return rows, dashboard metrics, and checks; judged stages also return threshold metric names.
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
        social = bool(recs) and all(ev_meta(r).get("kind") in ("social_post", "social_comment") for r in recs)  #social media posts
        first_party = bool(recs) and all(ev_meta(r).get("first_party") is True for r in recs)   # a company's own site
        row = {
            "unit_type": "finding", "unit_index": i, "unit_id": fid, "attempt": attempt,
            "category": f.get("category", ""), "statement": statement, "confidence": f.get("confidence", ""),
            "refs": len(ids), "refs_resolved": len(recs), "synthetic_refs": sum(is_synthetic(r) for r in recs),  # synthetic evidence
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
            "competitor_intelligence": "competitor positioning, strategy, or performance",
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

#
    fnd = [r for r in rows if r["unit_type"] == "finding"]
    fconfs = [conf_num(r["confidence"]) for r in fnd]
    overall = conf_num(d.get("confidence"))
    n_absence = sum("absence_claim" in r["flags"] for r in rows)
    avg_finding_confidence = mean(fconfs)
    ungrounded_number_findings = sum(bool(r["ungrounded_numbers"]) for r in fnd)
    metrics = {
        "status": d.get("status"),
        **llm_stats(rows, cfg),
    }
    discarded = {str(x.get("evidence_id")) for x in dict_items(meta.get("discarded_evidence"))}
    gap = abs(overall - avg_finding_confidence) if overall is not None and avg_finding_confidence is not None else None
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
        check("findings do not contain numbers absent from their evidence", ungrounded_number_findings == 0,
              f"{ungrounded_number_findings} findings", "warn"),
        check("no unit states an absence of evidence as a fact ('not mentioned')", n_absence == 0,
              f"{n_absence} units", "warn"),
        check("run completed (not partial/failed)", d.get("status") in (None, "completed"), str(d.get("status")), "warn"),
        check("run reported no error", not d.get("error"), str(d.get("error")), "warn"),
    ]
    return _result(rows, metrics, checks, cfg)




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
       ##-----  
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

    confs = [conf_num(r["confidence"]) for r in rows if conf_num(r["confidence"]) is not None]
    n_ver = sum(r["verification_status"] in POSITIVE for r in rows)
    n_not = len(rows) - n_ver
    conf_of = lambda sel: mean([conf_num(r["confidence"]) for r in rows if sel(r)])
    by_q = {q: conf_of(lambda r, q=q: r["evidence_quality"] == q) for q in ("high", "medium", "low")}
    with_issue = sum(bool(r["contradictions"]) for r in rows)
    avg_confidence_verified = conf_of(lambda r: r["verification_status"] in POSITIVE)
    avg_confidence_not_verified = conf_of(lambda r: r["verification_status"] not in POSITIVE)
    near_duplicate_findings = sum("near_duplicate" in r["flags"] for r in rows)
    metrics = llm_stats(rows, cfg)
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
              avg_confidence_verified > avg_confidence_not_verified,
              f"verified={avg_confidence_verified} not_verified={avg_confidence_not_verified}", "warn")
        if n_ver and n_not and avg_confidence_verified is not None
        and avg_confidence_not_verified is not None else None,
        check("confidence follows evidence quality (high >= medium >= low)", q_order == sorted(q_order, reverse=True),
              f"{by_q}", "warn") if len(q_order) >= 2 else None,
        check("no finding's confidence contradicts its label", not any("confidence_contradicts_status" in r["flags"] for r in rows),
              severity="warn"),
        check("control conflicts match findings with contradictions", len(control.get("conflicts") or []) == with_issue,
              f"control={len(control.get('conflicts') or [])} findings={with_issue}", "warn"),
        check("control gaps are not just a copy of next_research_targets",
              control.get("missing_information") != control.get("next_research_targets")
              or not control.get("missing_information")),
        check("no near-duplicate findings", near_duplicate_findings == 0,
              f"{near_duplicate_findings} duplicates", "warn"),
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
    signals_on_unverified = sum(bool(r["unverified_support"]) for r in rows)
    signals_entity_not_in_support = sum(bool(r["entities_missing_from_support"]) for r in rows)
    signals_confidence_exceeds_support = sum("confidence_exceeds_support" in r["flags"] for r in rows)
    metrics = {
        "status": d.get("status"),
        "signal_types": ", ".join(f"{t}={n}" for t, n in sorted(types.items())),
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
        check("no signal rests on findings the verifier did not verify", signals_on_unverified == 0,
              f"{signals_on_unverified} signals") if have_status else None,
        check("decision_ready_context.key_signals matches signals", len(block.get("key_signals", [])) == len(rows))
        if "key_signals" in block else None,
        check("decision_question_count matches decision_ready_context", stats.get("decision_question_count") == len(questions),
              f"stats={stats.get('decision_question_count')} actual={len(questions)}")
        if "decision_question_count" in stats else None,
        check("every signal's decision_question is listed in decision_ready_context",
              all(str(s.get("decision_question")) in questions for s in signals if s.get("decision_question")), severity="warn")
        if questions else None,
        check("no signal names an entity absent from its support", signals_entity_not_in_support == 0,
              f"{signals_entity_not_in_support} signals", "warn"),
        check("no convergence signal rests on a single worker",
              not any("convergence_from_single_worker" in r["flags"] for r in rows), severity="warn"),
        check("signal domains_involved are backed by its supporting findings",
              not any("domain_not_in_support" in r["flags"] for r in rows), severity="warn"),
        check("signal confidence never exceeds its support", signals_confidence_exceeds_support == 0, severity="warn"),
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
    recommendations_with_new_numbers = sum(bool(r["new_numbers_not_in_signals"]) for r in recs)
    recommendations_with_past_timeline = sum(bool(r["past_years_mentioned"]) for r in recs)
    metrics = {
        "status": d.get("status"),
        "priority_mix": ", ".join(f"{k}={v}" for k, v in sorted(Counter(str(p.get("priority")) for p in prios).items())),
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
        check("no recommendation has numbers absent from its signals", recommendations_with_new_numbers == 0,
              f"{recommendations_with_new_numbers} recommendations", "warn") if ctx.signals else None,
        check("no recommendation schedules work in a year that is already over",
              recommendations_with_past_timeline == 0,
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


import math
from pathlib import Path
from typing import Any, Callable


def _cosine_similarity(
    vector_a: list[float],
    vector_b: list[float],
) -> float:
    """Calculate cosine similarity between two embedding vectors."""
    if len(vector_a) != len(vector_b) or not vector_a:
        return 0.0

    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)



from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")
client = OpenAI()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate semantic embeddings using OpenAI."""
    if not texts:
        return []

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=[text.replace("\n", " ") for text in texts],
    )

    # Preserve the original input order.
    ordered = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in ordered]


def _golden_answer_for_rag(
    rag_payload: dict[str, Any],
    loaded: list[tuple[Path, str, dict[str, Any]]],
    embed_texts: Callable[[list[str]], list[list[float]]],
    ground_truth_root: Path | None = None,
    min_similarity: float = 0.35,
    max_findings: int = 5,
) -> tuple[str, str] | None:
    """
    Select semantically relevant findings from the internal-intelligence
    golden dataset for RAG evaluation.

    embed_texts(texts) must return one embedding vector per input text,
    using the same embedding model as the RAG retrieval pipeline.
    """

    # 1. Validate worker.
    worker = str(rag_payload.get("worker") or "").strip()

    if worker != "internal_intelligence":
        return None

    # 2. Get the actual RAG query.
    query = str(
        rag_payload.get("query")
        or rag_payload.get("question")
        or rag_payload.get("user_query")
        or ""
    ).strip()

    if not query:
        return None

    # 3. Get retrieved chunks.
    raw_chunks = (
        rag_payload.get("retrieved_chunks")
        or rag_payload.get("chunks")
        or rag_payload.get("retrieval_context")
        or []
    )

    if isinstance(raw_chunks, str):
        chunks = [raw_chunks.strip()] if raw_chunks.strip() else []
    elif isinstance(raw_chunks, list):
        chunks = []

        for chunk in raw_chunks:
            if isinstance(chunk, str):
                chunk_text = chunk
            elif isinstance(chunk, dict):
                chunk_text = str(
                    chunk.get("text")
                    or chunk.get("content")
                    or chunk.get("chunk_text")
                    or ""
                )
            else:
                continue

            if chunk_text.strip():
                chunks.append(chunk_text.strip())
    else:
        chunks = []

    # 4. Identify the current use case.
    use_cases = set()

    for _, kind, data in loaded:
        if kind != "input":
            continue

        business_context = data.get("business_context") or {}

        use_case = str(
            business_context.get("use_case")
            or data.get("use_case")
            or ""
        ).strip().casefold()

        if use_case:
            use_cases.add(use_case)

    if len(use_cases) != 1:
        return None

    use_case = next(iter(use_cases))

    # 5. Find the matching golden dataset.
    root = (
        ground_truth_root
        if ground_truth_root is not None
        else Path(__file__).resolve().parents[2]
        / "golden"
        / "ground_truth"
    )

    if not root.is_dir():
        return None

    matches = []

    for input_path in sorted(root.glob("*/inputs/initial_input.json")):
        golden_input = read_json(input_path)
        business_context = golden_input.get("business_context") or {}

        golden_use_case = str(
            business_context.get("use_case")
            or golden_input.get("use_case")
            or ""
        ).strip().casefold()

        if golden_use_case == use_case:
            matches.append(input_path.parent.parent)

    if len(matches) > 1:
        raise ValueError(
            f"Multiple golden datasets match use case {use_case!r}."
        )

    if not matches:
        return None

    dataset = matches[0]

    # Prefer a query-specific answer over the approximate finding selection
    # below. Normalize typography, case, and whitespace without changing words.
    rag_golden_path = dataset / "inputs" / "rag_internal_intelligence.json"
    if rag_golden_path.is_file():
        rag_golden = read_json(rag_golden_path)
        def normalize_query(value: Any) -> str:
            normalized = unicodedata.normalize("NFKC", str(value)).casefold()
            normalized = re.sub(r"[\u2010-\u2015]", "-", normalized)
            return " ".join(normalized.split())

        normalized_query = normalize_query(query)
        matches = [
            item
            for item in dict_items(rag_golden.get("queries"))
            if normalize_query(item.get("query") or "") == normalized_query
            and str(item.get("expected_output") or "").strip()
        ]
        if len(matches) > 1:
            raise ValueError(
                f"Multiple golden RAG references match query {query!r}."
            )
        if matches:
            source = (
                Path("golden")
                / "ground_truth"
                / dataset.name
                / "inputs"
                / rag_golden_path.name
            )
            return str(matches[0]["expected_output"]).strip(), source.as_posix()

    worker_path = (
        dataset / "inputs" / "worker_internal_intelligence.json"
    )

    if not worker_path.is_file():
        return None

    worker_data = read_json(worker_path)

    # 6. Extract candidate golden findings.
    findings = dict_items(worker_data.get("findings"))

    candidates = [
        {
            "statement": str(finding.get("statement") or "").strip(),
            "finding_id": finding.get("finding_id"),
        }
        for finding in findings
        if str(finding.get("statement") or "").strip()
    ]

    if not candidates:
        return None

    # 7. Embed the query, retrieved chunks, and golden findings together.
    # A single batch keeps the embedding calls efficient.
    texts = (
        [query]
        + chunks
        + [candidate["statement"] for candidate in candidates]
    )

    vectors = embed_texts(texts)

    if len(vectors) != len(texts):
        raise ValueError(
            "embed_texts must return exactly one vector per input text."
        )

    query_vector = vectors[0]
    chunk_vectors = vectors[1:1 + len(chunks)]
    finding_vectors = vectors[1 + len(chunks):]

    # 8. Score each golden finding.
    ranked = []

    for candidate, finding_vector in zip(candidates, finding_vectors):
        query_score = _cosine_similarity(
            query_vector,
            finding_vector,
        )

        chunk_score = max(
            (
                _cosine_similarity(chunk_vector, finding_vector)
                for chunk_vector in chunk_vectors
            ),
            default=None,
        )

        # Query is primary; retrieved chunks add supporting relevance.
        if chunk_score is None:
            score = query_score
        else:
            score = 0.70 * query_score + 0.30 * chunk_score

        candidate_result = {
            **candidate,
            "score": score,
            "query_score": query_score,
            "chunk_score": chunk_score,
        }

        if score >= min_similarity:
            ranked.append(candidate_result)

    if not ranked:
        return None

    # 9. Rank candidates and return the strongest matches.
    ranked.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    selected = ranked[:max_findings]

    expected_answer = "\n".join(
        f"- {item['statement']}"
        for item in selected
    )

    source = (
        Path("golden")
        / "ground_truth"
        / dataset.name
        / "inputs"
        / worker_path.name
    ).as_posix()

    return expected_answer, source

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
                            "argument_types", "argument_values", "result_count", "result_summary", "result", "latency_ms",
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
    deps_missing = []
    for task in tasks:
        name = str(task.get("worker") or task.get("name") or task.get("task_id") or "")
        deps = task.get("depends_on") or task.get("dependencies") or []
        if isinstance(deps, str): deps = [deps]
        for dep in deps:
            dep_name = str(dep.get("worker") or dep.get("task_id") or dep) if isinstance(dep, dict) else str(dep)
            if dep_name not in planned:
                deps_missing.append(f"{name}->{dep_name}")

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
    completed_pct = pct(len(successful), len(planned))
    manifest = getattr(ctx, "run_manifest", {}) or {}
    workflow_status = str(d.get("status") or manifest.get("status") or "").strip().casefold()
    workflow_success_pct = completed_pct if workflow_status in {"completed", "success", "succeeded", "ok"} else (
        0.0 if workflow_status in {"failed", "error", "partial", "cancelled", "canceled"} else None
    )
    metrics = {"planned_tasks":len(tasks),"result_tasks":len(results),
               "successful_tasks":len(successful),
               "task_completion_pct":completed_pct,
               "workflow_success_pct":workflow_success_pct}
    checks = [
        check("planned tasks have results", not missing, ", ".join(missing)),
        check("results correspond to planned tasks", not unexpected, ", ".join(unexpected), "warn") if planned else None,
        check("all declared dependencies reference planned tasks", not deps_missing, ", ".join(deps_missing)),
        check("orchestration reports no failed tasks", not failed, f"{len(failed)} failed", "warn"),
        check("pipeline status is successful", str(d.get("status","completed")).lower() not in {"failed","error","partial"},
              str(d.get("status","unknown")), "warn")
    ]
    return {"rows": rows, "metrics": metrics, "checks": [x for x in checks if x]}


@evaluator("rag")
def eval_rag(path, d, ctx, judge, cfg):
    docs = d.get("retrieval_results") or d.get("retrieved_documents") or d.get("retrieved_chunks") or []
    docs = dict_items(docs)
    query = str(d.get("query") or d.get("user_query") or d.get("retrieval_query") or "")
    ids = [str(x.get("document_id") or x.get("chunk_id") or x.get("id") or "") for x in docs]
    raw_scores = [rag_document_score(x) for x in docs]
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
    metrics = {
        f"{metric}_score": rag_judges[metric].get("score")
        for metric in ("context_precision", "context_recall", "context_relevancy")
    }
    metrics.update({
        f"{metric}_error": rag_judges[metric].get("error")
        for metric in ("context_precision", "context_recall", "context_relevancy")
    })
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
    return {"rows": rows, "metrics": metrics, "checks": [x for x in checks if x]}


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
    names = [
        str(x.get("tool") or x.get("tool_name") or (x.get("attributes") or {}).get("edrak.tool.name") or x.get("name") or "")
        for x in tool_calls
    ]
    metrics = {
        "worker_types": sorted({
            str((span.get("attributes") or {}).get("edrak.agent.type"))
            for span in worker_spans
            if (span.get("attributes") or {}).get("edrak.agent.type")
        }),
        "tool_names": sorted({n for n in names if n}),
    }
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
    metrics["units"] = len(rows)
    metrics["units_flagged"] = sum(bool(row["flags"]) for row in rows)
    return {
        "rows": rows,
        "metrics": metrics,
        "checks": [*checks, *execution["checks"]],
        "execution_quality": execution,
    }


# =============================================================================== 5. pipeline-level checks
def pipeline_checks(ctx: Ctx, decision_files: list[dict], loaded: list[tuple[Path, str, dict]]) -> dict[str, Any]:
    """End-to-end traceability: decision -> signal -> finding -> evidence. Each check runs only if its stages exist.
    `ctx` is the FINAL state of the run (latest worker attempts, latest verification, latest signals)."""
    checks = []
    findings = ctx.verified_findings or ctx.worker_findings

    if findings and ctx.evidence:
        bad = [fid for fid, f in findings.items() if not any(e in ctx.evidence for e in f.get("evidence_ids") or [])]
        checks.append(check("every finding traces to evidence", not bad, f"{len(bad)} untraceable"))

    if ctx.signals and findings:
        bad = [sid for sid, s in ctx.signals.items() if not any(f in findings for f in s.get("support", []))]
        checks.append(check("every signal traces to a finding", not bad, f"{len(bad)} untraceable"))
        if any(f.get("verification_status") for f in ctx.verified_findings.values()):
            weak = [sid for sid, s in ctx.signals.items()
                    if any(ctx.verified_findings.get(f, {}).get("verification_status") != "verified" for f in s["support"])]
            checks.append(check("signals only rest on verified findings", not weak, f"{len(weak)} signals", "warn"))

    if decision_files and ctx.signals and findings:
        total = traced = 0
        for dfile in decision_files:
            for qr in dict_items(dfile.get("question_recommendations")):
                ids = [str(x) for x in (qr.get("recommendation") or {}).get("supporting_signal_ids") or []]
                total += 1
                traced += bool(ids) and all(x in ctx.signals and any(f in findings for f in ctx.signals[x]["support"])
                                            for x in ids)
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
                            f"{w['ids_reused_with_changed_statement']} reused ids carry a different statement",
                            "warn"))
    for v in reruns["verification"]:
        checks.append(check("second verification pass does not downgrade previously verified findings",
                            v["verified_to_insufficient"] == 0, f"{v['verified_to_insufficient']} downgraded", "warn"))

    return {"checks": checks}


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
            out["workers"].append({
                "worker": w,
                "ids_reused_with_changed_statement": sum(norm(fa[i]["statement"]) != norm(fb[i]["statement"]) for i in common)})
    ver = sorted([(p, d) for p, k, d in loaded if k == "verification"],
                 key=lambda x: file_time("verification", x[1]) or EPOCH)
    for (pa, a), (pb, b) in zip(ver, ver[1:]):
        fa, fb = _by_id(a.get("findings")), _by_id(b.get("findings"))
        common = set(fa) & set(fb)
        same = [i for i in common if norm(fa[i]["statement"]) == norm(fb[i]["statement"])]
        st = lambda f, i: str(f[i].get("verification_status")).lower()
        out["verification"].append({
            "verified_to_insufficient": sum(st(fa, i) == "verified" and st(fb, i) != "verified" for i in same),
        })
    return out


# =============================================================================== 6. the evaluation agent (runner + reports)
class EvalAgent:
    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.cfg = Cfg(args)
        self.judge = Judge(
            args.model, enabled=not args.dry_run,
            retries=getattr(args, "retries", 2), workers=getattr(args, "workers", 1),
        )
        self.loaded: list[tuple[Path, str, dict]] = []
        self.errors: list[dict] = []
        self.per_agent: dict[str, list[dict]] = {}
        self.pipeline: dict = {"checks": []}

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
        self.loaded = kept

    def run(self) -> int:
        return self._run()

    def _run(self) -> int:
        final = build_ctx(self.loaded, self.a.task)
        has_output_evaluation = any(
            kind in {"worker", "verification", "cross_signal", "decision"}
            for _, kind, _ in self.loaded
        )
        if has_output_evaluation and not final.goal:
            print(
                "WARNING: no research goal found (include the initial-input file or pass --task); "
                "relevancy scores will be weak.",
                file=sys.stderr,
            )
        # Evaluate in pipeline order to preserve the dependency between stages.
        order = {"worker": 0, "metrics": 1, "verification": 2, "cross_signal": 3, "decision": 4}
        for p, kind, d in sorted(self.loaded, key=lambda x: (order.get(x[1], 9), file_time(x[1], x[2]) or EPOCH)):
            if kind == "input":
                continue
            if kind == "metrics":
                continue
            if kind not in EVALUATORS:
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
                golden_answer = _golden_answer_for_rag(
                    rag_payload=d,
                    loaded=self.loaded,
                    embed_texts=embed_texts,
                )
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
                res["checks"].extend(execution["checks"])
            res["rows"] = [{"file": p.name, "agent": label, **r} for r in res["rows"]]
            res["file"] = p.name
            self.per_agent.setdefault(label, []).append(res)
            integrity_checks = [item for item in res["checks"] if item["severity"] == "error"]
            integrity_failures = sum(not item["passed"] for item in integrity_checks)
            integrity_status = "FAIL" if integrity_failures else "PASS"
            units = len(res["rows"])
            flagged_units = sum(bool(row.get("flags")) for row in res["rows"])
            print(
                f"Finished evaluating {label}: integrity {integrity_status} "
                f"({integrity_failures}/{len(integrity_checks)} failed); "
                f"units {units} ({flagged_units} flagged)."
            )
        if not self.per_agent:
            print("ERROR: nothing to evaluate.", file=sys.stderr)
            return 2
        self.pipeline = pipeline_checks(final, [d for _, k, d in self.loaded if k == "decision"], self.loaded)
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

    def write_all(self) -> None:
        out = Path(self.a.out)
        out.mkdir(parents=True, exist_ok=True)
        obsolete_reports = [
            "metrics_report.json",
            "metrics_units.csv",
            "overview.csv",
            "summary.md",
            "observability.json",
        ]
        for obsolete in obsolete_reports:
            stale_report = out / obsolete
            if stale_report.is_file():
                stale_report.unlink()
        for agent, results in self.per_agent.items():
            stale_units = out / f"{agent}_units.csv"
            if stale_units.is_file():
                stale_units.unlink()
            used = {m for res in results for m in res.get("metrics_used", [])}
            report = {
                "agent": agent, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "model": None if self.a.dry_run else self.a.model, "dry_run": self.a.dry_run,
                "thresholds": {m: t for m, t in self.cfg.thr.items() if m in used},
                "files": [
                    {
                        "file": res["file"],
                        "metrics": dashboard_metrics(res["metrics"]),
                        **({
                            "execution_quality": {
                                "dimensions": res["execution_quality"].get("dimensions", {}),
                                "metrics": {
                                    key: value
                                    for key, value in res["execution_quality"].get("metrics", {}).items()
                                    if key in {"calls", "errors", "repeated_calls", "call_details"}
                                },
                            }
                        } if res.get("execution_quality") is not None else {}),
                        "checks": res["checks"],
                        "flagged_units": [
                            {key: row.get(key) for key in ("unit_type", "unit_id", "statement", "flags")}
                            for row in res["rows"] if row.get("flags")
                        ],
                    }
                    for res in results
                ],
            }
            (out / f"{agent}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                      encoding="utf-8")
        pipeline_checks = self.pipeline.get("checks", [])
        pipeline_report = {
            "total": len(pipeline_checks),
            "passed": sum(check["passed"] for check in pipeline_checks),
            "failed": sum(
                not check["passed"] and check["severity"] == "error"
                for check in pipeline_checks
            ),
            "warnings": sum(
                not check["passed"] and check["severity"] != "error"
                for check in pipeline_checks
            ),
            "checks": pipeline_checks,
        }
        (out / "pipeline_report.json").write_text(
            json.dumps(pipeline_report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


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
    ap.add_argument("--dry-run", action="store_true", help="No LLM calls; deterministic checks only")
    ap.add_argument("--deep", action="store_true", help="Also judge each recommended action")
    ap.add_argument(
        "--include-tool-payloads",
        action="store_true",
        help="Send redacted tool arguments/results to DeepEval for argument and result-use judgments",
    )
    ap.add_argument("--all-attempts", action="store_true",
                    help="Evaluate every attempt and verification pass, not just the final one (default for --run-bundle is final only)")
    ap.add_argument("--compare-to", help="Compare the completed run bundle with this golden/ground_truth folder")
    ap.add_argument("--compare-out", help="Write the comparison report to this path (defaults under the evaluation output directory)")
    a = ap.parse_args()
    if a.compare_to and not a.run_bundle:
        ap.error("--compare-to requires --run-bundle")
    if a.compare_to and a.dry_run:
        ap.error("--compare-to requires LLM calls and cannot be combined with --dry-run")

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