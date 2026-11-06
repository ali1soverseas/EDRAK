"""Graph nodes, helpers, routers and report rendering for the competitor research graph.

Flow (see graph.py):

    identify_requirements -> retrieve_kb -> assess_gaps -> plan_gap_queries      (gap_nodes.py)
      -> search -> synthesize -> ground -> check_requirements
      -> prepare_next_stage -> generate_queries -> search ...                    (later stages)
      -> final_report

Every requirement carries two sets of keys: the current schema (requirement_id,
research_question, expected_evidence, category, importance, time_scope) and the keys the
prompts and the report read (id, requirement). They always hold the same values.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import pprint
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from deepeval.tracing import observe, update_current_span
from dotenv import load_dotenv
from tavily import TavilyClient

from edrak.contracts.evidence import Evidence, EvidenceRef, EvidenceRelation, SourceType
from edrak.contracts.result import Finding, FindingCategory, WorkerResult, WorkerStatus
from edrak.contracts.task import WorkerType
from edrak.core.action_log import log_action
from edrak.core.llm import get_chat_model, schema_instruction
from edrak.guardrails.content import sanitize as sanitize_text

from .prompts import (
    STAGE_SYNTH_GUIDANCE,
    comparison_prompt,
    consolidation_prompt,
    queries_prompt,
    requirements_prompt,
    synthesis_prompt,
)
from .sources import (
    best_passage,
    domain_of,
    is_official as _is_official_source,
    judge as judge_source,
    official_domains,
)
from .state import (
    CONFIDENCE_RANK,
    DUPLICATE_SIMILARITY,
    INCLUDE_RAW_FINDINGS,
    MAX_EVIDENCE_CHARS,
    MAX_EVIDENCE_PER_FINDING,
    MAX_QUERIES_PER_REQUIREMENT,
    MAX_QUERIES_TOTAL,
    MAX_REQUIREMENTS_PER_COMPETITOR,
    MAX_RESEARCH_STAGES,
    MAX_RESULTS_PER_QUERY,
    MAX_STORE_CHARS,
    MIN_NEW_FINDINGS_TO_CONTINUE,
    SOURCE_CHARS,
    STAGE_STRATEGY,
    ComparisonReport,
    CompetitorProfile,
    CompetitorState,
    FindingList,
    ResearchRequirements,
    SearchQueryList,
)
from .tracing import save_competitor_span

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT AND CLIENTS
# ============================================================

# nodes.py -> competitor_intelligence -> agents -> edrak -> src -> backend -> repo root
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
load_dotenv(ENV_PATH)


def _require(name: str, value: Optional[str]) -> str:
    """Fail with the variable name when a credential is absent (checked per call, not at import)."""
    if not value:
        raise ValueError(f"{name} is missing")
    return value


def _tavily_key() -> str:
    from edrak.core.llm import provider_key

    load_dotenv(ENV_PATH, override=True)
    key, variable = provider_key("TAVILY_API_KEY", "competitor")
    return _require(variable, key)


def _banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(f"[competitor_intelligence] {title}")
    print("=" * 80)


def _compat_httpx2_brotli() -> None:
    """google-brotli process() rejects the keyword httpx2 always passes."""
    try:
        from httpx2._decoders import BrotliDecoder
    except ImportError:
        return

    original_init = BrotliDecoder.__init__

    def _init(self):
        original_init(self)
        if hasattr(self.decompressor, "decompress"):
            return
        decode = self._decompress

        def _decode(data, output_buffer_limit=None):
            return decode(data)

        self._decompress = _decode

    BrotliDecoder.__init__ = _init


_compat_httpx2_brotli()

# Built on first use, so a rotated key is picked up without a restart and an unconfigured
# worker imports cleanly instead of raising while the module loads.
_tavily_client = None
_tavily_client_key = None
_llm = None


def tavily():
    global _tavily_client, _tavily_client_key
    current_key = _tavily_key()
    if _tavily_client is None or _tavily_client_key != current_key:
        _tavily_client = TavilyClient(api_key=current_key)
        _tavily_client_key = current_key
    return _tavily_client


def llm():
    """The shared model, used for planning, queries, synthesis and consolidation."""
    global _llm
    if _llm is None:
        _llm = get_chat_model(agent="competitor", temperature=0)
    return _llm


def llm_strong():
    """Same model as `llm`; kept so call sites that want the stronger model keep their intent."""
    return llm()


# ============================================================
# DEBUG OUTPUT
# ============================================================

from edrak.core.config import settings as _settings  # noqa: E402

OUTPUT_DIR = (
    _settings.ARTIFACTS_PATH if _settings.ARTIFACTS_PATH.is_absolute() else _settings.BASE_DIR / _settings.ARTIFACTS_PATH
) / "competitor_runs"


def save_node_output(node_name: str, output: Dict[str, Any], research_stage: int = 1) -> None:
    """Per-stage JSON for debugging; written only with EDRAK_DEBUG on, under artifacts/."""
    if not _settings.EDRAK_DEBUG:
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"stage_{research_stage}_{node_name}.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# ============================================================
# STRUCTURED MODEL CALLS
# ============================================================

def call_structured(prompt: str, model_class, strong: bool = False, retries: int = 1,
                    raise_on_failure: bool = False):
    """Structured output via json_schema, with the schema restated in the prompt.

    method="function_calling" returned nothing for every nested-list schema here, so it is not
    used. When the structured call returns None, the plain reply is parsed as JSON once more.
    Returns None if no valid result came back (or raises, with raise_on_failure).
    """
    model = llm_strong() if strong else llm()
    structured = model.with_structured_output(model_class, method="json_schema")
    messages = [prompt, schema_instruction(model_class)]
    last_error: Optional[BaseException] = None
    received_none = False

    for attempt in range(retries + 1):
        try:
            result = structured.invoke(messages)
            if result is not None:
                return result
            received_none = True
            logger.warning("structured call for %s returned None (attempt %d)", model_class.__name__, attempt + 1)
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last_error = exc
            logger.warning("structured call for %s failed (attempt %d): %s: %s",
                           model_class.__name__, attempt + 1, type(exc).__name__, exc)

    if received_none:
        try:
            response = model.invoke(messages)
            content = response.content if hasattr(response, "content") else response
            if isinstance(content, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            text = str(content).strip()
            start = text.find("{")
            if start >= 0:
                payload, _ = json.JSONDecoder().raw_decode(text[start:])
                return model_class.model_validate(payload)
            last_error = ValueError("model reply contained no JSON object")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("plain-JSON recovery for %s failed: %s: %s", model_class.__name__, type(exc).__name__, exc)

    if raise_on_failure:
        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "no structured result or JSON reply"
        raise RuntimeError(f"Structured output for {model_class.__name__} failed after {retries + 1} attempts: {detail}") from last_error
    return None


# ============================================================
# URL AND SOURCE HELPERS
# ============================================================

def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str, domains: List[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains if d)


def canonical_url(url: str) -> str:
    u = normalize_url(url).lower().split("#")[0].split("?")[0]
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def result_text(result: Dict[str, Any], limit: int) -> str:
    """Search `content` plus `raw_content`, capped."""
    content = (result.get("content") or "").strip()
    raw = (result.get("raw_content") or "").strip()
    text = content
    if raw and len(text) < limit:
        text = f"{text}\n{raw}" if text else raw
    return text[:limit]


def is_official(url: str, competitor: str) -> bool:
    """The URL is on the competitor's own site (domains registered per run; see sources.py)."""
    return _is_official_source(url, competitor)


