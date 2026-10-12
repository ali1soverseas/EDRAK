#!/usr/bin/env python3
"""EDRAK evaluation: judge every agent of a multi-agent pipeline from its output JSON files.

HOW IT WORKS
  1. LOAD     read every JSON and detect which agent produced it (worker / verification / cross_signal / decision).
  2. PHASES   files may come from several phases of ONE run (a worker re-run, a second verification pass ...).
              Every file is evaluated "as of" its own timestamp: it only sees the worker version, verification and
              signals that existed when it was produced. Re-runs reuse finding ids, so this matters.
  3. CONTEXT  one shared picture per file: goal, evidence, findings, signals. Missing parts are simply skipped.
  4. JUDGE    each agent's evaluator turns its output into "units" (findings, signals, recommendations ...) and scores
              them with DeepEval (LLM-as-a-judge). Metrics differ per agent:
                 worker         faithfulness, relevancy, specificity, insight, actionability
                                (+ context_relevancy with a flag). insight/actionability use WORKER-SPECIFIC criteria
                                (a research finding about a competitor, not a signal or a recommendation).
                 verification   faithfulness, relevancy   (an independent second opinion on the verifier's labels)
                 cross_signal   faithfulness, relevancy, insight                (does the signal add something?)
                 decision       faithfulness, relevancy, actionability
  5. CHECK    deterministic Python checks: counts match metadata, ids resolve, numbers appear in the evidence,
              no duplicates, confidence is calibrated, deadlines are not in the past, absence claims
              ("not mentioned") are not stated as facts ... Checks that need data a use case does not have are
              skipped. Severity is "error" (integrity) or "warn" (quality / run state).
  6. TRACE    pipeline-level: decision -> signal -> finding -> evidence, plus a phase-1 vs phase-2 re-run comparison.
  7. REPORT   CSV per agent, JSON per agent, overview.csv, pipeline_report.json, summary.md.

RULE OF THE HOUSE
  The LLM only produces judgements (scores + reasons, cached on disk). Every count, percentage, pass/fail and
  flag is computed in plain Python.

USAGE (give every use case its own --out; put ALL phases of that use case in one run)
    pip install deepeval python-dotenv
    python edrak_eval_agent.py --inputs "usecase1/*.json" --out reports/usecase1
    python edrak_eval_agent.py --inputs "usecase1/*.json" --dry-run            # no LLM, deterministic checks only
    python edrak_eval_agent.py --inputs "usecase1/*.json" --context-relevancy  # also judge the retrieved evidence
    python edrak_eval_agent.py --inputs "usecase1/*.json" --deep               # also judge actions + matrix cells
    python edrak_eval_agent.py --inputs "usecase1/*.json" --no-extra-metrics   # only faithfulness + relevancy
    python edrak_eval_agent.py --inputs "usecase1/*.json" --agents verification decision

An initial-input JSON (research goal, company, competitors) is optional. Without it the goal is taken from the
decision / cross-signal `business_goal`, or from --task.
"""
from __future__ import annotations

import argparse, copy, csv, difflib, glob, hashlib, json, os, re, statistics, sys, time
from collections import Counter, defaultdict
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
# Per-agent overrides: a raw research finding is not a signal or a recommendation, so it gets its own criteria.
GEVAL_BY_AGENT = {
    "worker": {
        "insight": ("The output is a research finding about a competitor and the context is its source evidence. "
                    "Score high only if it goes beyond restating a source sentence: it explains what the fact "
                    "means for the competitor's position relative to the target company (a difference, advantage, "
                    "gap or implication) AND that interpretation follows from the evidence. Score low if it only "
                    "repeats marketing text or a generic fact, or draws conclusions the evidence does not support.",
                    True),
        "actionability": ("The output is a research finding and the context is its source evidence. Score high if "
                          "a product, pricing or strategy owner could act on it: it names the competitor and "
                          "market, gives the concrete mechanism (fee, threshold, program, delivery promise, "
                          "condition) and something to compare against. Score low if it is too vague or too "
                          "generic to inform any decision.", True),
    },
}
CUSTOM = tuple(GEVAL)
AGENT_METRICS = {
    "worker":       ["faithfulness", "relevancy", "specificity", "insight", "actionability"],
    "verification": ["faithfulness", "relevancy"],
    "cross_signal": ["faithfulness", "relevancy", "insight"],
    "decision":     ["faithfulness", "relevancy", "actionability"],
}


