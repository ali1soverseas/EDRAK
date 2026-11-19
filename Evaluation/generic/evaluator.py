from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable, TypedDict
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


class EvalState(TypedDict, total=False):
    trace_path: str
    report_path: str
    ground_truth_path: str
    config_path: str
    worker: str
    use_judge: bool
    second_pass: bool
    second_pass_top_k: int
    trace: dict[str, Any]
    report: dict[str, Any]
    ground_truth: dict[str, Any]
    config: dict[str, Any]
    evidence: list[str]
    evidence_items: list[dict[str, Any]]
    result: dict[str, Any]
    errors: list[str]


# ---------------------------------------------------------------------------
# Default configuration (every key the evaluator reads, with its default).
# Override per run via --config JSON or ground-truth "evaluation_config".
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "thresholds": {"covered": 0.7, "partial": 0.4},
    "judge": {"provider": "openai", "model": None, "base_url": None},
"embeddings": {"provider": "sentence_transformers", "model": "all-MiniLM-L6-v2", "device": None, "batch_size": 64},
    "scoring": {"mandatory_only": True, "partial_credit": 0.5},
    "retrieval": {"enabled": True, "top_k": 5, "max_passages": 500, "max_passage_chars": 1400,
                  "max_evidence_chars": 18000, "max_report_chars": 30000, "max_candidates_chars": 14000},
    "scope": {"enabled": True, "excluded_entities": [], "require_exclusion_label": False,
              "require_in_scope_presence": True,
              "exclusion_labels": ["out of scope", "excluded", "not in scope", "outside the scope", "not a target competitor"]},
    "trace": {"evidence_span_names": [], "recursive_evidence_discovery": True, "max_evidence_item_chars": 1200,
              "evidence_payload_keys": ["results", "verified_findings", "findings", "sources", "evidence",
                                        "retrieved_evidence", "organic", "supporting_text", "content",
                                        "snippet", "excerpt", "extracted_fact", "text"]},
    "claims": {"max_claims": 40,
               "claim_keys": ["claim", "statement", "finding", "summary", "insight", "primary_claim", "key_finding"],
               "claim_list_keys": ["claims", "findings", "key_findings", "insights"],
               "overview_keys": ["summary", "headline"]},
    "worker_quality": {"enabled": True, "metrics": {}, "thresholds": {"pass": 0.7, "partial": 0.4},
                       "max_report_chars": 30000, "max_evidence_chars": 18000},
    "agent_improvement_review": {"enabled": True, "max_report_chars": 30000, "max_evidence_chars": 18000, "max_findings": 20},
}


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------
def _find_span_output(spans: list[dict], span_name: str, last: bool = False) -> dict[str, Any]:
    """Output dict of the first (or last) span with this name, else {}."""
    found: dict[str, Any] = {}
    for span in spans:
        if isinstance(span, dict) and span.get("name") == span_name and isinstance(span.get("output"), dict):
            found = span["output"]
            if not last:
                return found
    return found


def _span_outputs(spans: list[dict], span_name: str) -> list[dict[str, Any]]:
    return [s["output"] for s in spans
            if isinstance(s, dict) and s.get("name") == span_name and isinstance(s.get("output"), dict)]