def _aliases(state: CompetitorState, competitor: str) -> List[str]:
    return list(((state.get("competitor_info") or {}).get(competitor) or {}).get("aliases") or [])


# ============================================================
# REQUIREMENT HELPERS
# ============================================================

def requirement_record(competitor: str, rid: str, question: str, *, importance: str = "medium",
                       time_scope: str = "", **extra: Any) -> Dict[str, Any]:
    """One requirement with both key sets: the current schema and the keys prompts and reports read."""
    question = " ".join((question or "").split())
    return {
        "id": rid, "requirement_id": rid, "competitor": competitor,
        "requirement": question, "research_question": question,
        "importance": importance or "medium", "time_scope": time_scope or "",
        **extra,
    }


def requirements_by_id(state: CompetitorState) -> Dict[str, Dict[str, Any]]:
    return {r["id"]: r for r in state.get("research_requirements", []) if r.get("id")}


def competitor_requirements(state: CompetitorState, competitor: str) -> List[Dict[str, Any]]:
    return [r for r in state.get("research_requirements", []) if r.get("competitor") == competitor]


def source_index_for(results: List[Dict[str, Any]], competitor: str) -> Dict[str, Dict[str, Any]]:
    """url -> result, for ONE competitor's results."""
    index: Dict[str, Dict[str, Any]] = {}
    for r in results:
        if r.get("competitor") != competitor:
            continue
        url = normalize_url(r.get("url", ""))
        if url:
            index[url] = r
    return index


# ============================================================
# EVIDENCE FOR THE MODEL
# ============================================================

def build_evidence(results: List[Dict[str, Any]]):
    """(evidence text, {S1: result, ...}). The model cites the short IDs, never URLs."""
    blocks: List[str] = []
    id_map: Dict[str, Dict[str, Any]] = {}
    for r in results:
        text = (r.get("text") or "")[:SOURCE_CHARS]
        if not text:
            continue
        sid = f"S{len(id_map) + 1}"
        id_map[sid] = r
        origin = "stored knowledge base" if r.get("from_knowledge_base") else r.get("search_type", "")
        blocks.append(
            f"SOURCE ID: {sid}\nCOMPETITOR: {r.get('competitor', '')}\nSEARCH TYPE: {origin}\n"
            f"OFFICIAL: {'yes' if r.get('is_official') else 'no'}\nTITLE: {r.get('title', '')}\n"
            f"URL: {r.get('url', '')}\nSOURCE TEXT:\n{text}"
        )
    return "\n\n----------------------\n\n".join(blocks), id_map