# =============================================================================== 1. small helpers
def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object.")
    return data


def detect(d: dict[str, Any]) -> str:
    """Which pipeline component produced this file? Workers are detected generically (any 'worker' name)."""
    if "company_profile" in d and "research_goal" in d:
        return "input"
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


def domain(url: Any) -> str:
    return urlparse(str(url or "")).netloc.lower().removeprefix("www.")


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
        self.deep, self.ctx_rel, self.extra = args.deep, args.context_relevancy, not args.no_extra_metrics
        self.thr = {"faithfulness": args.faithfulness_threshold, "relevancy": args.relevancy_threshold,
                    "context_relevancy": args.context_relevancy_threshold, "specificity": args.specificity_threshold,
                    "insight": args.insight_threshold, "actionability": args.actionability_threshold}
        self.metrics: list[str] = []
        self.geval: dict[str, tuple] = dict(GEVAL)

    def for_agent(self, kind: str) -> "Cfg":
        c = copy.copy(self)
        ms = [m for m in AGENT_METRICS[kind] if self.extra or m not in CUSTOM]
        if kind == "worker" and self.ctx_rel:
            ms.append("context_relevancy")
        c.metrics = ms
        c.geval = {**GEVAL, **GEVAL_BY_AGENT.get(kind, {})}
        return c