def _dig(obj: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _loads_maybe(value: Any) -> Any:
    """Some traces store JSON as a string; decode when possible."""
    if isinstance(value, str) and value.strip()[:1] in "[{\"":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _is_truncated(value: Any) -> bool:
    """Tracers replace deep values with '<max depth>'; such references cannot be resolved."""
    return isinstance(value, str) and "<max depth>" in value


def _as_list(value: Any) -> list[Any]:
    value = _loads_maybe(value)
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _finding(fid: Any, claim: str, **extra: Any) -> dict[str, Any]:
    """Normalized finding shape shared by every worker adapter."""
    item = {"id": fid, "claim": claim.strip()}
    item.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return item


def _evidence_item(eid: Any, text: Any, source: Any = "", **extra: Any) -> dict[str, Any] | None:
    text = _text(text)
    if not text:
        return None
    return {"id": str(eid) if eid else "", "source": str(source or ""), "text": text, **extra}


# ---------------------------------------------------------------------------
# Per-worker adapters.
# Each adapter takes (trace, spans) and returns (report, evidence_items) where
#   report         = {"worker", "task_goal", "summary", "findings": [normalized], "gaps", "status", ...}
#   evidence_items = [{"id", "source", "text"}]  (what the worker actually retrieved/cited)
# Add a new worker by writing an adapter and calling register_worker().
# ---------------------------------------------------------------------------
def _adapt_customer_trends(trace: dict, spans: list[dict]) -> tuple[dict, list[dict]]:
    results = trace.get("results")
    result = next((v for v in results.values() if isinstance(v, dict) and "findings" in v), {}) if isinstance(results, dict) else {}
    brief = result.get("brief", {}) if isinstance(result.get("brief"), dict) else {}
    run_out = _find_span_output(spans, "customer_trends.run_worker")
    submit = _find_span_output(spans, "customer_trends.submit")
    raw = result.get("findings") or trace.get("findings") or []   # trace["findings"] = legacy shape
    findings = [_finding(f.get("id"), f["claim"], category=f.get("type"), confidence=f.get("confidence"),
                         caveats=f.get("caveats"), evidence_ids=[str(e) for e in f.get("evidence_ids") or []])
                for f in raw if isinstance(f, dict) and _text(f.get("claim"))]
    if not findings:   # fall back to run_worker output (WorkerResult shape)
        for f in run_out.get("findings") or []:
            if isinstance(f, dict) and _text(f.get("statement")):
                findings.append(_finding(f.get("finding_id"), f["statement"], category=f.get("category"),
                                         confidence=f.get("confidence"), caveats=f.get("limitations"),
                                         evidence_ids=[str(r.get("evidence_id")) for r in f.get("evidence_refs") or [] if isinstance(r, dict)]))
    gaps = result.get("gaps") or trace.get("gaps") or run_out.get("gaps") or []
    seen: dict[str, dict] = {}
    for out in _span_outputs(spans, "customer_trends.evidence_query"):
        for it in out.get("items") or []:
            if isinstance(it, dict) and it.get("id") not in seen:
                label = " | ".join(str(x) for x in (it.get("platform"), it.get("published"), it.get("url")) if x)
                item = _evidence_item(it.get("id"), it.get("text"), label)
                if item:
                    seen[it["id"]] = item
    headline = _text(submit.get("headline"))
    report = {"worker": "customer_trends", "task_goal": _text(brief.get("question")) or _text(trace.get("task_goal")),
              "summary": headline, "findings": findings, "gaps": gaps,
              "status": run_out.get("status") or trace.get("status") or "unknown",
              "confidence": run_out.get("confidence"), "accepted_count": submit.get("accepted_count"),
              "rejected_count": submit.get("rejected_count")}
    return report, list(seen.values())


def _adapt_market_intelligence(trace: dict, spans: list[dict]) -> tuple[dict, list[dict]]:
    final = _find_span_output(spans, "output_node", last=True).get("final_report")
    final = final if isinstance(final, dict) else {}
    findings, evidence, seen = [], [], set()
    for i, f in enumerate(final.get("market_findings") or [], 1):
        if not isinstance(f, dict) or not _text(f.get("claim")):
            continue
        refs = []
        for e in f.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            src = str(e.get("source") or "")
            refs.append(src)
            key = (src, _text(e.get("text"))[:80])
            if key not in seen:
                seen.add(key)
                item = _evidence_item(src, e.get("text"), src)
                if item:
                    evidence.append(item)
        findings.append(_finding(f"M{i}", f["claim"], confidence=f.get("confidence"), quote=_text(f.get("quote")),
                                 task=f.get("task"), evidence_ids=refs, sources=refs))
    ts = final.get("task_summary") or {}
    summary = (f"{len(findings)} findings; research tasks: {ts.get('done', '?')} done, {ts.get('skipped', '?')} skipped "
               f"of {ts.get('total', '?')}.") if ts else ""
    report = {"worker": "market_intelligence", "task_goal": _text(final.get("goal")), "summary": summary,
              "business_context": _text(final.get("business_context"))[:1500], "findings": findings,
              "gaps": final.get("gaps") or [], "status": "completed" if findings else "no_findings"}
    return report, evidence


def _adapt_competitor_intelligence(trace: dict, spans: list[dict]) -> tuple[dict, list[dict]]:
    final = _find_span_output(spans, "competitor_intelligence.final_report", last=True).get("final_report")
    final = final if isinstance(final, dict) else {}
    status_by_req: dict[tuple[str, str], str] = {}
    for comp, profile in (final.get("competitor_profiles") or {}).items():
        for req in (profile.get("requirements") or []) if isinstance(profile, dict) else []:
            if isinstance(req, dict):
                status_by_req[(comp, str(req.get("requirement_id")))] = str(req.get("status"))
    verify = _span_outputs(spans, "competitor_intelligence.verify")
    verified = verify[-1].get("verified_findings", []) if verify else []   # last stage is cumulative
    findings, evidence, seen = [], [], set()
    for i, f in enumerate(verified, 1):
        if not isinstance(f, dict) or not _text(f.get("finding")):
            continue
        urls = []
        for e in f.get("evidence") or []:
            if isinstance(e, dict):
                url = str(e.get("source_url") or "")
                urls.append(url)
                if (url, _text(e.get("supporting_text"))[:80]) not in seen:
                    seen.add((url, _text(e.get("supporting_text"))[:80]))
                    item = _evidence_item(url, e.get("supporting_text"), e.get("source_title") or url)
                    if item:
                        evidence.append(item)
        rid, comp = str(f.get("requirement_id")), f.get("competitor")
        findings.append(_finding(f"{comp}:{rid}" if comp else f"V{i}", f["finding"], category=f.get("category"),
                                 confidence=f.get("confidence"), competitor=comp,
                                 requirement_status=status_by_req.get((comp, rid)),
                                 caveats=[f"evidence_quality: {f['evidence_quality']}"] if f.get("evidence_quality") else None,
                                 evidence_ids=urls, sources=urls))
    if not findings:   # fall back to the final profiles
        for comp, profile in (final.get("competitor_profiles") or {}).items():
            for req in (profile.get("requirements") or []) if isinstance(profile, dict) else []:
                summary = _loads_maybe(req.get("summary")) if isinstance(req, dict) else None
                if _text(summary):
                    findings.append(_finding(f"{comp}:{req.get('requirement_id')}", summary, competitor=comp,
                                             requirement_status=req.get("status"), confidence=req.get("confidence")))
    overviews = {c: _text(p.get("overview")) for c, p in (final.get("competitor_profiles") or {}).items() if isinstance(p, dict)}
    gaps = [f"{g.get('competitor')}/{g.get('requirement_id')}: {g.get('missing_reason')}"
            for g in final.get("remaining_unresolved_requirements") or [] if isinstance(g, dict)]
    report = {"worker": "competitor_intelligence", "task_goal": _text(final.get("research_goal")),
              "summary": " ".join(v for v in overviews.values() if v), "findings": findings, "gaps": gaps,
              "competitors": final.get("competitors"), "status": "completed" if findings else "no_findings"}
    return report, evidence


def _adapt_internal_intelligence(trace: dict, spans: list[dict]) -> tuple[dict, list[dict]]:
    plan_in = next((s.get("input") for s in spans if isinstance(s, dict) and isinstance(s.get("input"), dict)
                    and s["input"].get("task_goal")), {}) or {}
    analysis = _find_span_output(spans, "internal_intelligence.analyze_and_synthesize")
    result = _find_span_output(spans, "internal_intelligence.format_worker_result").get("worker_result")
    result = result if isinstance(result, dict) else {}
    # analyze_and_synthesize keeps full finding detail; worker_result is truncated by the tracer ("<max depth>").
    raw = analysis.get("findings") or result.get("findings") or []
    findings = []
    for f in raw:
        if not isinstance(f, dict) or not _text(f.get("statement")):
            continue
        lims = [str(x) for x in f.get("limitations") or []]
        quote = next((x.split(":", 1)[1].strip() for x in lims if x.lower().startswith("supporting quote:")), "")
        refs = [str(r.get("evidence_id")) if isinstance(r, dict) else str(r) for r in f.get("evidence_refs") or []]
        findings.append(_finding(f.get("finding_id"), f["statement"], category=f.get("category"),
                                 confidence=f.get("confidence"), quote=quote, evidence_ids=refs,
                                 caveats=[x for x in lims if not x.lower().startswith("supporting quote:")]))
    evidence = []
    for e in analysis.get("retrieved_evidence") or result.get("evidence") or []:
        if isinstance(e, dict):
            item = _evidence_item(e.get("evidence_id"), e.get("excerpt") or e.get("extracted_fact"),
                                  e.get("source_title") or e.get("source_url"))
            if item:
                evidence.append(item)
    report = {"worker": "internal_intelligence", "task_goal": _text(plan_in.get("task_goal")),
              "summary": _text(analysis.get("summary")), "findings": findings,
              "gaps": analysis.get("limitations_and_gaps") or result.get("gaps") or [],
              "conflicts": analysis.get("conflicts") or result.get("conflicts") or [],
              "status": result.get("status") or "unknown", "confidence": result.get("confidence")}
    return report, evidence


def _adapt_generic(trace: dict, spans: list[dict]) -> tuple[dict, list[dict]]:
    """Best-effort adapter for a worker with no registered adapter."""
    for s in reversed(spans):
        out = s.get("output") if isinstance(s, dict) else None
        if isinstance(out, dict):
            for key in ("final_report", "worker_result", "result", "report"):
                if isinstance(out.get(key), dict):
                    rep = out[key]
                    raw = next((rep[k] for k in ("findings", "market_findings", "key_findings") if isinstance(rep.get(k), list)), [])
                    findings = [_finding(i, next((_text(f.get(k)) for k in ("claim", "statement", "finding", "summary") if _text(f.get(k))), ""))
                                for i, f in enumerate(raw, 1) if isinstance(f, dict)]
                    findings = [f for f in findings if f["claim"]]
                    return ({"worker": trace.get("worker", "unknown"), "task_goal": _text(rep.get("goal") or rep.get("task_goal")),
                             "summary": _text(rep.get("summary") or rep.get("headline")), "findings": findings,
                             "gaps": rep.get("gaps") or [], "status": rep.get("status", "unknown")}, [])
    raise ValueError("No recognizable final result in trace; register an adapter with register_worker().")


# name -> {"adapter": fn, "signature_spans": span names that identify the worker when trace has no "worker" key,
#          "quality_focus": worker-specific emphasis added to the judge criteria}
WORKER_ADAPTERS: dict[str, dict[str, Any]] = {
    "customer_trends": {
        "adapter": _adapt_customer_trends, "signature_spans": set(),
        "quality_focus": "customer sentiment, needs and adoption patterns; check that the evidence breadth (platforms, languages, time range) actually supports the stated conclusions."},
    "market_intelligence": {
        "adapter": _adapt_market_intelligence, "signature_spans": {"task_planner", "output_node"},
        "quality_focus": "market size, growth, adoption, regulatory and technology signals; check that figures, years, geographies and units match the cited sources."},
    "competitor_intelligence": {
        "adapter": _adapt_competitor_intelligence, "signature_spans": set(),
        "quality_focus": "competitor capabilities, pricing, availability and adoption; check that each fact is attributed to the right competitor and that requirement status (fulfilled/missing) is consistent with the claims made."},
    "internal_intelligence": {
        "adapter": _adapt_internal_intelligence, "signature_spans": set(),
        "quality_focus": "facts drawn from internal documents; check that internal planning text is not presented as verified external fact and that missing data is disclosed."},
}


def register_worker(name: str, adapter: Callable, signature_spans: set[str] | None = None, quality_focus: str = "") -> None:
    """Plug in a new worker without touching the rest of the evaluator."""
    WORKER_ADAPTERS[name] = {"adapter": adapter, "signature_spans": set(signature_spans or ()), "quality_focus": quality_focus}


def detect_worker(trace: dict[str, Any]) -> str | None:
    worker = trace.get("worker")
    if worker in WORKER_ADAPTERS:
        return worker
    names = {str(s.get("name", "")) for s in trace.get("spans", []) or [] if isinstance(s, dict)}
    for candidate, spec in WORKER_ADAPTERS.items():
        if any(n.startswith(candidate + ".") for n in names):
            return candidate
        if spec["signature_spans"] and spec["signature_spans"] <= names:
            return candidate
    return worker if isinstance(worker, str) and worker else None


BOILERPLATE = ("no source found", "not established by the verified evidence", "information was not established",
               "not stated", "no evidence found", "missing information")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(gt: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge defaults, ground-truth evaluation_config, then explicit config JSON."""
    from_gt = gt.get("evaluation_config", {})
    merged = _deep_merge(DEFAULT_CONFIG, from_gt if isinstance(from_gt, dict) else {})
    return _deep_merge(merged, config or {})


def read_json(path: str) -> dict[str, Any]:
    """Read object JSON, or normalize a raw span-list trace to {"spans": [...]} ."""
    with Path(path).open("r", encoding="utf-8-sig") as f:
        value = json.load(f)
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return {"spans": value}
        raise ValueError(f"Expected a JSON object or a list of span objects in {path}")
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object or a list of span objects in {path}")
    return value


def extract_report_and_evidence(trace: dict[str, Any], worker: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize any supported worker trace into (report, evidence_items)."""
    if not isinstance(trace, dict):
        raise ValueError("Trace must be a JSON object.")
    worker = worker or detect_worker(trace)
    spans = [s for s in trace.get("spans", []) or [] if isinstance(s, dict)]
    spec = WORKER_ADAPTERS.get(worker or "")
    adapter = spec["adapter"] if spec else _adapt_generic
    report, items = adapter(trace, spans)
    report.setdefault("worker", worker or "unknown")
    if not report.get("summary") and not report.get("findings"):
        raise ValueError(f"No summary or findings found in {report['worker']} trace.")
    return report, items


def extract_report(trace: dict[str, Any], worker: str | None = None) -> dict[str, Any]:
    return extract_report_and_evidence(trace, worker)[0]


def format_evidence(items: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[str]:
    """Render evidence items as judge-ready strings; per-item and total caps keep every source represented."""
    cfg = load_config({}, config)
    per_item = int(cfg["trace"].get("max_evidence_item_chars", 1200))
    budget = int(cfg["retrieval"].get("max_evidence_chars", 18000))
    out, used = [], 0
    for it in items:
        head = f"[{it.get('id') or 'evidence'}] {it.get('source') or ''}".strip()
        chunk = f"{head}\n{it['text'][:per_item]}"
        if used + len(chunk) > budget:
            break
        out.append(chunk)
        used += len(chunk)
    return out


def extract_evidence(trace: dict[str, Any], config: dict[str, Any] | None = None, worker: str | None = None) -> list[str]:
    """Evidence strings for the judge: adapter evidence first, generic payload-key discovery as fallback."""
    cfg = load_config({}, config)
    try:
        _, items = extract_report_and_evidence(trace, worker)
    except ValueError:
        items = []
    if items:
        return format_evidence(items, cfg)
    return _generic_extract_evidence(trace, cfg)


def _generic_extract_evidence(trace: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """Fallback: configurable span names / payload keys, JSON-string outputs decoded."""
    trace_cfg = cfg["trace"]
    names = {str(x).casefold() for x in trace_cfg.get("evidence_span_names", [])}
    payload_keys = {str(x).casefold() for x in trace_cfg.get("evidence_payload_keys", [])}
    chunks: list[str] = []
    for index, span in enumerate(trace.get("spans", []) or []):
        if not isinstance(span, dict):
            continue
        if names and str(span.get("name", "")).casefold() not in names:
            continue
        output = _loads_maybe(span.get("output", {}))
        if isinstance(output, list):
            output = {"items": output}
        if not isinstance(output, dict):
            continue
        for key, value in output.items():
            if str(key).casefold() in payload_keys:
                _append_evidence(chunks, value, f"spans[{index}].output.{key}")
        if trace_cfg.get("recursive_evidence_discovery", True):
            _walk_evidence(output, f"spans[{index}].output", chunks, payload_keys)
    selected, total = [], 0
    max_chars = int(cfg["retrieval"].get("max_evidence_chars", 18000))
    for chunk in chunks:
        remaining = max_chars - total
        if remaining <= 0:
            break
        selected.append(chunk[:remaining])
        total += min(len(chunk), remaining)
    return selected


# ---------------------------------------------------------------------------
# Deterministic grounding checks (no LLM): cheap, repeatable, worker-agnostic.
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM_RE.findall(text) if len(n.replace(",", "")) >= 2}


def _ratio(values: list[bool | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 4) if known else None


def grounding_checks(report: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Per-finding checks against the evidence the worker retrieved:
      has_evidence_reference  finding cites at least one evidence id / source
      references_resolve      every cited id/source exists in the extracted evidence (None = not evaluable)
      quote_found             the finding's supporting quote appears in the evidence (None = no quote)
      unsupported_numbers     figures in the claim that do not appear in the cited evidence (None = not evaluable)
      status_claim_conflict   competitor requirement marked 'missing' yet a claim is reported for it
    """
    by_key: dict[str, str] = {}
    for it in evidence_items:
        for key in (it.get("id"), it.get("source")):
            if key:
                by_key[str(key)] = by_key.get(str(key), "") + " " + it["text"]
    corpus = _norm(" ".join(it["text"] for it in evidence_items))
    rows = []
    for f in [x for x in report.get("findings", []) if isinstance(x, dict)]:
        refs = [str(r) for r in (f.get("evidence_ids") or []) + (f.get("sources") or []) if r]
        truncated = [r for r in refs if _is_truncated(r)]
        real = list(dict.fromkeys(r for r in refs if r not in truncated))
        resolves = None if not real else all(r in by_key for r in real)
        quote = _text(f.get("quote"))
        quote_found = None
        if quote and evidence_items:
            segs = [s for s in re.split(r"\.\.\.|…", quote) if len(_norm(s)) >= 15]
            quote_found = all(_norm(s) in corpus for s in segs) if segs else None
        cited_text = " ".join(by_key[r] for r in real if r in by_key)
        unsupported = None
        if cited_text:
            cited_nums = _numbers(cited_text)
            unsupported = sorted(n for n in _numbers(f.get("claim", "")) if n not in cited_nums)
        rows.append({"finding_id": f.get("id"),
                     "has_evidence_reference": bool(refs),
                     "references_resolve": resolves,
                     "reference_note": "trace truncated evidence ids ('<max depth>'); not evaluable" if truncated and not real else None,
                     "quote_found": quote_found,
                     "unsupported_numbers": unsupported,
                     "status_claim_conflict": (str(f.get("requirement_status")).casefold() == "missing") if f.get("requirement_status") else None})
    confidences = [float(f["confidence"]) for f in report.get("findings", []) if isinstance(f.get("confidence"), (int, float))]
    return {"finding_count": len(rows), "evidence_item_count": len(evidence_items),
            "gap_count": len(report.get("gaps") or []),
            "evidence_reference_rate": _ratio([r["has_evidence_reference"] for r in rows]),
            "reference_resolution_rate": _ratio([r["references_resolve"] for r in rows]),
            "quote_verification_rate": _ratio([r["quote_found"] for r in rows]),
            "findings_with_unsupported_numbers": sum(1 for r in rows if r["unsupported_numbers"]),
            "status_claim_conflicts": sum(1 for r in rows if r["status_claim_conflict"]),
            "mean_numeric_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
            "per_finding": rows,
            "note": "Deterministic string checks against extracted evidence; they flag review candidates, not proof of truth or falsehood."}



def report_to_text(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def expected_findings(gt: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for case in gt.get("test_cases", []) or []:
        if not isinstance(case, dict):
            continue
        meta = case.get("test_case", {}) or {}
        golden = case.get("golden_reference", {}) or {}
        for finding in golden.get("expected_findings", []) or []:
            if isinstance(finding, dict):
                item = dict(finding)
                item["_test_case_id"] = meta.get("id", "UNKNOWN") if isinstance(meta, dict) else "UNKNOWN"
                item["_test_case_title"] = meta.get("title", "") if isinstance(meta, dict) else ""
                findings.append(item)
    return findings


def _append_evidence(chunks: list[str], value: Any, label: str, limit: int = 6000) -> None:
    if value:
        try:
            chunks.append(json.dumps({label: value}, ensure_ascii=False, default=str)[:limit])
        except (TypeError, ValueError):
            chunks.append(f"{label}: {str(value)[:limit]}")


def _walk_evidence(obj: Any, path: str, chunks: list[str], payload_keys: set[str], depth: int = 0) -> None:
    if depth > 12 or len(chunks) >= 200:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).casefold()
            child = f"{path}.{key}"
            if k in payload_keys:
                _append_evidence(chunks, value, child)
            elif isinstance(value, (dict, list)):
                _walk_evidence(value, child, chunks, payload_keys, depth + 1)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            if isinstance(value, (dict, list)):
                _walk_evidence(value, f"{path}[{i}]", chunks, payload_keys, depth + 1)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _simple_overlap(report_text: str, finding: dict[str, Any]) -> float:
    expected = " ".join([str(finding.get("primary_claim", finding.get("finding", ""))),
                         " ".join(str(x) for x in finding.get("acceptable_alternatives", []) or []),
                         " ".join(str(x) for x in finding.get("acceptance_criteria", []) or [])])
    report_tokens = set(_norm(report_text).split())
    expected_tokens = {t for t in _norm(expected).split() if len(t) > 2}
    return len(report_tokens & expected_tokens) / len(expected_tokens) if expected_tokens else 0.0


def _finding_text(finding: dict[str, Any]) -> str:
    return "\n".join([
        f"ID: {finding.get('id', '')}",
        f"Required finding: {finding.get('finding', finding.get('primary_claim', ''))}",
        f"Primary claim: {finding.get('primary_claim', '')}",
        f"Acceptable alternatives: {json.dumps(finding.get('acceptable_alternatives', []) or [], ensure_ascii=False)}",
        f"Acceptance criteria: {json.dumps(finding.get('acceptance_criteria', []) or [], ensure_ascii=False)}",
        f"Priority: {finding.get('priority', 'mandatory')}",
    ])


def _finding_result_base(finding: dict[str, Any]) -> dict[str, Any]:
    return {"finding_id": finding.get("id"), "requirement_id": finding.get("requirement_id"),
            "test_case_id": finding.get("_test_case_id"), "title": finding.get("finding", finding.get("primary_claim", "")),
            "priority": finding.get("priority", "mandatory")}


def _entity_list(gt: dict[str, Any], key: str, fallback: list[str] | None = None) -> list[str]:
    scope = gt.get("scope_conflict", {}) or {}
    value = scope.get(key)
    if value is None:
        value = fallback or []
    return [str(v) for v in value] if isinstance(value, list) else []


def scope_checks(report: dict[str, Any], gt: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generic scope checks configured by the ground-truth dataset and evaluator config."""
    cfg = load_config(gt, config)["scope"]
    text = report_to_text(report)
    lower = text.casefold()
    scope = gt.get("scope_conflict", {}) or {}
    included = [str(x) for x in (scope.get("required_entities") or scope.get("configured_competitors") or [])]
    comparison = [str(x) for x in (scope.get("comparison_targets") or [])]
    excluded = [str(x) for x in (scope.get("excluded_entities") or cfg.get("excluded_entities", []))]
    presence = {name: name.casefold() in lower for name in included}
    labels = [str(x).casefold() for x in (scope.get("exclusion_label_terms") or cfg.get("exclusion_labels", ["out of scope", "excluded", "not in scope", "outside the scope", "not a target competitor"]))]
    violations = []
    excluded_mentions = {}
    for entity in excluded:
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(entity) + r"(?![A-Za-z0-9])", re.I)
        matches = list(pattern.finditer(text))
        excluded_mentions[entity] = len(matches)
        snippets = [text[max(0,m.start()-120):min(len(text),m.end()+180)].casefold() for m in matches]
        labelled = any(any(label in snippet for label in labels) for snippet in snippets)
        if matches and cfg.get("require_exclusion_label", scope.get("require_exclusion_label", False)) and not labelled:
            violations.append({"entity": entity, "mentions": len(matches), "exclusion_label_found": False})
    require_presence = cfg.get("require_in_scope_presence", True)
    return {"enabled": bool(cfg.get("enabled", True)), "required_entities": included,
            "comparison_targets": comparison, "entity_presence": presence,
            "all_required_entities_present": all(presence.values()) if presence and require_presence else True,
            # Compatibility aliases for existing evaluator consumers.
            "all_in_scope_competitors_present": all(presence.values()) if presence and require_presence else True,
            "competitor_presence": presence,
            "excluded_entities": excluded, "excluded_entity_mentions": excluded_mentions,
            "scope_violations": violations, "scope_policy_pass": not violations,
            "policy": scope.get("policy", "")}

def _judge_status(score: float | None, config: dict[str, Any] | None = None) -> str:
    if score is None:
        return "error"
    cfg = load_config({}, config)["thresholds"]
    if score >= float(cfg.get("covered", 0.7)):
        return "covered"
    if score >= float(cfg.get("partial", 0.4)):
        return "partial"
    return "missing"


def _metric_score_reason(metric: Any) -> tuple[float | None, str | None]:
    score = getattr(metric, "score", None)
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return score, getattr(metric, "reason", None)


def _openai_judge_settings(config: dict[str, Any]) -> dict[str, Any]:
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    judge_cfg = config.get("judge", {})
    provider = str(judge_cfg.get("provider", "openai")).casefold()
    if provider != "openai":
        raise ValueError(f"Unsupported judge provider {provider!r}; supported provider: 'openai'.")
    api_key = judge_cfg.get("api_key") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to the repository .env or define it in the environment.")
    return {
        "model_name": (
            judge_cfg.get("model")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("DEEPEVAL_MODEL")
            or "gpt-4o-mini"
        ),
        "base_url": (
            judge_cfg.get("base_url")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/"),
        "api_key": api_key,
        "timeout": float(os.getenv("OPENAI_TIMEOUT_S", "120")),
    }


def _make_openai_judge(config: dict[str, Any]):
    return OpenAIDeepEvalModel(**_openai_judge_settings(config))


def _make_geval(name: str, criteria: str, config: dict[str, Any], params: list[Any]):
    from deepeval.metrics import GEval

    kwargs: dict[str, Any] = {
        "name": name,
        "criteria": criteria,
        "evaluation_params": params,
        "threshold": float(config.get("thresholds", {}).get("covered", 0.7)),
        "model": _make_openai_judge(config),
    }
    return GEval(**kwargs)


def _judge_criterion(report_text: str, finding: dict[str, Any], criterion: str, evidence: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Independently judge one acceptance criterion; returns a normalized criterion result."""
    try:
        from deepeval.test_case import LLMTestCase, SingleTurnParams
        evidence_text = "\n\n".join(evidence)[:int(config["retrieval"].get("max_evidence_chars", 18000))] if evidence else "No trace evidence context extracted."
        case = LLMTestCase(input=f"Evaluate this single criterion for finding {finding.get('id', '')}: {criterion}",
                           actual_output=report_text[:int(config["retrieval"].get("max_report_chars", 30000))],
                           expected_output=_finding_text(finding), retrieval_context=[evidence_text])
        metric = _make_geval("AcceptanceCriterionCoverage",
            "Judge only whether the actual report explicitly and substantively satisfies this criterion. Do not use outside knowledge. "
            "Related-topic mentions do not satisfy a criterion. Do not treat retrieval context as report content. "
            "Return a high score only when the criterion is clearly present in the report; partial details receive an intermediate score. "
            f"Criterion to evaluate: {criterion}", config,
            [SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT])
        metric.measure(case)
        score, reason = _metric_score_reason(metric)
        return {"criterion": criterion, "score": score, "status": _judge_status(score, config), "reason": reason}
    except Exception as exc:
        return {"criterion": criterion, "score": None, "status": "error", "reason": f"Criterion judge failed: {type(exc).__name__}: {exc}"}


def judge_one_finding(report_text: str, finding: dict[str, Any], evidence: list[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate every acceptance criterion independently and aggregate to a finding status."""
    cfg = load_config({}, config)
    base = _finding_result_base(finding)
    criteria = finding.get("acceptance_criteria", []) or []
    if isinstance(criteria, str):
        criteria = [criteria]
    criteria = [str(c) for c in criteria if str(c).strip()]
    if not criteria:
        criteria = [str(finding.get("primary_claim", finding.get("finding", "Required finding")))]
    results = [_judge_criterion(report_text, finding, criterion, evidence, cfg) for criterion in criteria]
    scoreable = [r for r in results if r["score"] is not None]
    score = sum(r["score"] for r in scoreable) / len(scoreable) if scoreable else None
    # A compound finding cannot be fully covered if any criterion is missing or erroneous.
    statuses = [r["status"] for r in results]
    if any(s == "error" for s in statuses):
        status = "error" if not scoreable else "partial"
    elif all(s == "covered" for s in statuses):
        status = "covered"
    elif all(s == "missing" for s in statuses):
        status = "missing"
    else:
        status = "partial"
    reason = "; ".join(f"{r['status']}: {r['criterion']} — {r.get('reason') or 'no reason supplied'}" for r in results)
    return {**base, "score": score, "status": status, "reason": reason,
            "criteria_results": results, "method": "criterion-level DeepEval G-Eval"}


def _split_sentences(text: str) -> list[str]:
    return [p.strip(" \t\r\n-•") for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip()) if p.strip(" \t\r\n-•")]


def extract_claim_candidates(report: dict[str, Any], max_claims: int = 40, config: dict[str, Any] | None = None) -> list[str]:
    cfg = load_config({}, config)["claims"]
    max_claims = int(cfg.get("max_claims", max_claims))
    claim_keys = {str(k).casefold() for k in cfg.get("claim_keys", [])}
    claim_list_keys = {str(k).casefold() for k in cfg.get("claim_list_keys", [])}
    overview_keys = {str(k).casefold() for k in cfg.get("overview_keys", [])}
    found: list[str] = []

    def add(value: Any, split: bool = False) -> None:
        if not isinstance(value, str): return
        for claim in (_split_sentences(value) if split else [value.strip()]):
            claim = re.sub(r"\s+", " ", claim).strip()
            if len(claim) < 25 or len(claim) > 1200 or claim.casefold().startswith(BOILERPLATE): continue
            if claim not in found: found.append(claim)

    def walk(obj: Any, parent_key: str = "") -> None:
        if len(found) >= max_claims: return
        if isinstance(obj, dict):
            for key, value in obj.items():
                k = str(key).casefold()
                if k in claim_keys and isinstance(value, str): add(value)
                elif k in overview_keys and isinstance(value, str): add(value, split=True)
                elif k in claim_list_keys and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str): add(item)
                        elif isinstance(item, dict): walk(item, k)
                elif isinstance(value, (dict, list)): walk(value, k)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)): walk(item, parent_key)
                elif isinstance(item, str) and parent_key in claim_list_keys: add(item)
    walk(report)
    return found[:max_claims]


def extract_report_passages(report: dict[str, Any], max_passages: int = 500, max_chars: int = 1400) -> list[dict[str, str]]:
    passages: list[dict[str, str]] = []
    seen: set[str] = set()
    def add(path: str, value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) < 25 or value.casefold().startswith(BOILERPLATE): return
        pieces = _split_sentences(value) if len(value) > max_chars or value.count(". ") >= 2 else [value]
        for piece in pieces:
            if len(piece) < 25: continue
            if len(piece) > max_chars: piece = piece[:max_chars].rsplit(" ", 1)[0]
            key = _norm(piece)
            if key and key not in seen:
                seen.add(key); passages.append({"source_path": path, "text": piece})
                if len(passages) >= max_passages: return
    def walk(obj: Any, path: str = "$", depth: int = 0) -> None:
        if len(passages) >= max_passages or depth > 30: return
        if isinstance(obj, dict):
            for key, value in obj.items():
                child = f"{path}.{key}"
                if isinstance(value, str): add(child, value)
                elif isinstance(value, (dict, list)): walk(value, child, depth + 1)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                if isinstance(value, str): add(f"{path}[{i}]", value)
                elif isinstance(value, (dict, list)): walk(value, f"{path}[{i}]", depth + 1)
    walk(report)
    return passages[:max_passages]


def _token_vector(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for token in [t for t in _norm(text).split() if len(t) > 2]: counts[token] = counts.get(token, 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in counts.values()))
    return {k: v / norm for k, v in counts.items()} if norm else {}


def _lexical_similarity(a: str, b: str) -> float:
    va, vb = _token_vector(a), _token_vector(b)
    return sum(value * vb.get(key, 0.0) for key, value in va.items()) if va and vb else 0.0


_ST_MODELS: dict[tuple[str, str | None], Any] = {}


def _embedding_vectors(texts: list[str], model: str = "all-MiniLM-L6-v2", config: dict[str, Any] | None = None) -> list[list[float]]:
    """Embed texts with sentence-transformers."""
    cfg = load_config({}, config)["embeddings"]
    provider = str(cfg.get("provider", "sentence_transformers")).casefold().replace("-", "_")
    model_name = str(cfg.get("model") or model)
    batch_size = max(1, int(cfg.get("batch_size", 64)))
    if provider in ("sentence_transformers", "sentence_transformer", "sbert"):
        from sentence_transformers import SentenceTransformer
        key = (model_name, cfg.get("device"))
        if key not in _ST_MODELS:   # load once per process
            _ST_MODELS[key] = SentenceTransformer(model_name, device=cfg.get("device"))
        vectors = _ST_MODELS[key].encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return [[float(v) for v in row] for row in vectors]
    raise ValueError(f"Unsupported embedding provider {provider!r}; use 'sentence_transformers'.")

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b): return 0.0
    dot = sum(x * y for x, y in zip(a, b)); na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def retrieve_candidates(finding: dict[str, Any], passages: list[dict[str, str]], passage_embeddings: list[list[float]] | None = None,
                        top_k: int = 5, config: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    if not passages: return [], "no_report_passages"
    query = _finding_text(finding); query_embedding = None; method = "lexical_fallback"
    if passage_embeddings is not None and len(passage_embeddings) == len(passages):
        query_embedding = _embedding_vectors([query], config=config)[0]
        method = "sentence_transformers_cosine"
    ranked = []
    for i, passage in enumerate(passages):
        sim = _cosine(query_embedding, passage_embeddings[i]) if query_embedding is not None and passage_embeddings is not None else _lexical_similarity(query, passage["text"])
        ranked.append({**passage, "similarity": round(float(sim), 6)})
    ranked.sort(key=lambda x: x["similarity"], reverse=True)
    return ranked[:max(1, top_k)], method


def _second_pass_judge(finding: dict[str, Any], candidates: list[dict[str, Any]], initial_result: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_config({}, config); base = _finding_result_base(finding)
    if not candidates:
        return {**base, "score": None, "status": "unresolved_no_candidates", "reason": "No report passages were available for second-pass review.", "method": "second-pass retrieval judge"}
    try:
        from deepeval.test_case import LLMTestCase, SingleTurnParams
        candidate_text = "\n\n".join(f"Passage {i}: {c['source_path']}; similarity={c['similarity']}\n{c['text']}" for i, c in enumerate(candidates, 1))[:int(cfg["retrieval"].get("max_candidates_chars", 14000))]
        criteria = finding.get("acceptance_criteria", []) or []
        case = LLMTestCase(input=f"Reassess {finding.get('id')} after retrieval. Initial status: {initial_result.get('status')}. Use only these report passages.",
                           actual_output=candidate_text, expected_output=_finding_text(finding))
        metric = _make_geval("SecondPassSemanticCoverage",
            "Judge substantive coverage in retrieved report passages only. Similarity is not coverage; do not use outside knowledge. "
            "Check every acceptance criterion and required qualifier. Fully covered requires all criteria; partial means some relevant criteria are met; missing means none are substantively met. "
            f"Acceptance criteria: {json.dumps(criteria, ensure_ascii=False)}", cfg,
            [SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT])
        metric.measure(case); score, reason = _metric_score_reason(metric)
        return {**base, "score": score, "status": _judge_status(score, cfg), "reason": reason, "method": "DeepEval second-pass retrieved passages"}
    except Exception as exc:
        return {**base, "score": None, "status": "error", "reason": f"Second-pass judge failed: {type(exc).__name__}: {exc}", "method": "DeepEval second-pass retrieved passages"}


def _reconcile_finding(initial: dict[str, Any], second: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(initial); result["initial_score"] = initial.get("score"); result["initial_status"] = initial.get("status")
    result["second_pass"] = second; result["second_pass_applied"] = False
    if not second or second.get("status") in ("error", "unresolved_no_candidates"):
        result["final_score"] = initial.get("score"); result["final_status"] = initial.get("status")
        result["reconciliation"] = "initial_result_retained_second_pass_unavailable"; return result
    rank = {"error": -1, "missing": 0, "partial": 1, "covered": 2}
    if initial.get("status") == "error":
        result["final_score"] = initial.get("score"); result["final_status"] = initial.get("status")
        result["reconciliation"] = "initial_error_retained"; return result
    if rank.get(second.get("status"), -1) > rank.get(initial.get("status"), -1):
        result.update(score=second.get("score"), status=second.get("status"), reason=f"Second pass upgraded initial {initial.get('status')} to {second.get('status')}. Initial: {initial.get('reason') or 'N/A'} Second pass: {second.get('reason') or 'N/A'}", method="first pass + second-pass semantic retrieval", second_pass_applied=True, reconciliation="upgraded_by_second_pass")
    elif rank.get(second.get("status"), -1) == rank.get(initial.get("status"), -1): result["reconciliation"] = "both_passes_agree"
    else: result["reconciliation"] = "initial_result_retained_second_pass_lower"
    result["final_score"] = result.get("score"); result["final_status"] = result.get("status")
    return result


def _judge_claim_novelty(claim: str, findings: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_config({}, config)
    try:
        from deepeval.test_case import LLMTestCase, SingleTurnParams
        golden = "\n\n".join(_finding_text(f) for f in findings)[:22000] or "No golden findings provided."
        case = LLMTestCase(input="Classify whether this claim adds a distinct fact beyond the benchmark.", actual_output=claim, expected_output=golden)
        metric = _make_geval("AdditionalClaimNovelty",
            "Compare claims at the fact level. Paraphrase of same fact is represented; a distinct customer, date, figure, specification, deployment status, or forecast can be additional. A changed value for the same metric/period may be contradiction, not novelty. Do not judge truth. Explain uncertainty clearly.", cfg,
            [SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT])
        metric.measure(case); score, reason = _metric_score_reason(metric)
        status = "error" if score is None else "additional_candidate" if score >= float(cfg["thresholds"].get("covered", .7)) else "already_represented_in_golden" if score < float(cfg["thresholds"].get("partial", .4)) else "novelty_uncertain"
        return {"score": score, "status": status, "reason": reason}
    except Exception as exc: return {"score": None, "status": "error", "reason": f"Novelty judge failed: {type(exc).__name__}: {exc}"}


def _judge_claim_support(claim: str, evidence: list[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_config({}, config)
    if not evidence: return {"score": None, "status": "unverified_no_evidence_context", "reason": "No evidence context was extracted; this is not proof the claim is false."}
    try:
        from deepeval.test_case import LLMTestCase, SingleTurnParams
        case = LLMTestCase(input="Assess whether trace evidence supports or contradicts this report claim.", actual_output=claim,
                           expected_output="Assess evidence support using retrieval_context only.", retrieval_context=["\n\n".join(evidence)[:int(cfg["retrieval"].get("max_evidence_chars", 18000))]])
        metric = _make_geval("AdditionalClaimEvidenceSupport", "Use only retrieval_context as evidence. Do not infer support from topic similarity. Insufficient context means unverified, not false. Score high only for direct relevant support.", cfg,
                             [SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT])
        metric.measure(case); score, reason = _metric_score_reason(metric)
        status = "error" if score is None else "supported_in_trace" if score >= float(cfg["thresholds"].get("covered", .7)) else "partially_supported_or_uncertain" if score >= float(cfg["thresholds"].get("partial", .4)) else "unsupported_or_contradicted_in_trace"
        return {"score": score, "status": status, "reason": reason}
    except Exception as exc: return {"score": None, "status": "error", "reason": f"Evidence-support judge failed: {type(exc).__name__}: {exc}"}


def assess_additional_claims(report: dict[str, Any], findings: list[dict[str, Any]], evidence: list[str], use_judge: bool, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    claims = extract_claim_candidates(report, config=config); results = []
    for idx, claim in enumerate(claims, 1):
        item: dict[str, Any] = {"claim_id": f"CLAIM-{idx:03d}", "claim": claim}
        if not use_judge:
            item.update(novelty_status="not_assessed_no_judge", novelty_score=None, evidence_status="not_assessed_no_judge", evidence_score=None,
                        note="No claim is labelled novel, correct, or false in no-judge mode."); results.append(item); continue
        if not findings:   # no benchmark: novelty is meaningless, so check every claim against the trace evidence
            support = _judge_claim_support(claim, evidence, config)
            item.update(novelty_status="not_applicable_no_benchmark", novelty_score=None,
                        evidence_status=support["status"], evidence_score=support["score"], evidence_reason=support["reason"])
            results.append(item)
            continue
        novelty = _judge_claim_novelty(claim, findings, config)
        item.update(novelty_status=novelty["status"], novelty_score=novelty["score"], novelty_reason=novelty["reason"])
        if novelty["status"] == "additional_candidate":
            support = _judge_claim_support(claim, evidence, config)
            item.update(evidence_status=support["status"], evidence_score=support["score"], evidence_reason=support["reason"])
        else: item.update(evidence_status="not_run_not_classified_as_additional", evidence_score=None)
        results.append(item)
    return results



# ---------------------------------------------------------------------------
# Worker-result quality (any worker): relevancy, correctness, faithfulness, completeness.
# ---------------------------------------------------------------------------

_QUALITY_BASE: dict[str, str] = {
    "relevancy": (
        "Does the result directly answer the research goal given as the input? Judge whether the findings stay on the "
        "requested entities, markets, and topics, and whether off-topic material is padded in. {focus}"
    ),
    "correctness": (
        "Are the reported facts, figures, dates, attributions, and conclusions consistent with {reference}? "
        "Check numbers, units, entities, and whether conclusions follow from the data. Do not use outside knowledge. "
        "If evidence is insufficient, treat a claim as unverified rather than automatically false. {focus}"
    ),
    "faithfulness": (
        "Are claims supported by the retrieved evidence, without invented details? Penalize unsupported figures, "
        "fabricated or misattributed facts, and conclusions stronger than the evidence allows. Use only the supplied "
        "trace evidence. If evidence is unavailable, faithfulness cannot be established from this trace. {focus}"
    ),
    "completeness": (
        "Does the result cover what the research goal asks for - every requested entity, market, and dimension - or "
        "clearly disclose what is missing? Mentioning an item is not covering it; check substantive coverage and "
        "whether the stated gaps are honest. {reference_completeness}"
    ),
}
WORKER_QUALITY_CRITERIA = _QUALITY_BASE   # metric names, kept for config compatibility


def _quality_criteria(worker: str | None, has_expected: bool) -> dict[str, str]:
    """Goal-relative criteria with a per-worker emphasis; benchmark language only when a benchmark exists."""
    focus = WORKER_ADAPTERS.get(worker or "", {}).get("quality_focus", "")
    focus = f"Worker-specific emphasis: {focus}" if focus else ""
    reference = ("the supplied benchmark findings and the available trace evidence" if has_expected
                 else "the available trace evidence (no benchmark was supplied)")
    ref_completeness = ("Also consider whether the benchmark's expected findings are represented." if has_expected else "")
    return {k: v.format(focus=focus, reference=reference, reference_completeness=ref_completeness).strip()
            for k, v in _QUALITY_BASE.items()}


def _worker_quality_task(gt: dict[str, Any], report: dict[str, Any]) -> str:
    """The research goal the worker was actually given (from the trace), else GT/report metadata."""
    if isinstance(report, dict) and isinstance(report.get("task_goal"), str) and report["task_goal"].strip():
        return report["task_goal"].strip()
    for case in gt.get("test_cases", []) or []:
        if not isinstance(case, dict):
            continue
        meta = case.get("test_case", {}) or {}
        if isinstance(meta, dict):
            for key in ("input", "task", "goal", "user_query", "question", "prompt"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    for obj in (gt, report):
        for key in ("task_goal", "task", "goal", "user_query", "question", "research_question", "prompt"):
            value = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()

    return f"Evaluate the {report.get('worker', 'research')} worker result."


def _worker_quality_expected(gt: dict[str, Any]) -> str:
    """Serialize benchmark findings as the expected reference for quality judging."""
    expected: list[dict[str, Any]] = []
    for case in gt.get("test_cases", []) or []:
        if not isinstance(case, dict):
            continue
        golden = case.get("golden_reference", {}) or {}
        for finding in golden.get("expected_findings", []) or []:
            if isinstance(finding, dict):
                expected.append(finding)
    if not expected:
        return "No golden expected findings were supplied."
    return json.dumps(expected, ensure_ascii=False, indent=2, default=str)


def evaluate_worker_quality(
    report: dict[str, Any],
    gt: dict[str, Any],
    evidence: list[str],
    use_judge: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score relevancy, correctness, faithfulness, and completeness against the worker's own research goal.

    Reuses this module's load_config(), _make_geval(), and
    _metric_score_reason(); no cross-module imports or circular imports.
    """
    cfg = load_config(gt, config)
    quality_cfg = cfg.get("worker_quality", {}) or {}
    default_flags = {
        "relevancy": True,
        "correctness": True,
        "faithfulness": True,
        "completeness": True,
    }
    flags = {**default_flags, **(quality_cfg.get("metrics", {}) or {})}
    enabled_names = [name for name in WORKER_QUALITY_CRITERIA if flags.get(name, True)]

    if not quality_cfg.get("enabled", True):
        return {
            "enabled": False,
            "metric_count": 0,
            "scoreable_metric_count": 0,
            "overall_score": None,
            "overall_status": "not_run",
            "metrics": {},
        }

    if not use_judge:
        return {
            "enabled": True,
            "metric_count": len(enabled_names),
            "scoreable_metric_count": 0,
            "overall_score": None,
            "overall_status": "not_assessed_no_judge",
            "metrics": {
                name: {
                    "score": None,
                    "status": "not_assessed_no_judge",
                    "reason": "This quality metric requires the configured LLM judge.",
                }
                for name in enabled_names
            },
        }

    from deepeval.test_case import LLMTestCase, SingleTurnParams

    max_report_chars = int(quality_cfg.get("max_report_chars", 30000))
    max_evidence_chars = int(quality_cfg.get("max_evidence_chars", 18000))
    actual = report_to_text(report)[:max_report_chars]
    task = _worker_quality_task(gt, report)
    expected = _worker_quality_expected(gt)
    has_expected = bool(expected_findings(gt))
    criteria_map = _quality_criteria(report.get("worker"), has_expected)
    evidence_text = (
        "\n\n".join(str(item) for item in evidence if item)[:max_evidence_chars]
        if evidence
        else "No trace evidence was extracted. Do not treat absence of trace evidence as proof that a claim is false."
    )

    params = {
        "relevancy": [
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
        ],
        "correctness": [
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
            SingleTurnParams.RETRIEVAL_CONTEXT,
        ],
        "faithfulness": [
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.RETRIEVAL_CONTEXT,
        ],
        "completeness": [
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
        ],
    }

    if not has_expected:   # no benchmark: judge against the goal and trace evidence only
        params["correctness"] = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT]
        params["completeness"] = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]

    thresholds = quality_cfg.get("thresholds", {}) or {}
    pass_threshold = float(thresholds.get("pass", 0.70))
    partial_threshold = float(thresholds.get("partial", 0.40))
    if not 0.0 <= partial_threshold <= pass_threshold <= 1.0:
        raise ValueError(
            "worker_quality.thresholds must satisfy "
            "0 <= partial <= pass <= 1."
        )

    results: dict[str, Any] = {}
    for name in enabled_names:
        # Faithfulness requires evidence. A missing trace context is not a failed claim.
        if name == "faithfulness" and not evidence:
            results[name] = {
                "score": None,
                "status": "not_evaluable_no_trace_evidence",
                "reason": (
                    "No trace evidence was extracted. This is not proof that "
                    "the report's claims are false."
                ),
                "method": "evidence-gated",
            }
            continue

        try:
            test_case = LLMTestCase(
                input=task,
                actual_output=actual,
                expected_output=expected,
                retrieval_context=[evidence_text],
            )
            metric = _make_geval(
                name=f"{report.get('worker', 'worker')}_{name}",
                criteria=criteria_map[name],
                config=cfg,
                params=params[name],
            )
            metric.measure(test_case)
            score, reason = _metric_score_reason(metric)

            if score is None:
                status = "error"
            elif score >= pass_threshold:
                status = "pass"
            elif score >= partial_threshold:
                status = "partial"
            else:
                status = "fail"

            results[name] = {
                "score": round(score, 4) if score is not None else None,
                "status": status,
                "reason": reason,
                "method": "DeepEval G-Eval",
            }
        except Exception as exc:
            results[name] = {
                "score": None,
                "status": "error",
                "reason": f"{type(exc).__name__}: {exc}",
                "method": "DeepEval G-Eval",
            }

    score_values = [
        float(item["score"])
        for item in results.values()
        if isinstance(item.get("score"), (int, float))
    ]
    overall_score = (
        sum(score_values) / len(score_values) if score_values else None
    )

    if overall_score is None:
        overall_status = "not_evaluable"
    elif overall_score >= pass_threshold:
        overall_status = "pass"
    elif overall_score >= partial_threshold:
        overall_status = "partial"
    else:
        overall_status = "fail"

    return {
        "enabled": True,
        "metric_count": len(enabled_names),
        "scoreable_metric_count": len(score_values),
        "overall_score": round(overall_score, 4) if overall_score is not None else None,
        "overall_status": overall_status,
        "aggregation": (
            "Unweighted mean of scoreable metric scores; not a probability "
            "of correctness. Faithfulness is marked not evaluable when trace "
            "evidence is absent."
        ),
        "metrics": results,
        "limitations": [
            "G-Eval scores are LLM-judge estimates, not objective probabilities.",
            "Correctness is bounded by the supplied benchmark and extracted trace evidence.",
            "Faithfulness depends on the completeness and quality of extracted trace evidence.",
            "Missing trace evidence is not proof that a claim is false.",
            "An overall mean can hide a weak individual dimension; inspect each reason.",
        ],
    }


def evaluate_agent_improvements(
    report: dict[str, Any],
    gt: dict[str, Any],
    evidence: list[str],
    finding_results: list[dict[str, Any]],
    worker_quality: dict[str, Any],
    use_judge: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the configured judge to identify observed agent limitations and improvements."""
    cfg = load_config(gt, config)
    review_cfg = cfg.get("agent_improvement_review", {}) or {}
    if not review_cfg.get("enabled", True):
        return {
            "enabled": False,
            "status": "not_run",
            "limitations": [],
            "recommended_enhancements": [],
        }
    if not use_judge:
        return {
            "enabled": True,
            "status": "not_assessed_no_judge",
            "limitations": [],
            "recommended_enhancements": [],
        }

    max_report_chars = int(review_cfg.get("max_report_chars", 30000))
    max_evidence_chars = int(review_cfg.get("max_evidence_chars", 18000))
    max_findings = max(1, int(review_cfg.get("max_findings", 20)))
    observed_findings = [
        {
            "finding_id": item.get("finding_id"),
            "requirement_id": item.get("requirement_id"),
            "final_status": item.get("final_status", item.get("status")),
            "reason": str(item.get("reason") or "")[:1200],
        }
        for item in finding_results[:max_findings]
    ]
    metric_results = {
        name: {
            "score": item.get("score"),
            "status": item.get("status"),
            "reason": str(item.get("reason") or "")[:1600],
        }
        for name, item in (worker_quality.get("metrics", {}) or {}).items()
    }
    context = {
        "research_task": _worker_quality_task(gt, report),
        "benchmark_findings": _worker_quality_expected(gt)[:max_report_chars],
        "report": report_to_text(report)[:max_report_chars],
        "finding_evaluation": observed_findings,
        "overall_quality_evaluation": {
            "score": worker_quality.get("overall_score"),
            "status": worker_quality.get("overall_status"),
            "metrics": metric_results,
        },
        "trace_evidence": "\n\n".join(str(item) for item in evidence if item)[
            :max_evidence_chars
        ],
    }
    prompt = (
        "Review this agent's observed result and evaluation evidence to identify "
        "limitations in the agent and concrete improvements to make to the agent. "
        "Use only the supplied context. Distinguish an agent capability limitation "
        "from missing input data, benchmark-specific requirements, and limitations "
        "of the evaluator. Do not invent causes or claim that a code component is "
        "broken without evidence. Ground each limitation in a specific observation. "
        "Recommend actionable changes to the agent's planning, data collection, "
        "analysis, evidence handling, or reporting that address those limitations. "
        "Return only valid JSON with this shape: "
        '{"limitations":[{"limitation":"...","evidence":"...","impact":"..."}],'
        '"recommended_enhancements":[{"enhancement":"...","addresses":"...",'
        '"priority":"high|medium|low"}]}. '
        "Use empty arrays if the context does not support a conclusion. "
        "Limit each array to 8 items.\n\n"
        f"Evaluation context:\n{json.dumps(context, ensure_ascii=False, default=str)}"
    )

    try:
        generated = _make_openai_judge(cfg).generate(prompt)
        if isinstance(generated, tuple):
            generated = generated[0]
        if isinstance(generated, str):
            response_text = generated
        elif hasattr(generated, "model_dump_json"):
            response_text = generated.model_dump_json()
        else:
            response_text = str(generated)
        json_start = response_text.find("{")
        json_end = response_text.rfind("}")
        if json_start < 0 or json_end < json_start:
            raise ValueError("Judge response did not contain a JSON object.")
        parsed = json.loads(response_text[json_start : json_end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("Judge response JSON must be an object.")

        def normalize_items(key: str, required_fields: tuple[str, ...]) -> list[dict[str, str]]:
            items = parsed.get(key, [])
            if not isinstance(items, list):
                raise ValueError(f"Judge response field {key!r} must be a list.")
            normalized = []
            for item in items[:8]:
                if not isinstance(item, dict):
                    continue
                normalized_item = {
                    field: str(item.get(field) or "").strip()
                    for field in required_fields
                }
                if all(normalized_item.values()):
                    normalized.append(normalized_item)
            return normalized

        limitations = normalize_items(
            "limitations", ("limitation", "evidence", "impact")
        )
        enhancements = normalize_items(
            "recommended_enhancements", ("enhancement", "addresses", "priority")
        )
        return {
            "enabled": True,
            "status": "generated",
            "limitations": limitations,
            "recommended_enhancements": enhancements,
            "method": "OpenAI LLM judge",
        }
    except Exception as exc:
        return {
            "enabled": True,
            "status": "error",
            "limitations": [],
            "recommended_enhancements": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def evaluate(report: dict[str, Any], gt: dict[str, Any], evidence: list[str], use_judge: bool = True,
             second_pass: bool = True, second_pass_top_k: int = 5, config: dict[str, Any] | None = None,
             evidence_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not use_judge:
        raise ValueError("LLM judging is mandatory; deterministic-only evaluation is not supported.")
    if not second_pass:
        raise ValueError("Second-pass review is mandatory for unresolved findings.")
    cfg = load_config(gt, config)
    if not cfg["retrieval"].get("enabled", True):
        raise ValueError("Second-pass review is mandatory; retrieval.enabled cannot be false.")
    report_text = report_to_text(report); findings = expected_findings(gt)
    if not findings:
        raise ValueError("A non-empty golden benchmark is required for mandatory judging and second-pass review.")
    initial_results = []
    for finding in findings:
        if use_judge: initial_results.append(judge_one_finding(report_text, finding, evidence, cfg))
        else:
            score = _simple_overlap(report_text, finding); status = _judge_status(score, cfg)
            initial_results.append({**_finding_result_base(finding), "score": round(score, 4), "status": status,
                                    "reason": "Local token overlap; rough diagnostic, not semantic or factual evaluation.", "method": "local token overlap (no API)", "criteria_results": []})
    retrieval_cfg = cfg["retrieval"]
    needs_second_pass = any(r.get("status") in ("missing", "partial") for r in initial_results)
    summary: dict[str, Any] = {"enabled": True, "required": True,
        "status": "pending" if needs_second_pass else "not_needed",
        "top_k": max(1, int(second_pass_top_k or retrieval_cfg.get("top_k", 5))),
        "retrieval_method": "not_run", "passage_count": 0, "initial_missing_count": sum(r.get("status") == "missing" for r in initial_results),
        "initial_partial_count": sum(r.get("status") == "partial" for r in initial_results), "findings_reviewed": 0,
        "findings_skipped_partial": 0, "findings_upgraded": 0, "embedding_error": None}
    final_results = [dict(r) for r in initial_results]; second_results = []
    if needs_second_pass:
        passages = extract_report_passages(report, int(retrieval_cfg.get("max_passages", 500)), int(retrieval_cfg.get("max_passage_chars", 1400)))
        summary["passage_count"] = len(passages); vectors = None
        summary["status"] = "completed"
        if passages:
            try:
                vectors = _embedding_vectors([p["text"] for p in passages], config=cfg)
                summary["retrieval_method"] = "sentence_transformers_cosine"
            except Exception as exc:
                summary["retrieval_method"] = "lexical_fallback_after_embedding_error"
                summary["embedding_error"] = f"{type(exc).__name__}: {exc}"
        # Reassess both missing and partial findings to reduce first-pass false negatives.
        for idx, (finding, initial) in enumerate(zip(findings, initial_results)):
            if initial.get("status") not in ("missing", "partial"): continue
            try:
                candidates, method = retrieve_candidates(finding, passages, vectors, top_k=summary["top_k"], config=cfg)
            except Exception as exc:
                summary["retrieval_method"] = "lexical_fallback_after_embedding_error"
                summary["embedding_error"] = f"{type(exc).__name__}: {exc}"
                candidates, method = retrieve_candidates(finding, passages, top_k=summary["top_k"], config=cfg)
            second = _second_pass_judge(finding, candidates, initial, cfg); second["retrieval_method"] = method; second["retrieved_candidates"] = candidates
            final_results[idx] = _reconcile_finding(initial, second); second_results.append(second); summary["findings_reviewed"] += 1
            if final_results[idx].get("second_pass_applied"): summary["findings_upgraded"] += 1
        if any(result.get("status") in ("error", "unresolved_no_candidates") for result in second_results):
            summary["status"] = "incomplete"
    for item in final_results:
        item.setdefault("initial_score", item.get("score")); item.setdefault("initial_status", item.get("status"))
        item.setdefault("final_score", item.get("score")); item.setdefault("final_status", item.get("status"))
        item.setdefault("second_pass", None); item.setdefault("reconciliation", "second_pass_not_needed")
    mandatory = [r for r in final_results if not cfg["scoring"].get("mandatory_only", True) or str(r.get("priority", "mandatory")).casefold() == "mandatory"]
    scoreable = [r for r in mandatory if r.get("final_score") is not None and r.get("final_status") in ("covered", "partial", "missing")]
    partial_credit = float(cfg["scoring"].get("partial_credit", .5))
    coverage = sum(1.0 if r["final_status"] == "covered" else partial_credit if r["final_status"] == "partial" else 0.0 for r in scoreable) / len(scoreable) if scoreable else None
    strict = sum(r["final_status"] == "covered" for r in scoreable) / len(scoreable) if scoreable else None
    claims = assess_additional_claims(report, findings, evidence, use_judge, cfg)
    novel = [c for c in claims if c.get("novelty_status") == "additional_candidate"]
    supported = [c for c in novel if c.get("evidence_status") == "supported_in_trace"]
    worker_quality = evaluate_worker_quality(
        report=report,
        gt=gt,
        evidence=evidence,
        use_judge=use_judge,
        config=cfg,
    )
    agent_improvement_review = evaluate_agent_improvements(
        report=report,
        gt=gt,
        evidence=evidence,
        finding_results=final_results,
        worker_quality=worker_quality,
        use_judge=use_judge,
        config=cfg,
    )
    judge_errors = []
    for result in initial_results:
        failed_criteria = [
            item.get("criterion", "unknown criterion")
            for item in result.get("criteria_results", [])
            if item.get("status") == "error"
        ]
        if failed_criteria:
            judge_errors.append(
                f"{result.get('finding_id')}: judge failed for {', '.join(failed_criteria)}"
            )
        elif result.get("status") == "error":
            judge_errors.append(f"{result.get('finding_id')}: judge returned an error")
    for result in second_results:
        if result.get("status") == "error":
            judge_errors.append(
                f"{result.get('finding_id')}: second-pass judge failed"
            )
    return {"worker": report.get("worker"), "task_goal": report.get("task_goal"),
        "deterministic_checks": grounding_checks(report, evidence_items or []),
        "ground_truth_snapshot_date": gt.get("snapshot_date") or gt.get("metadata", {}).get("snapshot_date"),
        "ground_truth_dataset_version": gt.get("dataset_version") or gt.get("metadata", {}).get("dataset_version"),
        "finding_count": len(findings), "mandatory_finding_count": len(mandatory), "coverage_score": round(coverage, 4) if coverage is not None else None,
        "strict_coverage_score": round(strict, 4) if strict is not None else None,
        "coverage_definition": f"Final reconciled mandatory findings: covered=1, partial={partial_credit}, missing=0; errors excluded. Strict coverage counts covered findings only.",
        "evaluation_config": {"thresholds": cfg["thresholds"], "embeddings": cfg["embeddings"], "scoring": cfg["scoring"], "worker_quality": cfg["worker_quality"]},
        "judge_status": "error" if judge_errors else "completed",
        "judge_errors": judge_errors,
        "second_pass_summary": {**summary, "scope": "Missing and partial findings are eligible for second-pass retrieval and reassessment.",
            "warning": "Retrieval similarity is not evidence of coverage or truth; review criterion-level judgments and passages."},
        "additional_claim_summary": {"extracted_claim_count": len(claims), "additional_candidates": len(novel), "additional_candidates_supported_by_trace": len(supported),
            "warning": "Novelty and support are separate LLM-assisted review signals, not proof of truth."},
        "scope_compliance": scope_checks(report, gt, cfg), "finding_results": final_results, "second_pass_results": second_results,
        "additional_claim_results": claims,
        "worker_result_quality": worker_quality,
        "agent_improvement_review": agent_improvement_review,
        "limitations": agent_improvement_review["limitations"],
        "recommended_enhancements": agent_improvement_review["recommended_enhancements"],
        "evaluation_limitations": ["Coverage measures report completeness against the supplied benchmark, not factual truth.",
            "Acceptance criteria are evaluated individually in judge mode; scores remain model estimates.",
            "Embeddings retrieve candidate passages only. On embedding failure, lexical fallback is recorded.",
            "Evidence support is limited to evidence extracted from this trace; missing context means unverified, not false.",
            "Scope checks are deterministic string-based checks and can miss semantic references.",
            "LLM judge scores are model estimates, not ground-truth labels."]}


def _mandatory_evaluation_failures(result: dict[str, Any]) -> list[str]:
    failures = []
    if result.get("judge_status") != "completed":
        failures.append("One or more mandatory LLM judge calls failed.")
    second_pass = result.get("second_pass_summary", {})
    if second_pass.get("status") == "incomplete":
        failures.append("Mandatory second-pass review could not complete for every unresolved finding.")
    return failures


def load_node(state: EvalState) -> dict[str, Any]:
    try:
        config = read_json(state["config_path"]) if state.get("config_path") else {}
        trace: dict[str, Any] = {}
        items: list[dict[str, Any]] = []
        if state.get("trace_path"):
            trace = read_json(state["trace_path"])
            report, items = extract_report_and_evidence(trace, state.get("worker"))
        else:
            report = read_json(state["report_path"])
        gt = read_json(state["ground_truth_path"]) if state.get("ground_truth_path") else {}
        worker = report.get("worker") or state.get("worker")
        worker_datasets = gt.get("worker_datasets")
        if isinstance(worker_datasets, dict):
            if not worker or not isinstance(worker_datasets.get(worker), dict):
                raise ValueError(f"Combined golden dataset has no worker benchmark for {worker!r}.")
            gt = worker_datasets[worker]
        config = load_config(gt, config)
        evidence = format_evidence(items, config) if items else (_generic_extract_evidence(trace, config) if trace else [])
        return {"trace": trace, "report": report, "ground_truth": gt, "config": config,
                "evidence": evidence, "evidence_items": items, "errors": []}
    except Exception as exc:
        return {"errors": [f"Input loading failed: {type(exc).__name__}: {exc}"]}


def evaluate_node(state: EvalState) -> dict[str, Any]:
    if state.get("errors"):
        return {}
    try:
        result = evaluate(state["report"], state["ground_truth"], state.get("evidence", []),
                          use_judge=state.get("use_judge", True), second_pass=state.get("second_pass", True),
                          second_pass_top_k=state.get("second_pass_top_k", 5), config=state.get("config"),
                          evidence_items=state.get("evidence_items"))
        return {"result": result}
    except Exception as exc:
        return {"errors": [f"Evaluation failed: {type(exc).__name__}: {exc}"]}


def build_graph():
    from langgraph.graph import END, START, StateGraph
    graph = StateGraph(EvalState)
    graph.add_node("load", load_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "evaluate")
    graph.add_edge("evaluate", END)
    return graph.compile()


def run_evaluation(state: EvalState) -> EvalState:
    """Run through LangGraph when installed, otherwise the same two nodes sequentially."""
    try:
        return build_graph().invoke(state)
    except ImportError:
        merged = {**state, **load_node(state)}
        return {**merged, **evaluate_node(merged)}


RUN_WORKER_NAMES = (
    "competitor_intelligence",
    "customer_trends",
    "internal_intelligence",
    "market_intelligence",
)
RUN_STAGE_NAMES = ("verification", "cross_signal", "decision_analysis")
RUN_PIPELINE_STEPS = RUN_WORKER_NAMES + RUN_STAGE_NAMES

def _artifact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _artifact_evidence(output: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for evidence in _as_list(output.get("evidence")):
        if not isinstance(evidence, dict):
            continue
        text = next(
            (
                _artifact_text(evidence.get(key))
                for key in ("excerpt", "extracted_fact", "supporting_text", "page_text", "text")
                if _artifact_text(evidence.get(key))
            ),
            "",
        )
        if not text:
            continue
        source = " | ".join(
            str(evidence.get(key))
            for key in ("source_title", "publisher", "source_url")
            if evidence.get(key)
        )
        items.append({
            "id": str(evidence.get("evidence_id") or evidence.get("id") or ""),
            "source": source,
            "text": text,
        })
    return items


def _artifact_worker_report(
    worker: str,
    task_input: dict[str, Any],
    task_output: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_findings = _as_list(task_output.get("findings"))
    findings = []
    for finding in raw_findings:
        if not isinstance(finding, dict):
            continue
        claim = next(
            (
                _artifact_text(finding.get(key))
                for key in ("statement", "claim", "finding")
                if _artifact_text(finding.get(key))
            ),
            "",
        )
        if not claim:
            continue
        refs = _as_list(finding.get("evidence_refs") or finding.get("evidence_ids"))
        evidence_ids = [
            str(ref.get("evidence_id") or ref.get("id") or ref.get("source_url") or "")
            if isinstance(ref, dict) else str(ref)
            for ref in refs
        ]
        normalized = {
            **finding,
            "id": finding.get("finding_id") or finding.get("id"),
            "claim": claim,
            "evidence_ids": [value for value in evidence_ids if value],
        }
        findings.append(normalized)

    metadata = task_output.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    metadata_keys = (
        "summary", "control_summary", "finding_metrics", "requirement_coverage",
        "requirement_checks", "competitor_profiles", "themes", "trend_series",
    )
    report_metadata = {key: metadata[key] for key in metadata_keys if key in metadata}
    report = {
        "worker": worker,
        "task_goal": _artifact_text(task_input.get("goal") or task_input.get("focus")),
        "summary": _artifact_text(metadata.get("summary") or task_output.get("summary")),
        "status": task_output.get("status") or "unknown",
        "confidence": task_output.get("confidence"),
        "findings": findings,
        "gaps": task_output.get("gaps") or [],
        "conflicts": task_output.get("conflicts") or [],
        "requirements": task_input.get("requirements") or [],
        "business_context": task_input.get("business_context") or {},
        "metadata": report_metadata,
        "error": task_output.get("error"),
    }
    if not report["summary"]:
        report["summary"] = f"{len(findings)} findings; {len(report['gaps'])} gaps."
    return report, _artifact_evidence(task_output)


def _stage_status_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        status = str(finding.get("verification_status") or "unknown").casefold()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _verification_artifact_checks(
    stage_input: dict[str, Any], stage_output: dict[str, Any]
) -> dict[str, Any]:
    agent_outputs = _as_list(stage_input.get("agent_outputs"))
    input_finding_ids: set[str] = set()
    input_evidence_ids: set[str] = set()
    for worker_output in agent_outputs:
        if not isinstance(worker_output, dict):
            continue
        input_finding_ids.update(
            str(item.get("finding_id"))
            for item in _as_list(worker_output.get("findings"))
            if isinstance(item, dict) and item.get("finding_id")
        )
        input_evidence_ids.update(
            str(item.get("evidence_id") or item.get("id"))
            for item in _as_list(worker_output.get("evidence"))
            if isinstance(item, dict) and (item.get("evidence_id") or item.get("id"))
        )

    result = stage_output.get("verification_result")
    result = result if isinstance(result, dict) else stage_output
    verdicts = [
        item for item in _as_list(result.get("findings"))
        if isinstance(item, dict) and item.get("finding_id")
    ]
    verdict_ids = [str(item["finding_id"]) for item in verdicts]
    verdict_id_set = set(verdict_ids)
    missing_ids = sorted(input_finding_ids - verdict_id_set)
    unknown_ids = sorted(verdict_id_set - input_finding_ids) if input_finding_ids else []
    evidence_refs = [
        str(ref)
        for item in verdicts
        for ref in item.get("evidence_ids") or []
        if ref
    ]
    unresolved_evidence = sorted(set(evidence_refs) - input_evidence_ids)
    return {
        "input_finding_count": len(input_finding_ids),
        "assessed_finding_count": len(verdict_id_set),
        "assessment_coverage": (
            round(len(input_finding_ids & verdict_id_set) / len(input_finding_ids), 4)
            if input_finding_ids else None
        ),
        "status_counts": _stage_status_counts(verdicts),
        "duplicate_finding_ids": sorted(
            finding_id for finding_id in verdict_id_set if verdict_ids.count(finding_id) > 1
        ),
        "missing_finding_ids": missing_ids,
        "unknown_finding_ids": unknown_ids,
        "evidence_reference_count": len(evidence_refs),
        "unresolved_evidence_ids": unresolved_evidence,
    }


def _supporting_finding_ids(signal: dict[str, Any]) -> list[str]:
    supports = _as_list(signal.get("supporting_findings"))
    result = []
    for item in supports:
        value = item.get("finding_id") if isinstance(item, dict) else item
        if value:
            result.append(str(value))
    return result


def _cross_signal_artifact_checks(
    stage_input: dict[str, Any], stage_output: dict[str, Any]
) -> dict[str, Any]:
    verified_ids = {
        str(item.get("finding_id"))
        for item in _as_list(stage_input.get("verified_findings"))
        if isinstance(item, dict)
        and item.get("finding_id")
        and str(item.get("verification_status", "")).casefold() == "verified"
    }
    result = stage_output.get("cross_signal")
    result = result if isinstance(result, dict) else stage_output
    signals = [item for item in _as_list(result.get("signals")) if isinstance(item, dict)]
    invalid_refs: dict[str, list[str]] = {}
    weak_support: list[str] = []
    titles: list[str] = []
    for index, signal in enumerate(signals, start=1):
        signal_id = str(signal.get("signal_id") or f"signal-{index}")
        references = _supporting_finding_ids(signal)
        distinct = set(references)
        invalid = sorted(distinct - verified_ids)
        if invalid:
            invalid_refs[signal_id] = invalid
        if len(distinct) < 2:
            weak_support.append(signal_id)
        title = _norm(str(signal.get("title") or signal.get("signal") or ""))
        if title:
            titles.append(title)
    duplicate_titles = sorted(
        title for title in set(titles) if titles.count(title) > 1
    )
    return {
        "verified_input_finding_count": len(verified_ids),
        "signal_count": len(signals),
        "signals_without_two_distinct_supporting_findings": weak_support,
        "signals_with_unverified_or_unknown_references": invalid_refs,
        "duplicate_signal_titles": duplicate_titles,
        "empty_signal_output": not signals,
    }


def _decision_artifact_checks(
    stage_input: dict[str, Any], stage_output: dict[str, Any]
) -> dict[str, Any]:
    cross_signal = stage_input.get("cross_signal")
    cross_signal = cross_signal if isinstance(cross_signal, dict) else {}
    known_signal_ids = {
        str(item.get("signal_id"))
        for item in cross_signal.get("signals") or []
        if isinstance(item, dict) and item.get("signal_id")
    }
    result = stage_output.get("decision_analysis")
    result = result if isinstance(result, dict) else stage_output
    reference_sets: list[tuple[str, list[str]]] = []
    for key in ("opportunities", "risks", "recommended_actions", "options"):
        for index, item in enumerate(_as_list(result.get(key)), start=1):
            if isinstance(item, dict):
                refs = [str(ref) for ref in item.get("supporting_signal_ids") or [] if ref]
                if refs:
                    reference_sets.append((str(item.get("title") or item.get("key") or f"{key}-{index}"), refs))
    for index, item in enumerate(_as_list(result.get("question_recommendations")), start=1):
        if isinstance(item, dict):
            recommendation = item.get("recommendation")
            if isinstance(recommendation, dict):
                refs = [str(ref) for ref in recommendation.get("supporting_signal_ids") or [] if ref]
                if refs:
                    reference_sets.append((str(item.get("question") or f"question-{index}"), refs))

    invalid_refs = {
        name: sorted(set(refs) - known_signal_ids)
        for name, refs in reference_sets
        if set(refs) - known_signal_ids
    }
    options = [
        item for item in _as_list(result.get("options"))
        if isinstance(item, dict)
    ]
    option_keys = [
        _norm(str(item.get("title") or item.get("thesis") or item.get("key") or ""))
        for item in options
    ]
    option_keys = [key for key in option_keys if key]
    return {
        "known_signal_count": len(known_signal_ids),
        "question_recommendation_count": len(_as_list(result.get("question_recommendations"))),
        "option_count": len(options),
        "distinct_option_count": len(set(option_keys)),
        "duplicate_options": sorted(
            key for key in set(option_keys) if option_keys.count(key) > 1
        ),
        "items_with_signal_references": len(reference_sets),
        "items_with_unknown_signal_references": invalid_refs,
        "empty_options": not options,
    }


def _validate_combined_run_ground_truth(ground_truth: dict[str, Any]) -> None:
    worker_datasets = ground_truth.get("worker_datasets")
    if not isinstance(worker_datasets, dict):
        raise ValueError("Run-artifact evaluation requires a combined golden dataset with worker_datasets.")
    required_workers = set(RUN_WORKER_NAMES)
    missing_workers = sorted(required_workers - set(worker_datasets))
    if missing_workers:
        raise ValueError(f"Combined golden dataset is missing worker benchmarks: {missing_workers}")
    required_stages = {
        "verification": ground_truth.get("verification_stage_rubric"),
        "cross_signal": ground_truth.get("cross_signal"),
        "decision_analysis": ground_truth.get("decision_analysis"),
    }
    for stage, dataset in required_stages.items():
        if not isinstance(dataset, dict) or not expected_findings(dataset):
            raise ValueError(f"Combined golden dataset has no expected findings for {stage}.")
    for worker, dataset in worker_datasets.items():
        if not isinstance(dataset, dict) or not expected_findings(dataset):
            raise ValueError(f"Combined golden dataset has no expected findings for {worker}.")


def evaluate_run_artifacts(
    run_dir: str | Path,
    out_dir: str | Path,
    ground_truth: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    resume_from: str | None = None,
) -> dict[str, Any]:
    """Evaluate one run against combined worker and stage rubrics."""
    run_path = Path(run_dir)
    if not run_path.is_dir():
        raise ValueError(f"Run artifact directory does not exist: {run_path}")
    _validate_combined_run_ground_truth(ground_truth)
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_output_dir = output_dir / "workers"
    stage_output_dir = output_dir / "stages"
    worker_output_dir.mkdir(parents=True, exist_ok=True)
    stage_output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config({}, config or {})
    if not cfg["retrieval"].get("enabled", True):
        raise ValueError("Second-pass review is mandatory; retrieval.enabled cannot be false.")
    worker_datasets = ground_truth["worker_datasets"]
    workers: list[dict[str, Any]] = []
    stages: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    resume_index = None
    if resume_from is not None:
        if resume_from not in RUN_PIPELINE_STEPS:
            raise ValueError(
                f"Unsupported resume step {resume_from!r}; choose from {RUN_PIPELINE_STEPS}."
            )
        resume_index = RUN_PIPELINE_STEPS.index(resume_from)
        previous_summary_path = output_dir / "run_evaluation_report.json"
        if not previous_summary_path.is_file():
            raise ValueError(
                f"Cannot resume without the previous summary: {previous_summary_path}"
            )
        previous_summary = read_json(str(previous_summary_path))
        if not isinstance(previous_summary, dict):
            raise ValueError("Previous run evaluation summary must be a JSON object.")
        expected_ground_truth = (
            ground_truth.get("dataset_name") or ground_truth.get("dataset_version")
        )
        if (
            previous_summary.get("run_id") != run_path.name
            or previous_summary.get("ground_truth") != expected_ground_truth
        ):
            raise ValueError(
                "Previous evaluation summary does not match this run and ground-truth dataset."
            )

        previous_workers = previous_summary.get("workers", [])
        previous_stages = previous_summary.get("stages", {})
        if not isinstance(previous_workers, list) or not isinstance(previous_stages, dict):
            raise ValueError("Previous evaluation summary has an invalid workers or stages section.")

        for worker in RUN_WORKER_NAMES:
            if RUN_PIPELINE_STEPS.index(worker) >= resume_index:
                continue
            worker_results = [
                item for item in previous_workers
                if isinstance(item, dict) and item.get("worker") == worker
            ]
            expected_outputs = {
                (task_dir / "output.json").resolve()
                for task_dir in (run_path / worker).iterdir()
                if task_dir.is_dir()
                and (task_dir / "input.json").is_file()
                and (task_dir / "output.json").is_file()
            } if (run_path / worker).is_dir() else set()
            previous_outputs = {
                Path(item["source_output"]).resolve()
                for item in worker_results
                if isinstance(item.get("source_output"), str)
            }
            if not expected_outputs or previous_outputs != expected_outputs:
                raise ValueError(
                    f"Cannot resume at {resume_from}: prior results for {worker} are missing or do not cover the current artifacts."
                )
            for item in worker_results:
                evaluation = item.get("evaluation", {})
                second_pass = evaluation.get("second_pass_summary", {}) if isinstance(evaluation, dict) else {}
                if (
                    item.get("mandatory_evaluation_failures")
                    or not isinstance(evaluation, dict)
                    or evaluation.get("judge_status") != "completed"
                    or not second_pass.get("enabled")
                    or second_pass.get("status") not in ("completed", "not_needed")
                ):
                    raise ValueError(
                        f"Cannot resume at {resume_from}: prior evaluation for {worker} is not complete."
                    )
            workers.extend(worker_results)

        for stage in RUN_STAGE_NAMES:
            if RUN_PIPELINE_STEPS.index(stage) >= resume_index:
                continue
            previous_stage = previous_stages.get(stage)
            evaluation = (
                previous_stage.get("benchmark_evaluation")
                if isinstance(previous_stage, dict)
                else None
            )
            second_pass = (
                evaluation.get("second_pass_summary", {})
                if isinstance(evaluation, dict)
                else {}
            )
            if (
                not isinstance(evaluation, dict)
                or previous_stage.get("quality_judgment", {}).get("status") == "error"
                or _mandatory_evaluation_failures(evaluation)
                or not second_pass.get("enabled")
                or second_pass.get("status") not in ("completed", "not_needed")
            ):
                raise ValueError(
                    f"Cannot resume at {resume_from}: prior evaluation for stage {stage} is not complete."
                )
            stages[stage] = previous_stage

    for worker_dir in sorted(path for path in run_path.iterdir() if path.is_dir() and path.name != "stages"):
        if (
            resume_index is not None
            and worker_dir.name in RUN_PIPELINE_STEPS
            and RUN_PIPELINE_STEPS.index(worker_dir.name) < resume_index
        ):
            continue
        for task_dir in sorted(path for path in worker_dir.iterdir() if path.is_dir()):
            input_path = task_dir / "input.json"
            output_path = task_dir / "output.json"
            if not input_path.is_file() or not output_path.is_file():
                errors.append(f"{worker_dir.name}/{task_dir.name}: missing input.json or output.json")
                continue
            try:
                task_input = read_json(str(input_path))
                task_output = read_json(str(output_path))
                report, evidence_items = _artifact_worker_report(worker_dir.name, task_input, task_output)
                evidence = format_evidence(evidence_items, cfg)
                worker_ground_truth = worker_datasets.get(worker_dir.name)
                if not isinstance(worker_ground_truth, dict):
                    raise ValueError(
                        f"Combined golden dataset has no benchmark for worker {worker_dir.name!r}."
                    )
                result = evaluate(
                    report,
                    worker_ground_truth,
                    evidence,
                    use_judge=True,
                    second_pass=True,
                    config=cfg,
                    evidence_items=evidence_items,
                )
                worker_result = {
                    "worker": worker_dir.name,
                    "task_id": task_output.get("task_id") or task_input.get("task_id") or task_dir.name,
                    "attempt": task_output.get("attempt") or task_input.get("attempt"),
                    "source_input": str(input_path),
                    "source_output": str(output_path),
                    "status": task_output.get("status") or "unknown",
                    "finding_count": len(report["findings"]),
                    "evidence_count": len(evidence_items),
                    "gap_count": len(report["gaps"]),
                    "error": task_output.get("error"),
                    "evaluation": result,
                    "mandatory_evaluation_failures": _mandatory_evaluation_failures(result),
                }
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{worker_dir.name}_{task_dir.name}")
                worker_file = worker_output_dir / f"{safe_name}_evaluation.json"
                worker_result["evaluation_file"] = str(worker_file)
                worker_file.write_text(
                    json.dumps(worker_result, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                workers.append(worker_result)
                errors.extend(
                    f"{worker_dir.name}/{task_dir.name}: {failure}"
                    for failure in worker_result["mandatory_evaluation_failures"]
                )
            except Exception as exc:  # noqa: BLE001 - capture this task failure and continue with other artifacts
                error = f"{worker_dir.name}/{task_dir.name}: {type(exc).__name__}: {exc}"
                errors.append(error)

    if not workers:
        errors.append("No worker task input.json/output.json pairs were found in the run folder.")

    stage_checkers = {
        "verification": _verification_artifact_checks,
        "cross_signal": _cross_signal_artifact_checks,
        "decision_analysis": _decision_artifact_checks,
    }
    stage_ground_truth = {
        "verification": ground_truth["verification_stage_rubric"],
        "cross_signal": ground_truth["cross_signal"],
        "decision_analysis": ground_truth["decision_analysis"],
    }
    for stage in RUN_STAGE_NAMES:
        if (
            resume_index is not None
            and RUN_PIPELINE_STEPS.index(stage) < resume_index
        ):
            continue
        stage_dir = run_path / "stages" / stage
        input_path = stage_dir / "input.json"
        output_path = stage_dir / "output.json"
        if not input_path.is_file() or not output_path.is_file():
            stages[stage] = {
                "status": "missing",
                "input": str(input_path) if input_path.is_file() else None,
                "output": str(output_path) if output_path.is_file() else None,
            }
            errors.append(f"stages/{stage}: missing input.json or output.json")
            continue
        try:
            stage_input = read_json(str(input_path))
            stage_output = read_json(str(output_path))
            request = stage_input.get("request") or stage_input.get("business_request") or {}
            goal = _artifact_text(request.get("goal")) if isinstance(request, dict) else ""
            stage_report = {
                "worker": stage,
                "task_goal": goal or f"Evaluate the {stage} stage.",
                "summary": _artifact_text(stage_output.get("summary") or stage_output.get("status")),
                "stage_output": stage_output,
            }
            stage_evaluation = evaluate(
                stage_report,
                stage_ground_truth[stage],
                [json.dumps(stage_input, ensure_ascii=False, default=str)],
                use_judge=True,
                second_pass=True,
                config=cfg,
            )
            evaluation_failures = _mandatory_evaluation_failures(stage_evaluation)
            coverage_score = stage_evaluation.get("coverage_score")
            thresholds = stage_evaluation.get("evaluation_config", {}).get("thresholds", {})
            if evaluation_failures or coverage_score is None:
                quality_status = "error"
            elif coverage_score >= float(thresholds.get("covered", 0.7)):
                quality_status = "pass"
            elif coverage_score >= float(thresholds.get("partial", 0.4)):
                quality_status = "partial"
            else:
                quality_status = "fail"
            if evaluation_failures:
                quality_reason = "; ".join(evaluation_failures)
            elif coverage_score is None:
                quality_reason = "No scoreable benchmark findings were produced."
            else:
                quality_reason = "Combined-golden criteria evaluated with mandatory LLM judging and second-pass review."
            stage_result = {
                "status": stage_output.get("status") or "unknown",
                "source_input": str(input_path),
                "source_output": str(output_path),
                "deterministic_checks": stage_checkers[stage](stage_input, stage_output),
                "quality_judgment": {
                    "status": quality_status,
                    "score": coverage_score,
                    "reason": quality_reason,
                    "method": "criterion-level DeepEval G-Eval with second-pass retrieval",
                },
                "benchmark_evaluation": stage_evaluation,
            }
            stage_file = stage_output_dir / f"{stage}_evaluation.json"
            stage_result["evaluation_file"] = str(stage_file)
            stage_file.write_text(
                json.dumps(stage_result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            stages[stage] = stage_result
            errors.extend(
                f"stages/{stage}: {failure}" for failure in evaluation_failures
            )
        except Exception as exc:  # noqa: BLE001 - report a stage failure without skipping remaining stages
            error = f"stages/{stage}: {type(exc).__name__}: {exc}"
            errors.append(error)
            stages[stage] = {"status": "error", "error": error}

    summary = {
        "status": "completed" if not errors else "completed_with_errors",
        "run_id": run_path.name,
        "run_dir": str(run_path),
        "judge_enabled": True,
        "evaluation_mode": "combined_golden_with_mandatory_judge_and_second_pass",
        "resumed_from": resume_from,
        "resumed_workers": [
            worker for worker in RUN_WORKER_NAMES
            if resume_index is not None and RUN_PIPELINE_STEPS.index(worker) < resume_index
        ],
        "resumed_stages": [
            stage for stage in RUN_STAGE_NAMES
            if resume_index is not None and RUN_PIPELINE_STEPS.index(stage) < resume_index
        ],
        "ground_truth": ground_truth.get("dataset_name") or ground_truth.get("dataset_version"),
        "worker_task_count": len(workers),
        "workers_evaluated": sorted({item["worker"] for item in workers}),
        "stage_count": sum(stage.get("status") != "missing" for stage in stages.values()),
        "workers": workers,
        "stages": stages,
        "errors": errors,
    }
    summary_path = output_dir / "run_evaluation_report.json"
    summary["output"] = str(summary_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return summary


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Evaluate research-worker traces and verification datasets with mandatory LLM judging."
    )
    p.add_argument("--trace", nargs="+", help="Trace JSON file(s); the worker is auto-detected.")
    p.add_argument(
        "--ground-truth",
        help="Benchmark JSON; the combined golden dataset works with --trace, --all-workers, and --run-dir.",
    )
    p.add_argument("--worker", help="Force a worker name instead of auto-detecting (single --trace only).")
    p.add_argument("--config", help="Optional config JSON overriding DEFAULT_CONFIG.")
    p.add_argument("--out-dir", default="eval_output")
    p.add_argument("--verification-golden", help="Optional verification benchmark JSON to evaluate against a verification trace.")
    p.add_argument("--verification-predictions", help="Optional verification predictions JSON. If omitted, the original statuses in the golden file are used as baseline.")
    p.add_argument("--verification-output", default="verification_evaluation_report.json", help="Where to write the verification evaluation report.")
    p.add_argument("--all-workers", action="store_true", help="Auto-discover worker traces under the repository and evaluate each against the matching golden benchmark.")
    p.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]), help="Repository root used when auto-discovering worker traces. Defaults to the project repo root.")
    p.add_argument("--run-dir", help="Evaluate worker and stage artifacts against a combined golden dataset.")
    p.add_argument(
        "--resume-from",
        choices=RUN_PIPELINE_STEPS,
        help="Resume a prior --run-dir evaluation from this worker or stage, reusing only completed earlier results.",
    )
    args = p.parse_args(argv)

    if args.resume_from and not args.run_dir:
        p.error("--resume-from requires --run-dir.")
    if args.run_dir:
        if args.trace or args.all_workers or args.verification_golden or args.verification_predictions:
            p.error("--run-dir cannot be combined with --trace, --all-workers, or verification dataset options")
        if not args.ground_truth:
            p.error("--run-dir requires --ground-truth with the combined golden dataset.")
        config = read_json(args.config) if args.config else {}
        ground_truth = read_json(args.ground_truth)
        summary = evaluate_run_artifacts(
            args.run_dir,
            args.out_dir,
            ground_truth,
            config=config,
            resume_from=args.resume_from,
        )
        print(json.dumps({
            "status": summary["status"],
            "run_id": summary["run_id"],
            "resumed_from": summary["resumed_from"],
            "worker_task_count": summary["worker_task_count"],
            "workers_evaluated": summary["workers_evaluated"],
            "stage_count": summary["stage_count"],
            "stages": {
                name: stage.get("status")
                for name, stage in summary["stages"].items()
            },
            "errors": summary["errors"],
            "output": summary["output"],
        }, ensure_ascii=False, indent=2, default=str))
        return 0 if not summary["errors"] else 1

    if args.verification_golden and not args.all_workers:
        return verification_eval_main([
            "--golden",
            args.verification_golden,
            "--predictions",
            args.verification_predictions,
            "--output",
            args.verification_output,
        ])

    if args.all_workers:
        summary = run_all_worker_evaluations(
            root=args.repo_root,
            out_dir=args.out_dir,
            use_judge=True,
            second_pass=True,
            config=args.config,
            ground_truth_path=args.ground_truth,
            verification_golden=args.verification_golden,
            verification_predictions=args.verification_predictions,
        )
        print(json.dumps({
            "repo_root": summary["repo_root"],
            "total_workers": summary["total_workers"],
            "total_ok": summary["total_ok"],
            "workers": summary["workers"],
            "verification": summary["verification"],
            "errors": summary["errors"],
        }, ensure_ascii=False, indent=2, default=str))
        return 0 if not summary["errors"] else 1

    if not args.trace:
        p.error("either --trace, --all-workers, or --verification-golden is required")
    if not args.ground_truth:
        p.error("--trace requires --ground-truth for mandatory benchmark and second-pass evaluation.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for trace_path in args.trace:
        state: EvalState = {"trace_path": trace_path, "use_judge": True, "second_pass": True}
        if args.ground_truth:
            state["ground_truth_path"] = args.ground_truth
        if args.config:
            state["config_path"] = args.config
        if args.worker and len(args.trace) == 1:
            state["worker"] = args.worker
        final = run_evaluation(state)
        if final.get("errors"):
            print(f"[{trace_path}] " + "; ".join(final["errors"]))
            status = 1
            continue
        result = final["result"]
        stem = result.get("worker") or Path(trace_path).stem
        output_path = out_dir / f"{stem}_evaluation.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"[{stem}] wrote {output_path}")
        if _mandatory_evaluation_failures(result):
            status = 1
    return status


# ---------------------------------------------------------------------------
# Verification evaluation utilities copied from
# Evaluation/verification_evaluation/run_verification_eval.py.
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_assessments(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("assessments"), list):
        return obj["assessments"]
    if isinstance(obj, dict) and isinstance(obj.get("spans"), list):
        for span in obj["spans"]:
            if span.get("name") == "assess_findings":
                output = span.get("output", {})
                if isinstance(output, dict):
                    return output.get("assessments", [])
    if isinstance(obj, dict) and isinstance(obj.get("result"), dict):
        findings = obj["result"].get("findings")
        if isinstance(findings, list):
            return findings
    if isinstance(obj, dict):
        per_case = obj.get("deterministic_metrics", {}).get("per_case")
        if isinstance(per_case, list):
            return [
                {
                    "finding_id": item["finding_id"],
                    "verification_status": item["predicted_status"],
                }
                for item in per_case
                if isinstance(item, dict) and item.get("finding_id") and item.get("predicted_status")
            ]
    raise ValueError("Could not find an assessments array in predictions JSON.")


def normalize_status(value: Any) -> str:
    value = str(value or "").strip().lower()
    aliases = {"verified": "verified", "insufficient": "insufficient"}
    if value not in aliases:
        raise ValueError(f"Unsupported verification status: {value!r}. Expected verified or insufficient.")
    return aliases[value]


def make_context(case: dict[str, Any]) -> list[str]:
    context: list[str] = []
    for ev in case.get("input", {}).get("evidence", []):
        text = ev.get("excerpt") or ev.get("extracted_fact") or ""
        if not text:
            continue
        label = (ev.get("source_title") or ev.get("publisher") or ev.get("source_url") or ev.get("evidence_id") or "evidence")
        context.append(f"Evidence {ev.get('evidence_id', 'unknown')} ({label}): {text}")
    return context


try:
    from deepeval.models import DeepEvalBaseLLM
except ImportError:  # pragma: no cover
    DeepEvalBaseLLM = None  # type: ignore[assignment]


if DeepEvalBaseLLM is not None:
    class OpenAIDeepEvalModel(DeepEvalBaseLLM):
        def __init__(self, *, model_name: str, base_url: str, api_key: str, timeout: float):
            if requests is None:
                raise RuntimeError("requests is not installed. Install it with: pip install requests.")
            self.model_name = model_name
            self.base_url = base_url.rstrip("/")
            self.api_key = api_key
            self.timeout = timeout
            self.endpoint = f"{self.base_url}/chat/completions"
            self.session = requests.Session()
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })

        def load_model(self):
            return self

        def get_model_name(self):
            return f"OpenAI / {self.model_name}"

        def generate(self, prompt: str, schema=None):
            system_content = (
                "You are a careful and objective LLM evaluation judge. Evaluate only using the information provided. "
                "Do not invent evidence or assume unsupported facts. Follow the requested output schema exactly."
            )
            if schema is not None:
                json_schema = (
                    schema.model_json_schema()
                    if hasattr(schema, "model_json_schema")
                    else schema.schema()
                )
                system_content += f"\nRespond with JSON matching this schema:\n{json.dumps(json_schema)}"
            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            try:
                response = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
                response.raise_for_status()
                result = response.json()
            except requests.exceptions.Timeout as exc:
                raise RuntimeError("OpenAI request timed out; increase OPENAI_TIMEOUT_S or retry.") from exc
            except requests.exceptions.HTTPError as exc:
                raise RuntimeError(f"OpenAI HTTP error: {exc}. Response: {response.text[:2000]}") from exc
            except requests.exceptions.RequestException as exc:
                raise RuntimeError(f"Could not connect to OpenAI: {exc}") from exc
            choices = result.get("choices")
            content = ""
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                )
            content = str(content).strip()
            if not content:
                raise RuntimeError("OpenAI returned no message content.")
            content = re.sub(r"^\s*```(?:json)?\s*", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\s*```\s*$", "", content)
            if schema is None:
                return content
            try:
                parsed = json.loads(content)
                if hasattr(schema, "model_validate"):
                    return schema.model_validate(parsed)
                return schema.parse_obj(parsed)
            except Exception as exc:
                raise RuntimeError(f"OpenAI returned JSON that did not match the DeepEval schema.\nOutput: {content[:2000]}") from exc

        async def a_generate(self, prompt: str, schema=None):
            return await asyncio.to_thread(self.generate, prompt, schema)
else:
    class OpenAIDeepEvalModel:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("DeepEval is not installed. Install it with: pip install deepeval requests.")


def deterministic_metrics(cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    rows: list[dict[str, Any]] = []
    for case in cases:
        fid = case["finding_id"]
        expected = normalize_status(case["golden_reference"]["verification_status"])
        actual_obj = predictions.get(fid, {})
        predicted = normalize_status(actual_obj.get("verification_status"))
        if expected == "verified" and predicted == "verified":
            tp += 1
        elif expected == "insufficient" and predicted == "verified":
            fp += 1
        elif expected == "verified" and predicted == "insufficient":
            fn += 1
        else:
            tn += 1
        rows.append({
            "test_case_id": case["test_case_id"],
            "finding_id": fid,
            "worker": case["worker"],
            "expected_status": expected,
            "predicted_status": predicted,
            "correct": expected == predicted,
            "review_reason": case["golden_reference"]["review_reason"],
        })
    n = len(rows)
    accuracy = (tp + tn) / n if n else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "count": n,
        "confusion_matrix_positive_class_verified": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
        "accuracy": accuracy,
        "precision_verified": precision,
        "recall_verified": recall,
        "f1_verified": f1,
        "false_verification_rate_among_agent_verified": fp / (tp + fp) if tp + fp else 0.0,
        "false_positive_rate_among_expected_insufficient": fp / (fp + tn) if fp + tn else 0.0,
        "per_case": rows,
    }


def run_deepeval(cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        from deepeval.metrics import GEval, FaithfulnessMetric, ContextualRelevancyMetric
        from deepeval.test_case import LLMTestCase, SingleTurnParams
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DeepEval is not installed. Run: pip install deepeval requests") from exc
    model = _make_openai_judge(DEFAULT_CONFIG)
    test_cases: list[Any] = []
    for case in cases:
        fid = case["finding_id"]
        pred = predictions[fid]
        context = make_context(case)
        expected = case["golden_reference"]
        actual_output = json.dumps(pred, ensure_ascii=False)
        expected_output = json.dumps({
            "verification_status": expected["verification_status"],
            "review_reason": expected["review_reason"],
        }, ensure_ascii=False)
        test_cases.append(LLMTestCase(
            input=case["input"]["finding_statement"],
            actual_output=actual_output,
            expected_output=expected_output,
            retrieval_context=context,
        ))

    decision_metric = GEval(
        name="Verification Decision Correctness",
        criteria=(
            "Judge whether the predicted verification_status is correct for the finding, using the attached evidence and expected "
            "human-reviewed status. A finding is verified only if the evidence supports the complete claim as worded. If any "
            "material part is unsupported, outdated, misattributed, contradicted, or inferred beyond the evidence, the correct "
            "status is insufficient."
        ),
        evaluation_steps=[
            "Read the finding statement and attached evidence excerpts.",
            "Identify every material component of the finding, including company, product, geography, metric, and time period.",
            "Check whether the evidence directly supports all material components.",
            "Compare the predicted verification_status with the expected human-reviewed status.",
            "Penalize a verified decision when evidence supports only part of the finding or belongs to another company, period, or claim.",
        ],
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.EXPECTED_OUTPUT,
            SingleTurnParams.RETRIEVAL_CONTEXT,
        ],
        model=model,
        threshold=0.8,
    )
    faithfulness_metric = FaithfulnessMetric(threshold=0.8, model=model, include_reason=True, async_mode=False)
    contextual_relevancy_metric = ContextualRelevancyMetric(threshold=0.8, model=model, include_reason=True, async_mode=False)

    results: list[dict[str, Any]] = []
    for case, test_case in zip(cases, test_cases):
        fid = case["finding_id"]
        item: dict[str, Any] = {"test_case_id": case["test_case_id"], "finding_id": fid}
        for metric_name, metric in [
            ("decision_correctness", decision_metric),
            ("faithfulness", faithfulness_metric),
            ("contextual_relevancy", contextual_relevancy_metric),
        ]:
            try:
                metric.measure(test_case)
                item[metric_name] = {
                    "score": metric.score,
                    "passed": metric.is_successful(),
                    "reason": getattr(metric, "reason", None),
                }
            except Exception as exc:
                item[metric_name] = {"error": str(exc)}
        results.append(item)
    return {"provider": "OpenAI", "model": model.get_model_name(), "endpoint": model.endpoint, "results": results}


def run_verification_dataset(
    golden_path: str | Path,
    predictions_path: str | Path | None = None,
    output_path: str | Path | None = None,
    *,
    use_judge: bool = True,
) -> dict[str, Any]:
    golden = load_json(golden_path)
    if "test_cases" not in golden and isinstance(golden.get("verification"), dict):
        golden = golden["verification"]
    if not use_judge:
        raise ValueError("LLM judging is mandatory for verification evaluation.")
    cases = golden["test_cases"]
    if predictions_path:
        raw = load_json(predictions_path)
        assessments = extract_assessments(raw)
        predictions = {a["finding_id"]: a for a in assessments if a.get("finding_id") and a.get("verification_status")}
    else:
        predictions = {c["finding_id"]: c["actual_verification"] for c in cases}
    missing = [c["finding_id"] for c in cases if c["finding_id"] not in predictions]
    if missing:
        raise ValueError(f"Predictions are missing {len(missing)} finding IDs: {missing}")
    deterministic = deterministic_metrics(cases, predictions)
    report: dict[str, Any] = {
        "dataset": golden.get("dataset_name"),
        "trace_id": golden.get("trace_id"),
        "prediction_source": str(predictions_path) if predictions_path else "original trace baseline stored in golden dataset",
        "deterministic_metrics": deterministic,
    }
    if use_judge:
        try:
            report["deepeval"] = run_deepeval(cases, predictions)
            report["deepeval"]["status"] = "completed"
        except Exception as exc:
            report["deepeval"] = {"status": "not_run", "reason": str(exc), "setup": "Configure OPENAI_API_KEY in .env and install deepeval and requests."}
    if output_path is not None:
        Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def verification_eval_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Edrak verification output against a verification golden dataset.")
    parser.add_argument("--golden", default="verification_golden.json")
    parser.add_argument("--predictions", default=None)
    parser.add_argument("--output", default="verification_evaluation_report.json")
    args = parser.parse_args(argv)
    report = run_verification_dataset(args.golden, args.predictions, args.output)
    deterministic = report["deterministic_metrics"]
    print(json.dumps({
        "output": args.output,
        "deterministic_metrics": {k: v for k, v in deterministic.items() if k != "per_case"},
        "deepeval_status": report["deepeval"].get("status", "completed"),
        "judge_model": report["deepeval"].get("model", "Not initialized"),
    }, indent=2))
    return 0 if report["deepeval"].get("status") == "completed" else 1


WORKER_GROUND_TRUTH_HINTS: dict[str, tuple[str, ...]] = {
    "customer_trends": ("customer_trends", "customer", "trends"),
    "market_intelligence": ("market_intelligence", "market"),
    "competitor_intelligence": ("competitor_intelligence", "competitor"),
    "internal_intelligence": ("internal_intelligence", "internal"),
}


def infer_worker_from_path(path: str | Path) -> str | None:
    name = Path(path).name.lower()
    for worker, hints in WORKER_GROUND_TRUTH_HINTS.items():
        if any(hint in name for hint in hints):
            return worker
    return None


def find_matching_ground_truth(worker: str, root: str | Path) -> str | None:
    normalized = worker.lower()
    candidates = []
    for path in Path(root).rglob("*.json"):
        text = path.name.lower()
        if "ground_truth" in text or "golden" in text:
            if any(h in text for h in WORKER_GROUND_TRUTH_HINTS.get(worker, (worker,))):
                candidates.append(str(path))
    if not candidates:
        for path in Path(root).rglob("*.json"):
            text = path.name.lower()
            if "ground_truth" in text or "golden" in text:
                if any(k in text for k in ("market", "customer", "competitor", "internal")):
                    candidates.append(str(path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def discover_worker_trace_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    if not root_path.exists():
        return []
    candidates: list[Path] = []
    for path in sorted(root_path.rglob("*.json")):
        name = path.name.lower()
        if any(token in name for token in ("ground_truth", "golden", "verification", "baseline", "evaluation", "verify_report", "review_table")):
            continue
        if any(part.lower() in {"evaluation", "golden", "verification_evaluation", "newreport"} for part in path.parts):
            continue
        if "package-lock" in name or "tsconfig" in name or "schema" in name:
            continue
        candidates.append(path)
    return candidates


def choose_best_trace_for_worker(worker: str, trace_paths: list[Path]) -> Path | None:
    if not trace_paths:
        return None
    worker_key = worker.lower()
    ranked = sorted(trace_paths, key=lambda p: (0 if worker_key in p.name.lower() else 1, len(p.name)))
    return ranked[0]


def find_verification_predictions(root: str | Path, golden_path: str | Path) -> Path | None:
    root_path = Path(root)
    golden = load_json(golden_path)
    if "trace_id" not in golden and isinstance(golden.get("verification"), dict):
        golden = golden["verification"]
    trace_id = golden.get("trace_id")
    if trace_id:
        trace_prediction = root_path / "artifacts" / "runs" / str(trace_id) / "verification_tracing.json"
        if trace_prediction.is_file():
            return trace_prediction

    saved_report = root_path / "Evaluation" / "NewReport" / "verify_report.json"
    if saved_report.is_file():
        try:
            data = load_json(saved_report)
            if data.get("dataset") == golden.get("dataset_name"):
                return saved_report
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return None


def run_all_worker_evaluations(
    root: str | Path,
    out_dir: str | Path,
    *,
    use_judge: bool = True,
    second_pass: bool = True,
    config: str | Path | None = None,
    ground_truth_path: str | Path | None = None,
    verification_golden: str | Path | None = None,
    verification_predictions: str | Path | None = None,
) -> dict[str, Any]:
    if not use_judge or not second_pass:
        raise ValueError("LLM judging and second-pass review are mandatory.")
    repo_root = Path(root)
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    traces_by_worker: dict[str, list[Path]] = {}
    for path in discover_worker_trace_files(repo_root):
        try:
            value = read_json(str(path))
        except Exception:
            continue
        worker = detect_worker(value) or infer_worker_from_path(path)
        if not worker:
            continue
        traces_by_worker.setdefault(worker, []).append(path)

    summary_rows: list[dict[str, Any]] = []
    worker_results: list[dict[str, Any]] = []
    errors: list[str] = []
    if not traces_by_worker:
        errors.append("No worker trace JSON was discovered for mandatory benchmark evaluation.")
        for worker in sorted(WORKER_GROUND_TRUTH_HINTS):
            gt = str(ground_truth_path) if ground_truth_path else find_matching_ground_truth(worker, repo_root)
            summary_rows.append({
                "worker": worker,
                "status": "not_found",
                "trace": None,
                "ground_truth": gt,
                "output": None,
                "coverage_score": None,
                "strict_coverage_score": None,
                "finding_count": None,
                "note": "No worker trace JSON was discovered. Add a trace file or run the worker to populate it.",
            })

    for worker in sorted(traces_by_worker):
        trace_candidates = traces_by_worker[worker]
        trace_path = choose_best_trace_for_worker(worker, trace_candidates)
        if trace_path is None:
            continue
        gt_path = str(ground_truth_path) if ground_truth_path else find_matching_ground_truth(worker, repo_root)
        state: EvalState = {
            "trace_path": str(trace_path),
            "use_judge": use_judge,
            "second_pass": second_pass,
            "second_pass_top_k": 5,
        }
        if gt_path:
            state["ground_truth_path"] = gt_path
        if config:
            state["config_path"] = str(config)
        try:
            final = run_evaluation(state)
        except Exception as exc:
            error = f"{worker}: {type(exc).__name__}: {exc}"
            errors.append(error)
            summary_rows.append({"worker": worker, "status": "error", "error": str(exc), "trace": str(trace_path), "ground_truth": gt_path})
            continue
        if final.get("errors"):
            error_text = "; ".join(final["errors"])
            errors.append(f"{worker}: {error_text}")
            summary_rows.append({"worker": worker, "status": "error", "error": error_text, "trace": str(trace_path), "ground_truth": gt_path})
            continue
        result = final["result"]
        out_name = f"{worker}_evaluation.json"
        output_file = output_dir / out_name
        output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        mandatory_failures = _mandatory_evaluation_failures(result)
        if mandatory_failures:
            errors.extend(f"{worker}: {failure}" for failure in mandatory_failures)
        summary_row = {
            "worker": worker,
            "status": "error" if mandatory_failures else "ok",
            "trace": str(trace_path),
            "ground_truth": gt_path,
            "output": str(output_file),
            "coverage_score": result.get("coverage_score"),
            "strict_coverage_score": result.get("strict_coverage_score"),
            "finding_count": result.get("finding_count"),
            "mandatory_evaluation_failures": mandatory_failures,
        }
        summary_rows.append(summary_row)
        worker_results.append({"worker": worker, "result": result, "output": str(output_file)})

    verification_summary: dict[str, Any]
    golden_path = Path(verification_golden) if verification_golden else (
        repo_root / "Evaluation" / "verification_evaluation" / "verification_golden.json"
    )
    if not golden_path.is_file():
        verification_summary = {
            "status": "not_found",
            "golden": str(golden_path),
            "note": "Verification golden dataset was not found.",
        }
    else:
        prediction_path = Path(verification_predictions) if verification_predictions else find_verification_predictions(repo_root, golden_path)
        verification_output = output_dir / "verification_evaluation_report.json"
        try:
            verification_report = run_verification_dataset(
                golden_path,
                prediction_path,
                verification_output,
                use_judge=use_judge,
            )
            metrics = verification_report["deterministic_metrics"]
            verification_summary = {
                "status": "ok" if verification_report["deepeval"].get("status") == "completed" else "error",
                "golden": str(golden_path),
                "predictions": str(prediction_path) if prediction_path else None,
                "prediction_source": verification_report["prediction_source"],
                "output": str(verification_output),
                "deepeval_status": verification_report["deepeval"].get("status"),
                "deterministic_metrics": {
                    key: value for key, value in metrics.items() if key != "per_case"
                },
            }
            if verification_summary["status"] == "error":
                errors.append("verification evaluation: mandatory LLM judge did not complete.")
        except Exception as exc:
            error_text = f"verification evaluation: {type(exc).__name__}: {exc}"
            errors.append(error_text)
            verification_summary = {
                "status": "error",
                "golden": str(golden_path),
                "predictions": str(prediction_path) if prediction_path else None,
                "error": str(exc),
            }

    summary = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "repo_root": str(repo_root),
        "workers": summary_rows,
        "total_workers": len(summary_rows),
        "total_ok": sum(1 for row in summary_rows if row.get("status") == "ok"),
        "verification": verification_summary,
        "errors": errors,
    }
    summary_file = output_dir / "all_worker_summary.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