def chunk_results(results: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Batches that fit MAX_EVIDENCE_CHARS; results are split across batches, never truncated."""
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    size = 0
    for r in results:
        length = min(len(r.get("text") or ""), SOURCE_CHARS) + 300
        if current and size + length > MAX_EVIDENCE_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(r)
        size += length
    if current:
        batches.append(current)
    return batches


def resolve_source(ref: str, id_map: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A citation as an S# ID (or, defensively, a URL) -> the supplied source, else None."""
    ref = (ref or "").strip().strip("[]").upper()
    if ref in id_map:
        return id_map[ref]
    wanted = canonical_url(ref.lower())
    return next((r for r in id_map.values() if canonical_url(r.get("url", "")) == wanted), None) if wanted else None


def attach_evidence(source_ids: List[str], statement: str, id_map: Dict[str, Dict[str, Any]],
                    competitor: str) -> List[Dict[str, Any]]:
    """Real evidence for the IDs the model cited. An unknown ID creates no evidence."""
    evidence: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for ref in source_ids:
        source = resolve_source(ref, id_map)
        if not source or source.get("competitor") != competitor:
            continue
        url = normalize_url(source.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        evidence.append({
            "source_url": url,
            "source_title": source.get("title", ""),
            "passage": best_passage(source.get("text") or "", statement),
            "is_official": bool(source.get("is_official")) or is_official(url, competitor),
            "from_knowledge_base": bool(source.get("from_knowledge_base")),
            "published_date": source.get("published_date") or source.get("fetched_at"),
        })
        if len(evidence) >= MAX_EVIDENCE_PER_FINDING:
            break
    return evidence


# ============================================================
# CONFIDENCE
# ============================================================

def normalize_confidence_value(value: Any) -> Optional[float]:
    """0-1 from a number, a percentage or a named level; None when unknown."""
    if value is None:
        return None
    if isinstance(value, str):
        named = {"high": 0.85, "medium": 0.65, "low": 0.4}.get(value.strip().lower())
        if named is not None:
            return named
        try:
            value = float(value.strip().rstrip("%"))
        except ValueError:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1:
        number /= 100
    return round(max(0.0, min(number, 1.0)), 3)


def source_evidence_score(source: Dict[str, Any], competitor: str) -> float:
    """How far one source can be trusted. Third-party sources stay fully eligible."""
    official = bool(source.get("is_official")) or is_official(source.get("url", ""), competitor)
    score = 0.8 if official else 0.62
    search_score = source.get("score")
    if isinstance(search_score, (int, float)):
        score += min(0.08, max(0.0, float(search_score)) * 0.08)
    if len(source.get("text") or "") >= 1500:
        score += 0.04
    return round(min(score, 0.92), 3)


def finding_evidence_quality(finding: Dict[str, Any], source_index: Dict[str, Dict[str, Any]]) -> str:
    """first_party, third_party, mixed or none."""
    kinds = set()
    for e in finding.get("evidence", []):
        url = normalize_url(e.get("source_url", ""))
        if url not in source_index:
            continue
        kinds.add("first" if e.get("is_official") or is_official(url, finding.get("competitor", "")) else "third")
    if kinds == {"first"}:
        return "first_party"
    if kinds == {"third"}:
        return "third_party"
    return "mixed" if kinds else "none"


def calculate_finding_confidence(finding: Dict[str, Any], source_index: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """The strongest cited source sets the baseline; more independent sources add a little."""
    competitor = finding.get("competitor", "")
    scores, seen = [], set()
    for e in finding.get("evidence", []):
        url = normalize_url(e.get("source_url", ""))
        if not url or url in seen or url not in source_index:
            continue
        seen.add(url)
        scores.append(source_evidence_score(source_index[url], competitor))
    if not scores:
        return None
    scores.sort(reverse=True)
    confidence = scores[0] + (min(0.06, 0.02 * (len(scores) - 1)) if len(scores) >= 2 else 0)
    return round(min(confidence, 0.98), 3)


def ensure_finding_confidence(finding: Dict[str, Any], source_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Every verified finding gets a confidence no higher than its evidence supports, and a quality label."""
    existing = normalize_confidence_value(finding.get("confidence"))
    calculated = calculate_finding_confidence(finding, source_index)
    if existing is not None and calculated is not None:
        finding["confidence"] = min(existing, calculated)
    else:
        finding["confidence"] = existing if existing is not None else calculated
    finding["evidence_quality"] = finding_evidence_quality(finding, source_index)
    return finding


def requirement_confidence(findings: List[Dict[str, Any]], requirement_id: str) -> Optional[float]:
    values = [normalize_confidence_value(f.get("confidence")) for f in findings if f.get("requirement_id") == requirement_id]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def requirement_evidence_quality(findings: List[Dict[str, Any]], requirement_id: str) -> str:
    kinds = {f.get("evidence_quality") for f in findings if f.get("requirement_id") == requirement_id} - {None, "none"}
    if not kinds:
        return "none"
    return next(iter(kinds)) if len(kinds) == 1 else "mixed"


def calculate_overall_confidence(findings: List[Finding]) -> Optional[float]:
    values = [f.confidence for f in findings if f.confidence is not None]
    return round(sum(values) / len(values), 3) if values else None


# ============================================================
# FINDING HELPERS
# ============================================================

def finding_view(finding: Dict[str, Any], text_limit: int = 400) -> Dict[str, Any]:
    """A compact finding for prompt context."""
    return {
        "competitor": finding.get("competitor", ""),
        "requirement_id": finding.get("requirement_id", ""),
        "finding": (finding.get("finding", "") or "")[:text_limit],
        "confidence": normalize_confidence_value(finding.get("confidence")),
        "evidence_quality": finding.get("evidence_quality", "none"),
        "evidence_urls": [e.get("source_url", "") for e in finding.get("evidence", [])] or finding.get("evidence_urls", []),
    }


def is_duplicate(new: Dict[str, Any], existing: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The existing finding `new` duplicates (same competitor and requirement, near-identical text)."""
    a = (new.get("finding") or "").strip().lower()
    for old in existing:
        if old.get("competitor") != new.get("competitor") or old.get("requirement_id") != new.get("requirement_id"):
            continue
        b = (old.get("finding") or "").strip().lower()
        if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= DUPLICATE_SIMILARITY:
            return old
    return None


def merge_findings(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Existing findings plus the new ones that are not duplicates. A duplicate adds its new
    evidence to the finding it duplicates instead of being dropped with it."""
    merged = [dict(f, evidence=list(f.get("evidence", []))) for f in existing]
    for f in new:
        old = is_duplicate(f, merged)
        if old is None:
            merged.append(f)
            continue
        urls = {normalize_url(e.get("source_url", "")) for e in old["evidence"]}
        for e in f.get("evidence", []):
            if normalize_url(e.get("source_url", "")) not in urls and len(old["evidence"]) < MAX_EVIDENCE_PER_FINDING:
                old["evidence"].append(e)
    return merged


# ============================================================
# NODE 1: IDENTIFY RESEARCH REQUIREMENTS
# ============================================================

REQUIREMENT_SHAPE = """
FOR EACH REQUIREMENT ALSO GIVE
- research_question: the requirement as one precise question (at most ~30 words)
- importance: critical, high, medium or low, for this research goal
- time_scope: the period that matters ("latest available" unless the goal says otherwise)
- requirement_id: leave empty; it is assigned for you
"""


@observe(type="node", name="competitor_intelligence.identify_research_requirements")
def identify_research_requirements_node(state: CompetitorState):
    span_input = {
        "company": state.get("company"),
        "competitors": state.get("competitors", []),
        "business_context": state.get("business_context", {}),
    }
    span_metadata = {"research_stage": state.get("research_stage", 1)}
    update_current_span(input=span_input, metadata=span_metadata)
    log_action("competitor_intelligence", "identifying research requirements")
    _banner("NODE: identify_requirements")

    competitors = list(state.get("competitors") or [])
    canonical = {c.strip().lower(): c for c in competitors}
    requirements: List[Dict[str, Any]] = []
    per_competitor: Dict[str, int] = {}
    seen_text: set[tuple[str, str]] = set()

    def add(competitor: str, question: str, **fields: Any) -> None:
        key = (competitor, " ".join(question.lower().split()))
        if not question.strip() or key in seen_text or per_competitor.get(competitor, 0) >= MAX_REQUIREMENTS_PER_COMPETITOR:
            return
        seen_text.add(key)
        per_competitor[competitor] = per_competitor.get(competitor, 0) + 1
        requirements.append(requirement_record(competitor, f"R{len(requirements) + 1}", question, **fields))

    if competitors:
        result = call_structured(requirements_prompt(state) + REQUIREMENT_SHAPE, ResearchRequirements, raise_on_failure=True)
        for item in getattr(result, "requirements", None) or []:
            competitor = canonical.get((item.competitor or "").strip().lower())
            if not competitor:
                logger.warning("dropping a requirement for an unknown competitor: %s", item.competitor)
                continue
            add(competitor, item.research_question, importance=item.importance, time_scope=item.time_scope)

    missing = [c for c in competitors if not per_competitor.get(c)]
    if missing:
        logger.warning("no requirements for: %s", ", ".join(missing))
    log_action("competitor_intelligence", f"{len(requirements)} research requirement(s) for {len(competitors)} competitor(s)")
    # pprint.pprint(requirements, sort_dicts=False)

    output = {
        "research_requirements": requirements,
        "missing_requirement_competitors": missing,
        "research_stage": state.get("research_stage", 1),
        "research_strategy": STAGE_STRATEGY.get(state.get("research_stage", 1), "official_sources"),
    }
    update_current_span(output=output)
    save_competitor_span("competitor_intelligence.identify_research_requirements",
                         input=span_input, output=output, metadata=span_metadata)
    return output


# ============================================================
# NODE 2: GENERATE SEARCH QUERIES (later stages; stage 1 is planned from the knowledge-base gaps)
# ============================================================

def _scope_text(state: CompetitorState, competitors: List[str]) -> str:
    """Per competitor: official domains, and what the knowledge base verified about its products."""
    kb = state.get("kb_profiles") or {}
    lines = []
    for c in competitors:
        domains = official_domains(c) or (kb.get(c) or {}).get("official_domains") or []
        offerings = (kb.get(c) or {}).get("offerings") or []
        line = f"- {c}: official domain(s) {', '.join(domains) or 'unknown'}"
        if offerings:
            line += f"; verified products: {', '.join(offerings[:12])}"
        lines.append(line)
    return "\n".join(lines) or "(none)"


@observe(type="node", name="competitor_intelligence.generate_queries")
def generate_queries_node(state: CompetitorState):
    log_action("competitor_intelligence", "generating search queries")
    stage = state.get("research_stage", 1)
    strategy = STAGE_STRATEGY.get(stage, "targeted_missing_requirements")
    span_input = {
        "research_stage": stage,
        "research_strategy": strategy,
        "missing_information": state.get("missing_information", []),
        "executed_query_count": len(state.get("executed_queries", [])),
    }
    span_metadata = {"research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    _banner(f"NODE: generate_queries  STAGE {stage}: {strategy.upper()}")

    reqs = requirements_by_id(state)
    if stage == 1:
        targets = [{"requirement_id": r["id"], "competitor": r["competitor"], "requirement": r["requirement"]}
                   for r in reqs.values()]
    else:
        targets = [{"requirement_id": m["requirement_id"], "competitor": m["competitor"],
                    "requirement": reqs[m["requirement_id"]]["requirement"],
                    "missing_reason": m.get("missing_reason", ""),
                    "still_needed": m.get("missing", [])}
                   for m in state.get("missing_information", []) if m.get("requirement_id") in reqs]
    executed = state.get("executed_queries", [])
    budget = max(0, MAX_QUERIES_TOTAL - len(executed))
    if not targets or not budget:
        output = {"search_queries": [], "research_strategy": strategy}
        update_current_span(output=output)
        return output

    competitors = sorted({t["competitor"] for t in targets})
    known = [finding_view(f) for f in state.get("verified_findings", []) if f.get("competitor") in competitors][:40]
    previous = [q.get("query", "") for q in executed][-60:]
    result = call_structured(
        queries_prompt(state, stage, strategy, targets, known, previous, _scope_text(state, competitors)),
        SearchQueryList,
    )

    valid = {t["requirement_id"]: t for t in targets}
    executed_keys = {(q.get("competitor", "").lower(), q.get("query", "").lower()) for q in executed}
    seen: set[tuple[str, str]] = set()
    per_req: Dict[str, int] = {}
    queries: List[Dict[str, Any]] = []
    for q in (result.queries if result else []):
        rid = (q.requirement_id or "").strip()
        target = valid.get(rid)
        if not target:
            logger.warning("dropping a query with unknown requirement_id %r: %s", rid, q.query)
            continue
        competitor = target["competitor"]
        text, domain_hint = _query_without_site_operator((q.query or "").strip())
        key = (competitor.lower(), text.lower())
        if not text or key in seen or key in executed_keys or per_req.get(rid, 0) >= MAX_QUERIES_PER_REQUIREMENT:
            continue
        if len(queries) >= budget:
            break
        seen.add(key)
        per_req[rid] = per_req.get(rid, 0) + 1
        queries.append({"competitor": competitor, "requirement_id": rid, "requirement": target["requirement"],
                        "query": text, "domain_hint": domain_hint, "purpose": q.purpose, "search_type": strategy})

    for q in queries:
        print(f'\n[{q["competitor"]}] {q["query"]}\n  {q["requirement_id"]}: {q["requirement"]}')
    if not queries:
        print("\nWARNING: no valid new queries survived validation.")

    output = {"search_queries": queries, "research_strategy": strategy}
    update_current_span(output=output)
    save_competitor_span("competitor_intelligence.generate_queries", input=span_input, output=output, metadata=span_metadata)
    return output


def after_queries_router(state: CompetitorState) -> str:
    if state.get("search_queries"):
        return "search"
    print("\nNo queries to run. Finishing the research loop.")
    return "finish"


def _query_without_site_operator(query: str) -> tuple[str, list[str]]:
    """Move any site: operator out of the search text into a domain hint."""
    hinted = re.findall(r"(?:^|\s)site:([a-z0-9.-]+)", query, flags=re.IGNORECASE)
    clean_query = re.sub(r"(?:^|\s)site:[a-z0-9.-]+", " ", query, flags=re.IGNORECASE)
    clean_query = re.sub(r"\s+", " ", clean_query).strip()
    domains = sorted({domain_of(domain) for domain in hinted if domain_of(domain)})
    return clean_query, domains


def _search_domains(competitor: str, query: str, strategy: str, domain_hint: list[str] | None = None) -> list[str]:
    """Registered official domains, or the planner's explicit site: scope; only for official-source searches."""
    if strategy != "official_sources":
        return []
    registered = official_domains(competitor)
    if registered:
        return registered
    if domain_hint:
        return sorted({domain_of(domain) for domain in domain_hint if domain_of(domain)})
    _, hinted = _query_without_site_operator(query)
    return hinted


# ============================================================
# NODE 3: SEARCH
# ============================================================

@observe(type="tool", name="tavily_search", description="Search the web for competitor intelligence evidence.")
def run_tavily(query: str, stage: int, domains: Optional[List[str]] = None) -> Dict[str, Any]:
    span_input = {"query": query, "stage": stage, "domains": domains or []}
    span_metadata = {"provider": "tavily", "research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    kwargs: Dict[str, Any] = {"query": query, "search_depth": "advanced", "max_results": MAX_RESULTS_PER_QUERY,
                              "include_raw_content": True}
    if domains:
        kwargs["include_domains"] = domains
    try:
        response = tavily().search(**kwargs)
    except Exception as exc:
        save_competitor_span("tavily_search", input=span_input, metadata=span_metadata, status="error",
                             error=f"{type(exc).__name__}: {exc}")
        raise
    span_output = {"result_count": len(response.get("results", [])),
                   "results": [{"title": r.get("title", ""), "url": r.get("url", ""), "score": r.get("score")}
                               for r in response.get("results", [])]}
    update_current_span(output=span_output)
    save_competitor_span("tavily_search", input=span_input, output=span_output, metadata=span_metadata)
    return response


@observe(type="tool", name="serper_search", description="Fallback web search through Serper.")
def run_serper(query: str, stage: int) -> tuple[dict[str, Any] | None, int | None]:
    span_input = {"query": query, "stage": stage}
    span_metadata = {"provider": "serper", "research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    if not serper_key:
        span_output = {"status": "skipped", "reason": "missing_api_key"}
        update_current_span(output=span_output)
        save_competitor_span("serper_search", input=span_input, output=span_output, metadata=span_metadata)
        return None, None
    try:
        response = requests.post("https://google.serper.dev/search",
                                 headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                                 json={"q": query, "num": MAX_RESULTS_PER_QUERY}, timeout=15)
    except Exception as exc:
        save_competitor_span("serper_search", input=span_input, metadata=span_metadata, status="error",
                             error=f"{type(exc).__name__}: {exc}")
        raise
    if response.status_code != 200:
        span_output = {"status": response.status_code, "result_count": 0}
        update_current_span(output=span_output)
        save_competitor_span("serper_search", input=span_input, output=span_output, metadata=span_metadata)
        return None, response.status_code
    try:
        data = response.json()
    except Exception as exc:
        save_competitor_span("serper_search", input=span_input, metadata=span_metadata, status="error",
                             error=f"{type(exc).__name__}: {exc}")
        raise
    organic = data.get("organic", [])
    span_output = {"status": response.status_code, "result_count": len(organic),
                   "results": [{"title": i.get("title", ""), "url": i.get("link", ""), "snippet": i.get("snippet", ""),
                                "date": i.get("date")} for i in organic]}
    update_current_span(output=span_output)
    save_competitor_span("serper_search", input=span_input, output=span_output, metadata=span_metadata)
    return data, response.status_code


def _serper_as_tavily(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"title": i.get("title", ""), "url": i.get("link", ""), "content": i.get("snippet", ""),
             "published_date": i.get("date"), "provider": "serper"} for i in (data or {}).get("organic", [])]


@observe(type="node", name="search_node", description="Search for competitor intelligence sources using Tavily and Serper.")
def search_node(state: CompetitorState):
    log_action("competitor_intelligence", "searching sources")
    stage = state.get("research_stage", 1)
    strategy = state.get("research_strategy", STAGE_STRATEGY.get(stage, "discovery"))
    span_input = {"research_stage": stage, "research_strategy": strategy,
                  "search_queries": state.get("search_queries", []),
                  "existing_source_count": len(state.get("search_results", []))}
    span_metadata = {"research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    _banner(f"NODE: search {strategy.upper()}")

    results = list(state.get("search_results", []))
    known = {(r["competitor"], normalize_url(r["url"])) for r in results if normalize_url(r.get("url", ""))}
    executed = list(state.get("executed_queries", []))
    dropped = list(state.get("dropped_sources", []))
    added = 0

    for item in state.get("search_queries", []):
        query, competitor = item["query"], item["competitor"]
        print(f"\n[{competitor}] Query: {query}")
        provider, error, raw = "tavily", None, []
        try:
            domains = _search_domains(competitor, query, strategy, item.get("domain_hint"))
            raw = run_tavily(query, stage, domains or None).get("results", [])
            if domains and not raw:  # nothing on the official site: search the open web instead
                raw = run_tavily(query, stage, None).get("results", [])
        except Exception as exc:  # noqa: BLE001 - fall back to Serper, then record the failure
            error = f"{type(exc).__name__}: {exc}"
            try:
                data, _status = run_serper(query, stage)
                raw, provider = _serper_as_tavily(data), "serper"
            except Exception as exc2:  # noqa: BLE001
                error = f"{error}; serper: {type(exc2).__name__}: {exc2}"
                raw = []

        kept = 0
        for r in raw:
            url = normalize_url(r.get("url", ""))
            if not url or (competitor, url) in known:
                continue
            # Guardrail: page text is data, never instructions.
            text, removed = sanitize_text(result_text(r, MAX_STORE_CHARS))
            record = {
                "competitor": competitor, "requirement_id": item.get("requirement_id", ""), "query": query,
                "purpose": item.get("purpose", ""), "search_type": strategy, "stage": stage,
                "title": r.get("title", ""), "url": url, "text": text, "provider": r.get("provider", provider),
                "publisher": host_of(url), "published_date": r.get("published_date"), "score": r.get("score"),
                "is_official": is_official(url, competitor), "guardrail_removed_lines": removed,
            }
            keep, reason = judge_source(record, competitor, _aliases(state, competitor))
            if not keep:
                dropped.append({"competitor": competitor, "url": url, "reason": reason, "stage": stage})
                continue
            known.add((competitor, url))
            results.append(record)
            added += 1
            kept += 1
        executed.append({"competitor": competitor, "requirement_id": item.get("requirement_id", ""), "query": query,
                         "stage": stage, "provider": provider, "result_count": len(raw), "kept": kept, "error": error})
        print(f"  {len(raw)} result(s), {kept} kept" + (f" ({error})" if error else ""))

    print(f"Total sources: {len(results)}")
    output = {"search_results": results, "executed_queries": executed,
              "new_sources_this_stage": added, "dropped_sources": dropped[-300:]}
    span_output = {"research_stage": stage, "new_source_count": added, "total_source_count": len(results),
                   "executed_query_count": len(executed), "dropped_source_count": len(dropped),
                   "providers": sorted({r.get("provider", "") for r in results})}
    update_current_span(output=span_output)
    save_competitor_span("competitor_intelligence.search", input=span_input, output=span_output, metadata=span_metadata)
    return output


# ============================================================
# NODE 4: SYNTHESIZE (this stage's sources, per competitor)
# ============================================================

@observe(type="node", name="competitor_intelligence.synthesize")
def synthesize_node(state: CompetitorState):
    log_action("competitor_intelligence", "summarizing findings from the sources")
    stage = state.get("research_stage", 1)
    _banner(f"NODE: synthesize  STAGE {stage}")
    guidance = STAGE_SYNTH_GUIDANCE.get(
        stage, "STAGE 4+ (gap filling): produce findings only for information newly supported by this evidence.")

    stage_results = [r for r in state.get("search_results", []) if r.get("stage") == stage and r.get("text")]
    verified = state.get("verified_findings", [])
    new_findings: List[Dict[str, Any]] = []

    for competitor in state.get("competitors", []):
        comp_reqs = competitor_requirements(state, competitor)
        sources = [r for r in stage_results if r.get("competitor") == competitor]
        if not comp_reqs or not sources:
            continue
        valid_ids = {r["id"] for r in comp_reqs}
        already = [finding_view(f) for f in verified if f.get("competitor") == competitor][:30]
        for batch in chunk_results(sources):
            evidence_text, id_map = build_evidence(batch)
            if not id_map:
                continue
            result = call_structured(synthesis_prompt(state, competitor, guidance, comp_reqs, already, evidence_text), FindingList)
            for item in (result.findings if result else []):
                rid = (item.requirement_id or "").strip()
                statement = " ".join((item.finding or "").split())
                if rid not in valid_ids or not statement:
                    continue
                evidence = attach_evidence(item.evidence_source_ids, statement, id_map, competitor)
                if not evidence:  # a claim without a real cited source is not a finding
                    continue
                new_findings.append({
                    "competitor": competitor, "requirement_id": rid, "finding": statement,
                    "category": item.category, "competitive_area": item.competitive_area,
                    "business_relevance": item.business_relevance, "evidence": evidence, "stage": stage,
                })

    # Only what is not already verified goes to grounding; a claim rejected earlier may come back
    # with better evidence, so new findings are compared with the verified set only.
    merged = merge_findings(verified, new_findings)
    truly_new = [f for f in merged[len(verified):]]
    findings = list(state.get("findings", [])) + truly_new
    log_action("competitor_intelligence", f"{len(new_findings)} evidence-backed finding(s) this stage, {len(truly_new)} new")
    return {"findings": findings, "new_findings": truly_new}


# ============================================================
# NODE 5: GROUND (each new finding against the sources it cites)
# ============================================================

_STOPWORDS = {
    "that", "this", "with", "from", "have", "been", "were", "their", "about", "into", "over",
    "than", "such", "also", "only", "does", "each", "when", "which", "while", "where", "after",
    "before", "between", "without", "including", "across", "offers", "provides", "supports", "using",
}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def grounding_problem(statement: str, source_text: str) -> str | None:
    """Why a finding is not supported by the text it cites, or None when it is.

    Deterministic on purpose: every number in the statement must appear in the sources, and most
    of its key words must too. The pipeline's verification stage does the careful review.
    """
    text = (source_text or "").lower()
    if not text.strip():
        return "cited sources have no text"
    flat = text.replace(",", "")
    for number in _NUMBER.findall(statement):
        if number.replace(",", "") not in flat:
            return f"the number {number} is not in the cited sources"
    words = {w for w in re.findall(r"[a-z][a-z0-9]{3,}", statement.lower()) if w not in _STOPWORDS}
    if not words:
        return None
    present = sum(1 for w in words if w in text or w[:-1] in text)
    if present / len(words) < 0.45:
        return "the cited sources do not say this"
    return None


@observe(type="node", name="competitor_intelligence.verify")
def verify_node(state: CompetitorState):
    """Keep the new findings that their cited sources support, and score their confidence."""
    stage = state.get("research_stage", 1)
    span_input = {"research_stage": stage, "new_findings": state.get("new_findings", []),
                  "previously_verified_count": len(state.get("verified_findings", [])),
                  "search_result_count": len(state.get("search_results", []))}
    span_metadata = {"research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    log_action("competitor_intelligence", "checking each finding against the sources it cites")
    _banner(f"NODE: ground  STAGE {stage}")

    previously_verified = state.get("verified_findings", [])
    accepted: List[Dict[str, Any]] = []
    rejected = 0
    for f in state.get("new_findings", []):
        competitor = f["competitor"]
        index = source_index_for(state.get("search_results", []), competitor)
        evidence = [e for e in f.get("evidence", []) if normalize_url(e.get("source_url", "")) in index]
        if not evidence:
            rejected += 1
            continue
        joined = "\n".join(index[normalize_url(e["source_url"])].get("text") or "" for e in evidence)
        problem = grounding_problem(f.get("finding", ""), joined)
        if problem:
            rejected += 1
            print(f"NOT GROUNDED [{competitor}] {f.get('finding', '')[:120]} ({problem})")
            continue
        accepted.append(ensure_finding_confidence({**f, "evidence": evidence}, index))

    verified = merge_findings(previously_verified, accepted)
    added = len(verified) - len(previously_verified)
    log_action("competitor_intelligence", f"{added} finding(s) grounded in their sources this stage, {rejected} set aside")
    output = {"verified_findings": verified, "new_verified_this_stage": added}
    span_output = {"verified_findings": verified, "new_verified_this_stage": added, "rejected_finding_count": rejected}
    update_current_span(output=span_output)
    save_competitor_span("competitor_intelligence.verify", input=span_input, output=span_output, metadata=span_metadata)
    return output


# ============================================================
# NODE 6: REQUIREMENT CHECK
# ============================================================

def check_research_requirements_node(state: CompetitorState):
    """Deterministic: a requirement is fulfilled once a grounded finding maps to it.

    The knowledge-base gaps (`kb_coverage`) say what is still missing for the next stage.
    """
    log_action("competitor_intelligence", "checking which requirements are established")
    findings = state.get("verified_findings", [])
    coverage = state.get("kb_coverage") or {}
    checks: List[Dict[str, Any]] = []
    for r in state.get("research_requirements", []):
        found = [f for f in findings if f.get("requirement_id") == r["id"]]
        reason = "No grounded finding yet."
        gap = coverage.get(r["id"]) or {}
        still = [i.get("what_is_missing") or i.get("item") for i in gap.get("items", []) if i.get("status") != "covered"]
        checks.append({
            "requirement_id": r["id"], "competitor": r["competitor"], "requirement": r["requirement"],
            "status": "fulfilled" if found else "missing",
            "evidence_found": f"{len(found)} grounded finding(s)" if found else "",
            "missing_reason": "" if found else reason,
            "missing": [] if found else still,
            "confidence": requirement_confidence(found, r["id"]),
            "evidence_quality": requirement_evidence_quality(found, r["id"]),
        })
    missing = [{k: c[k] for k in ("requirement_id", "competitor", "requirement", "missing_reason", "missing")}
               for c in checks if c["status"] != "fulfilled"]
    log_action("competitor_intelligence", f"{len(checks) - len(missing)} of {len(checks)} requirement(s) established")
    return {"requirement_checks": checks, "missing_information": missing}


# ============================================================
# NODE 7: PREPARE NEXT STAGE + ROUTER
# ============================================================

def prepare_next_stage_node(state: CompetitorState):
    next_stage = state.get("research_stage", 1) + 1
    log_action("competitor_intelligence", f"starting research stage {next_stage}")
    _banner(f"NODE: prepare_next_stage  STAGE {next_stage}")
    for item in state.get("missing_information", []):
        print(f'\n[{item["competitor"]}] {item["requirement_id"]}: {item["requirement"]}\n  Reason: {item.get("missing_reason", "")}')
    return {
        "research_stage": next_stage,
        "research_strategy": STAGE_STRATEGY.get(next_stage, "targeted_missing_requirements"),
        "search_queries": [],
        "new_findings": [],
        "new_sources_this_stage": 0,
    }


def research_router(state: CompetitorState) -> str:
    """Continue only while it is worth it: open requirements, stages and queries left, and progress."""
    stage = state.get("research_stage", 1)
    missing = state.get("missing_information", [])
    if not missing:
        return "finish"
    if stage >= MAX_RESEARCH_STAGES:
        log_action("competitor_intelligence", f"stopping after {stage} stages with {len(missing)} open requirement(s)")
        return "finish"
    if len(state.get("executed_queries", [])) >= MAX_QUERIES_TOTAL:
        log_action("competitor_intelligence", "stopping: the search budget for this run is used")
        return "finish"
    if stage > 1 and state.get("new_verified_this_stage", 0) < MIN_NEW_FINDINGS_TO_CONTINUE:
        log_action("competitor_intelligence", "stopping: the last stage added too little to justify another")
        return "finish"
    return "research_again"


# ============================================================
# NODE 8: FINAL REPORT
# ============================================================

def consolidate_competitor(state: CompetitorState, competitor: str) -> Dict[str, Any]:
    """One merged, evidence-grounded profile per competitor."""
    reqs = competitor_requirements(state, competitor)
    checks = {c["requirement_id"]: c for c in state.get("requirement_checks", [])}
    findings = [f for f in state.get("verified_findings", []) if f.get("competitor") == competitor]

    sources: Dict[str, Dict[str, str]] = {}
    for f in findings:
        for e in f.get("evidence", []):
            url = normalize_url(e.get("source_url", ""))
            if url and url not in sources:
                sources[url] = {"id": f"S{len(sources) + 1}", "title": e.get("source_title", "")}
    id_to_url = {v["id"]: u for u, v in sources.items()}

    payload = []
    for r in reqs:
        chk = checks.get(r["id"], {})
        rf = [f for f in findings if f.get("requirement_id") == r["id"]]
        payload.append({
            "requirement_id": r["id"], "requirement": r["requirement"],
            "status": chk.get("status", "missing"), "missing_reason": chk.get("missing_reason", ""),
            "confidence": requirement_confidence(rf, r["id"]), "evidence_quality": requirement_evidence_quality(rf, r["id"]),
            "findings": [{"finding": f["finding"], "confidence": normalize_confidence_value(f.get("confidence")),
                          "evidence_quality": f.get("evidence_quality", "none"),
                          "source_ids": [sources[normalize_url(e["source_url"])]["id"] for e in f.get("evidence", [])
                                         if normalize_url(e.get("source_url", "")) in sources]} for f in rf],
        })
    source_list = [{"id": v["id"], "url": u, "title": v["title"]} for u, v in sources.items()]

    profile = call_structured(consolidation_prompt(state, competitor, payload, source_list), CompetitorProfile) if findings else None
    by_id = {p["requirement_id"]: p for p in payload}
    requirements_out = []
    summaries = {c.requirement_id: c for c in (profile.requirements if profile else [])}
    for p in payload:
        c = summaries.get(p["requirement_id"])
        if c is not None and p["findings"]:
            summary, release = c.summary, c.release_status
            urls = [id_to_url[s] for s in c.evidence_source_ids if s in id_to_url]
        else:  # no model summary (or nothing found): say plainly what the evidence holds
            summary = (" ".join(f["finding"] for f in p["findings"][:2]) if p["findings"]
                       else "Not established by the verified evidence.")
            release = "not_stated"
            urls = [id_to_url[s] for f in p["findings"] for s in f["source_ids"] if s in id_to_url]
        requirements_out.append({**p, "summary": summary, "release_status": release,
                                 "evidence_urls": list(dict.fromkeys(urls))})
    overview = profile.overview if profile else (
        " ".join(f["finding"] for f in findings[:2]) if findings else f"No verified findings about {competitor}.")
    return {
        "competitor": competitor,
        "overview": overview,
        "requirements": requirements_out,
        "caveats": list(profile.caveats) if profile else [],
        "sources": source_list,
        "finding_count": len(findings),
        "requirements_fulfilled": sum(1 for p in by_id.values() if p["status"] == "fulfilled"),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [f"# Competitor research: {report.get('target_company', '')}", "",
             f"**Goal:** {report.get('research_goal', '')}", ""]
    for name, profile in (report.get("competitor_profiles") or {}).items():
        lines += [f"## {name}", "", profile.get("overview", ""), ""]
        for r in profile.get("requirements", []):
            lines += [f"### {r['requirement_id']}: {r['requirement']}", "", f"Status: {r['status']}", "", r.get("summary", ""), ""]
            lines += [f"- {u}" for u in r.get("evidence_urls", [])] + [""]
    open_items = report.get("remaining_unresolved_requirements") or []
    if open_items:
        lines += ["## Not established", ""] + [f"- {m['competitor']}: {m['requirement']}" for m in open_items]
    return "\n".join(lines)


@observe(type="node", name="competitor_intelligence.final_report")
def final_report_node(state: CompetitorState):
    span_input = {"research_stage": state.get("research_stage", 1),
                  "verified_finding_count": len(state.get("verified_findings", [])),
                  "source_count": len(state.get("search_results", [])),
                  "missing_information": state.get("missing_information", [])}
    update_current_span(input=span_input)
    log_action("competitor_intelligence", "writing the final report")
    _banner("NODE: final_report")

    profiles = {c: consolidate_competitor(state, c) for c in state.get("competitors", [])}
    kb_sources = [r for r in state.get("search_results", []) if r.get("from_knowledge_base")]
    final_report = {
        "agent": "competitor_intelligence",
        "target_company": state.get("company", ""),
        "research_goal": state.get("research_goal", ""),
        "competitors": state.get("competitors", []),
        "research_stages_completed": state.get("research_stage", 1),
        "queries_executed": len(state.get("executed_queries", [])),
        "total_sources": len(state.get("search_results", [])),
        "knowledge_base_sources": len(kb_sources),
        "total_verified_findings": len(state.get("verified_findings", [])),
        "dynamic_research_requirements": state.get("research_requirements", []),
        "knowledge_base_coverage": {rid: c.get("status") for rid, c in (state.get("kb_coverage") or {}).items()},
        "competitor_profiles": profiles,
        "remaining_unresolved_requirements": state.get("missing_information", []),
    }
    if INCLUDE_RAW_FINDINGS:
        raw: Dict[str, List[Dict[str, Any]]] = {c: [] for c in state.get("competitors", [])}
        for f in state.get("verified_findings", []):
            raw.setdefault(f["competitor"], []).append({
                "requirement_id": f.get("requirement_id"), "finding": f["finding"],
                "confidence": normalize_confidence_value(f.get("confidence")),
                "evidence_quality": f.get("evidence_quality", "none"),
                "evidence_urls": [e["source_url"] for e in f.get("evidence", [])],
            })
        final_report["findings_by_competitor"] = raw

    if _settings.EDRAK_DEBUG:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "final_report.md").write_text(render_markdown(final_report), encoding="utf-8")
    output = {"final_report": final_report}
    update_current_span(output=output)
    save_competitor_span("competitor_intelligence.final_report", input=span_input, output=output)
    return output


# ============================================================
# NODE 9 (optional): COMPARE THE TARGET COMPANY WITH THE COMPETITORS
# ============================================================

def comparison_node(state: CompetitorState):
    """Dimension-by-dimension comparison from the consolidated profiles. Not wired by default
    (it adds a model call); add it between final_report and END to include it."""
    log_action("competitor_intelligence", "building the comparison")
    _banner("NODE: comparison")
    report = state.get("final_report") or {}
    compact = {
        name: [{"requirement_id": r["requirement_id"], "requirement": r["requirement"], "status": r["status"],
                "summary": r.get("summary", ""), "release_status": r.get("release_status", "not_stated"),
                "evidence_quality": r.get("evidence_quality", "none")} for r in p.get("requirements", [])]
        for name, p in (report.get("competitor_profiles") or {}).items()
    }
    try:
        result = call_structured(comparison_prompt(state, compact), ComparisonReport)
        comparison = result.model_dump() if result else {"error": "no structured comparison returned"}
    except Exception as exc:  # noqa: BLE001 - the report stands without the comparison
        comparison = {"error": f"{type(exc).__name__}: {exc}"}
    return {"comparison": comparison}


# ============================================================
# WORKER RESULT
# ============================================================

_NEWS_HOSTS = ("reuters.com", "bloomberg.com", "theverge.com", "techcrunch.com", "cnbc.com", "ft.com",
               "wsj.com", "theregister.com", "zdnet.com", "venturebeat.com", "prnewswire.com", "businesswire.com")


def _source_type_for(url: str, official: bool) -> SourceType:
    """The shared SourceType for one evidence URL."""
    path = (url or "").lower()
    host = host_of(url)
    if official:
        if any(w in path for w in ("pricing", "/price", "/plans", "licens")):
            return SourceType.PRICING_PAGE
        if any(w in path for w in ("release-notes", "changelog", "whats-new")):
            return SourceType.RELEASE_NOTES
        if any(w in path for w in ("/news", "/press", "newsroom", "/blog")):
            return SourceType.ANNOUNCEMENT
        if any(w in path for w in ("/docs", "documentation", "techdocs", "/support", "/datasheet")) or host.startswith(("docs.", "techdocs.")):
            return SourceType.OFFICIAL_DOCUMENTATION
        return SourceType.WEB_PAGE
    if any(host == h or host.endswith("." + h) for h in _NEWS_HOSTS) or "/news" in path:
        return SourceType.NEWS_ARTICLE
    return SourceType.WEB_PAGE


def _numeric_confidence(value: Any) -> Optional[float]:
    if isinstance(value, str) and value.lower() in CONFIDENCE_RANK:
        return {0: 0.4, 1: 0.65, 2: 0.85}[CONFIDENCE_RANK[value.lower()]]
    return normalize_confidence_value(value)


_CATEGORY_ALIASES = {
    "pricing": FindingCategory.PRICING_PACKAGING, "packaging": FindingCategory.PRICING_PACKAGING,
    "product": FindingCategory.PRODUCT_FEATURE, "feature": FindingCategory.PRODUCT_FEATURE,
    "capabilit": FindingCategory.PRODUCT_FEATURE, "position": FindingCategory.POSITIONING,
    "customer": FindingCategory.TARGET_CUSTOMER, "market": FindingCategory.MARKET_SIGNAL,
    "risk": FindingCategory.RISK, "opportunit": FindingCategory.OPPORTUNITY,
    "strength": FindingCategory.STRENGTH, "gap": FindingCategory.GAP, "sentiment": FindingCategory.CUSTOMER_SENTIMENT,
}


def map_finding_category(category: str, competitive_area: str = "") -> FindingCategory:
    """The graph's category (or free-text competitive area) as the shared FindingCategory."""
    try:
        return FindingCategory(category)
    except ValueError:
        pass
    text = f"{category} {competitive_area}".lower()
    return next((v for k, v in _CATEGORY_ALIASES.items() if k in text), FindingCategory.OTHER)


def build_worker_result(state: CompetitorState) -> WorkerResult:
    """The shared WorkerResult: every finding references evidence that exists in this result."""
    company = state.get("company", "")
    search_results = state.get("search_results", [])
    report = state.get("final_report") or {}
    comparison = state.get("comparison") or {}
    info = state.get("competitor_info", {})

    evidence_for: Dict[str, Evidence] = {}
    worker_findings: List[Finding] = []
    for n, f in enumerate(state.get("verified_findings", [])):
        competitor = f.get("competitor", "")
        index = source_index_for(search_results, competitor)
        refs: List[EvidenceRef] = []
        for e in f.get("evidence", []):
            url = normalize_url(e.get("source_url", ""))
            if not url:
                continue
            # One evidence record per finding and page: extracted_fact is what THIS finding took from it.
            key = f"{n}|{competitor}|{url}"
            if key not in evidence_for:
                source = index.get(url, {})
                official = bool(e.get("is_official")) or is_official(url, competitor)
                evidence_for[key] = Evidence(
                    source_type=_source_type_for(url, official),
                    source_title=e.get("source_title") or source.get("title") or None,
                    source_url=url,
                    publisher=source.get("publisher") or host_of(url) or None,
                    extracted_fact=f["finding"],
                    excerpt=e.get("passage") or None,
                    page_text=source.get("text") or None,
                    metadata={
                        "competitor": competitor,
                        "official": official,
                        "from_knowledge_base": bool(source.get("from_knowledge_base")),
                        "published_date": source.get("published_date") or source.get("fetched_at"),
                        "provider": source.get("provider"),
                    },
                )
            refs.append(EvidenceRef(evidence_id=evidence_for[key].evidence_id, relation=EvidenceRelation.SUPPORTS))
        if not refs:
            continue  # the contract requires provenance; a finding with none is not reported
        worker_findings.append(Finding(
            statement=f"{competitor}: {f['finding']}" if competitor and not f["finding"].startswith(competitor) else f["finding"],
            category=map_finding_category(f.get("category", ""), f.get("competitive_area", "")),
            evidence_refs=refs,
            confidence=normalize_confidence_value(f.get("confidence")),
            limitations=[] if f.get("evidence_quality") != "third_party"
            else ["Supported by third-party sources only."],
        ))

    missing = state.get("missing_information", [])
    gaps = [f"{m['competitor']}: {m['requirement']}" + (f" ({m['missing_reason']})" if m.get("missing_reason") else "")
            for m in missing]
    dropped = state.get("dropped_sources", [])
    if dropped:
        gaps.append(f"{len(dropped)} search result(s) were set aside as off-topic, low-value or unreadable.")

    if not worker_findings:
        status = WorkerStatus.NO_EVIDENCE
    elif missing:
        status = WorkerStatus.PARTIAL
    else:
        status = WorkerStatus.COMPLETED

    return WorkerResult(
        task_id=state["task_id"],
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=status,
        attempt=state.get("attempt", 1),
        findings=worker_findings,
        evidence=list(evidence_for.values()),
        gaps=gaps,
        conflicts=[],
        confidence=calculate_overall_confidence(worker_findings),
        metadata={
            "company": company,
            "competitors": [
                {
                    "name": name,
                    "website": (info.get(name) or {}).get("website"),
                    "official_domains": official_domains(name),
                    "social_accounts": (info.get(name) or {}).get("links", []),
                    "discovered_by": (info.get(name) or {}).get("discovered_by", "user"),
                    "discovery_reason": (info.get(name) or {}).get("reason"),
                    "discovery_confidence": (info.get(name) or {}).get("confidence"),
                    "knowledge_base": ((state.get("kb_profiles") or {}).get(name) or {}).get("kb_key"),
                }
                for name in state.get("competitors", [])
            ],
            "research_stages_completed": state.get("research_stage", 1),
            "queries_executed": len(state.get("executed_queries", [])),
            "sources_read": len(search_results),
            "knowledge_base_sources": sum(1 for r in search_results if r.get("from_knowledge_base")),
            "knowledge_base_coverage": {rid: c.get("status") for rid, c in (state.get("kb_coverage") or {}).items()},
            "sources_set_aside": len(dropped),
            "requirement_checks": state.get("requirement_checks", []),
            "comparison_matrix": {
                "company": company,
                "executive_summary": comparison.get("executive_summary", ""),
                "dimensions": comparison.get("dimensions", []),
                "areas_where_target_appears_ahead": comparison.get("areas_where_target_appears_ahead", []),
                "areas_where_competitors_appear_ahead": comparison.get("areas_where_competitors_appear_ahead", []),
                "limitations": comparison.get("limitations", []),
            },
            "competitor_profiles": report.get("competitor_profiles", {}),
        },
    )