class Judge:
    """Thin wrapper around DeepEval metrics. Results are cached on disk by content hash, so reruns are free."""

    def __init__(self, model: str, cache_path: Path, enabled: bool, retries: int = 2) -> None:
        self.model, self.cache_path, self.enabled, self.retries = model, cache_path, enabled, retries
        self.cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self.calls = self.hits = self.errors = 0

    def run(self, metric: str, question: str, output: str, ctx: list[str], threshold: float,
            spec: tuple | None = None) -> dict[str, Any]:
        """`spec` = (criteria, uses_context) for GEval metrics; it lets each agent have its own criteria."""
        if not self.enabled:
            return {"score": None, "reason": "", "error": "dry-run"}
        spec = spec or GEVAL.get(metric)
        needs_ctx = spec[1] if spec else METRICS[metric][1]
        if needs_ctx and not ctx:
            return {"score": None, "reason": "", "error": "skipped: no context text"}
        if not (output or "").strip():
            return {"score": None, "reason": "", "error": "skipped: empty output"}
        key = hashlib.sha256(json.dumps([metric, self.model, question, output, ctx, spec],
                                        ensure_ascii=False).encode()).hexdigest()
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        last = ""
        for attempt in range(self.retries):
            try:
                res = self._measure(metric, question, output, ctx, threshold, spec)
                self.calls += 1
                self.cache[key] = res
                self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
                return res
            except Exception as exc:  # keep going; the failure is recorded in the report
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(1 + attempt)
        self.errors += 1
        return {"score": None, "reason": "", "error": last}

    def _measure(self, metric: str, question: str, output: str, ctx: list[str], threshold: float,
                 spec: tuple | None = None) -> dict[str, Any]:
        import deepeval.metrics as dm
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
        case = LLMTestCase(input=question or "Evaluate this output.", actual_output=output,
                           retrieval_context=ctx or None)
        if spec:
            criteria, uses_ctx = spec
            params = [LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT]
            if uses_ctx:
                params.append(LLMTestCaseParams.RETRIEVAL_CONTEXT)
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
    for m in cfg.metrics:
        applies = (only is None or m in only) and (m in ("relevancy", *CUSTOM) or (m == "faithfulness" and faith)
                                                   or (m == "context_relevancy" and ctx_rel))
        if skip_note or not applies:
            cols.update({f"{m}_score": None, f"{m}_pass": None, f"{m}_reason": "",
                         f"{m}_note": skip_note or "not applicable"})
            continue
        r = judge.run(m, question, (outputs or {}).get(m, output), ctx, cfg.thr[m], spec=geval.get(m))
        cols.update({f"{m}_score": r["score"], f"{m}_pass": None if r["score"] is None else r["score"] >= cfg.thr[m],
                     f"{m}_reason": r["reason"], f"{m}_note": r["error"]})
    return cols


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
    cfg = cfg.for_agent("worker")
    worker = d["worker"]
    meta = d.get("metadata") or {}
    control = meta.get("control_summary") or {}
    goal = ctx.task_goals.get(worker) or meta.get("research_goal") or ctx.goal
    own = evidence_map(d.get("evidence"))
    findings = dict_items(d.get("findings"))
    competitors = [c for c in map(comp_name, meta.get("competitors") or ctx.competitors) if c]
    profiles = meta.get("competitor_profiles") or {}
    matrix = meta.get("comparison_matrix") or {}
    competitor_style = bool(profiles or meta.get("competitors"))      # competitor-type worker
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
        f_question = (f"{f.get('category') or 'Overview'} of {comp or 'the competitors'}. "
                      f"Overall research goal: {goal}")
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

    # ---- comparison matrix cells: each one is a claim about a competitor and should rest on evidence
    cells_n = cells_ev = cells_open = 0
    for dim in dict_items(matrix.get("dimensions")):
        for cell in dict_items(dim.get("cells")):
            cells_n += 1
            ids = [str(x) for x in cell.get("evidence_ids") or []]
            texts = [t for t in (ev_text(own[x]) for x in ids if x in own) if t][:MAX_CTX_ITEMS]
            rel = str(cell.get("relative_to_target", "")).lower()
            cells_ev += bool(ids)
            cells_open += rel == "not_established"
            summary = cell.get("summary", "")
            row = {"unit_type": "matrix_cell", "unit_index": cells_n, "unit_id": f"{dim.get('dimension')} | {cell.get('competitor')}",
                   "attempt": attempt, "category": rel, "statement": summary, "confidence": cell.get("confidence"),
                   "refs": len(ids), "refs_resolved": sum(x in own for x in ids),
                   "ungrounded_numbers": ungrounded_numbers(summary, texts) if texts else []}
            row.update(judge_unit(judge, cfg, f"{dim.get('dimension', goal)} of {cell.get('competitor', '')}".strip(),
                                  summary, texts, only=("faithfulness", "relevancy"),
                                  skip_note="" if cfg.deep else "skipped (use --deep to judge matrix cells)"))
            flags = []
            if rel != "not_established" and not ids:
                flags.append("cell_claim_without_evidence")
            if row["refs"] and row["refs_resolved"] < row["refs"]:
                flags.append("unresolved_refs")
            if absence_claim(summary):
                flags.append("absence_claim")
            flags += [METRICS[m][2] for m in cfg.metrics if row.get(f"{m}_pass") is False]
            row["flags"] = flags
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
        "profiles": len(profiles), "matrix_cells": cells_n, "matrix_cells_with_evidence_pct": pct(cells_ev, cells_n),
        "matrix_cells_not_established_pct": pct(cells_open, cells_n),
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
        check("matrix claims rest on evidence", not any("cell_claim_without_evidence" in r["flags"] for r in rows),
              f"{sum('cell_claim_without_evidence' in r['flags'] for r in rows)} cells", "warn") if cells_n else None,
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
        self.judge = Judge(args.model, Path(args.cache), enabled=not args.dry_run)
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

    def not_evaluated(self) -> list[str]:
        """Downstream agents with no evaluated file. Without them insight (cross_signal) and the recommendation-level
        actionability (decision) are not scored, and the end-to-end trace is incomplete."""
        wanted = {"verification", "cross_signal", "decision"}
        if self.a.agents:
            wanted &= set(self.a.agents)
        return sorted(wanted - set(self.per_agent))

    def run(self) -> int:
        final = build_ctx(self.loaded, self.a.task)
        if not final.goal:
            print("WARNING: no research goal found (include the initial-input file or pass --task); "
                  "relevancy scores will be weak.", file=sys.stderr)
        # evaluate in pipeline order, so the console reads like the run
        order = {"worker": 0, "verification": 1, "cross_signal": 2, "decision": 3}
        for p, kind, d in sorted(self.loaded, key=lambda x: (order.get(x[1], 9), file_time(x[1], x[2]) or EPOCH)):
            if kind in ("input", "orchestration"):
                continue
            if kind not in EVALUATORS:
                print(f"Skipped {p.name}: unrecognised schema")
                continue
            label = d["worker"] if kind == "worker" else kind
            if self.a.agents and kind not in self.a.agents and label not in self.a.agents:
                continue
            ctx = build_ctx(self.loaded, self.a.task, as_of=file_time(kind, d))   # what existed when this file was made
            try:
                res = EVALUATORS[kind](p, d, ctx, self.judge, self.cfg)
            except Exception as exc:  # one broken file must not stop the others
                self.errors.append({"file": p.name, "error": f"evaluator crashed: {type(exc).__name__}: {exc}"})
                print(f"  ERROR evaluating {p.name}: {exc}", file=sys.stderr)
                continue
            res["rows"] = [{"file": p.name, "agent": label, **r} for r in res["rows"]]
            res["file"] = p.name
            self.per_agent.setdefault(label, []).append(res)
            if kind != "worker":
                self.chain.append({"file": p.name, "agent": kind, "worker_attempts_seen": ctx.worker_attempt,
                                   "verification_seen": ctx.verification_file, "cross_signal_seen": ctx.cross_signal_file})
            err = sum(c["passed"] for c in res["checks"] if c["severity"] == "error")
            n_err = sum(c["severity"] == "error" for c in res["checks"])
            warn = sum(not c["passed"] for c in res["checks"] if c["severity"] == "warn")
            print(f"[{label}] {p.name}: {len(res['rows'])} units, {err}/{n_err} integrity checks passed, {warn} warnings")
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
        return 1 if self.errors else 0

    # -- reports
    def metrics_in_use(self) -> list[str]:
        used = [m for res in sum(self.per_agent.values(), []) for m in res.get("metrics_used", [])]
        return [m for m in METRICS if m in used]

    def write_all(self) -> None:
        out = Path(self.a.out)
        out.mkdir(parents=True, exist_ok=True)
        overview = []
        for agent, results in self.per_agent.items():
            write_csv(out / f"{agent}_units.csv", [r for res in results for r in res["rows"]])
            used = {m for res in results for m in res.get("metrics_used", [])}    # only thresholds that were applied
            report = {
                "agent": agent, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "model": None if self.a.dry_run else self.a.model, "dry_run": self.a.dry_run,
                "thresholds": {m: t for m, t in self.cfg.thr.items() if m in used},
                "files": [{"file": res["file"], "metrics_used": res.get("metrics_used"), "metrics": res["metrics"],
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
        (out / "summary.md").write_text(self.markdown(), encoding="utf-8")
        print(f"\nDone. LLM calls: {self.judge.calls}, cache hits: {self.judge.hits}, judge failures: {self.judge.errors}."
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
              "| Agent | File | Integrity checks | Warnings | Units | Flagged | " + " | ".join(f"{m} avg" for m in m_cols) + " |",
              "|---|---|---|---|---|---|" + "---|" * len(m_cols)]
        for agent, results in self.per_agent.items():
            for r in results:
                m = r["metrics"]
                errs = [c for c in r["checks"] if c["severity"] == "error"]
                warn = sum(not c["passed"] for c in r["checks"] if c["severity"] == "warn")
                cell = lambda x: "-" if m.get(f"{x}_avg") is None else str(m[f"{x}_avg"])
                L.append(f"| {agent} | {r['file']} | {sum(c['passed'] for c in errs)}/{len(errs)} | {warn} | {m.get('units')} | "
                         f"{m.get('units_flagged')} | " + " | ".join(cell(x) if x in r.get("metrics_used", []) else "n/a" for x in m_cols) + " |")
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
    ap.add_argument("--inputs", nargs="+", required=True, help="JSON files or globs of ONE use case (all phases together)")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--agents", nargs="+", help="Only evaluate these (kind or worker name, e.g. verification decision)")
    ap.add_argument("--task", default="", help="Override the research goal text")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), help="DeepEval judge model")
    ap.add_argument("--faithfulness-threshold", type=float, default=0.7)
    ap.add_argument("--relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--specificity-threshold", type=float, default=0.5)
    ap.add_argument("--insight-threshold", type=float, default=0.5)
    ap.add_argument("--actionability-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy", action="store_true", help="Also judge whether cited evidence is relevant to the goal")
    ap.add_argument("--no-extra-metrics", action="store_true", help="Only faithfulness + relevancy (skip specificity / insight / actionability)")
    ap.add_argument("--cache", default="edrak_judge_cache.json")
    ap.add_argument("--dry-run", action="store_true", help="No LLM calls; deterministic checks only")
    ap.add_argument("--deep", action="store_true", help="Also judge each recommended action and each matrix cell")
    ap.add_argument("--narrative", action="store_true", help="Add an LLM-written diagnosis to summary.md")
    a = ap.parse_args()

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
    return agent.run()


if __name__ == "__main__":
    raise SystemExit(main())