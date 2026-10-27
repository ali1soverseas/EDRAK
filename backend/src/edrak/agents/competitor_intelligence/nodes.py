"""Graph nodes, helpers, routers and report rendering."""

import difflib
import json
import logging
import os
import pprint
import re
import requests
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse

from edrak.core.action_log import log_action
from edrak.core.llm import get_chat_model, schema_instruction
from dotenv import load_dotenv
from deepeval.tracing import observe, update_current_span
from tavily import TavilyClient

from edrak.contracts.result import (
    WorkerResult,
    WorkerStatus,
    Finding,
    FindingCategory,
    Conflict,
)
from edrak.contracts.evidence import (
    Evidence,
    EvidenceRef,
    EvidenceRelation,
    SourceType,
)
from edrak.contracts.task import WorkerType

from .prompts import (
    STAGE_SYNTH_GUIDANCE,
    comparison_prompt,
    consolidation_prompt,
    queries_prompt,
    requirements_prompt,
    synthesis_prompt,
)

from edrak.guardrails.content import sanitize as sanitize_text

from .sources import (
    best_passage,
    domain_of,
    is_official as _is_official_source,
    judge as judge_source,
    official_domains,
)
from .tracing import save_competitor_span

from .state import (
    CONFIDENCE_RANK,
    ComparisonReport,
    CompetitorProfile,
    CompetitorState,
    DUPLICATE_SIMILARITY,
    FindingList,
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
    ResearchRequirements,
    SOURCE_CHARS,
    STAGE_STRATEGY,
    SearchQueryList,
    comparison_dimensions,
)

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT
# ============================================================

# nodes.py -> competitor_intelligence -> agents -> edrak -> src -> backend -> repo root
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
load_dotenv(ENV_PATH)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


def _require(name: str, value: str | None) -> str:
    """Fail with the variable name when a credential is absent.

    Resolved per call rather than at import so a key can be rotated without
    restarting the process, and so an unconfigured competitor worker does not
    stop the other workers from loading.
    """
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


# ============================================================
# CLIENTS
# ============================================================

# Clients are built on first use so a rotated key is picked up without a
# restart, and so an unconfigured worker imports cleanly instead of raising
# while the module loads.
_tavily_client = None
_tavily_client_key = None
_llm = None
_llm_strong = None


def tavily():
    global _tavily_client, _tavily_client_key
    current_key = _tavily_key()
    if _tavily_client is None or _tavily_client_key != current_key:
        _tavily_client = TavilyClient(api_key=current_key)
        _tavily_client_key = current_key
    return _tavily_client


def llm():
    """The shared Ollama model, used for planning, queries and synthesis."""
    global _llm
    if _llm is None:
        _llm = get_chat_model(agent="competitor", temperature=0)
    return _llm


def llm_strong():
    """Same model as `llm`.

    This used to be a second, larger model for verification and completeness.
    There is now one model for the whole platform, so the two are kept only so
    existing call sites keep their intent visible.
    """
    return llm()


# ============================================================
# OUTPUT
# ============================================================

from edrak.core.config import settings as _settings

# Intermediate stage files are a debugging aid. They are written only with EDRAK_DEBUG on, and
# under artifacts/ (which is not committed), never inside the package.
OUTPUT_DIR = (
    _settings.ARTIFACTS_PATH if _settings.ARTIFACTS_PATH.is_absolute() else _settings.BASE_DIR / _settings.ARTIFACTS_PATH
) / "competitor_runs"


def save_node_output(
    node_name: str,
    output: Dict[str, Any],
    research_stage: int = 1,
):
    if not _settings.EDRAK_DEBUG:
        return
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    file_path = OUTPUT_DIR / f"stage_{research_stage}_{node_name}.json"

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            indent=4,
            ensure_ascii=False,
            default=str,
        )

    print(f"Saved node output -> {file_path}")


# ============================================================
# HELPERS
# ============================================================

def call_structured(
    prompt: str,
    model_class,
    strong: bool = False,
    retries: int = 1,
    raise_on_failure: bool = False,
):
    """
    Structured output via json_schema with the schema restated in the prompt.

    Was method="function_calling", which returned no structured output at all
    for every nested-list schema here, so requirement identification failed and
    the worker raised. Measured on ResearchRequirements: function_calling 0/5,
    json_schema with the instruction 4/5.

    Returns None if the model never produced a valid structured result.
    """

    model = llm_strong() if strong else llm()

    structured = model.with_structured_output(model_class, method="json_schema")
    messages = [prompt, schema_instruction(model_class)]
    last_error = None
    received_none = False

    for attempt in range(retries + 1):
        try:
            result = structured.invoke(messages)

            if result is not None:
                return result

            received_none = True
            print(
                f"WARNING: structured call returned None "
                f"(attempt {attempt + 1})"
            )

        except Exception as exc:
            last_error = exc
            print(
                f"WARNING: structured call failed "
                f"(attempt {attempt + 1}): {type(exc).__name__}: {exc}"
            )

    if received_none:
        try:
            response = model.invoke(messages)
            content = response.content if hasattr(response, "content") else response
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            text = str(content).strip()
            start = text.find("{")
            if start >= 0:
                payload, _ = json.JSONDecoder().raw_decode(text[start:])
                return model_class.model_validate(payload)
            last_error = ValueError("model reply contained no JSON object")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Plain-JSON recovery for %s failed: %s: %s",
                model_class.__name__,
                type(exc).__name__,
                exc,
            )

    if raise_on_failure:
        detail = (
            f"{type(last_error).__name__}: {last_error}"
            if last_error is not None
            else "model returned no structured result or JSON response"
        )
        raise RuntimeError(
            f"Structured output for {model_class.__name__} failed after "
            f"{retries + 1} attempts: {detail}"
        ) from last_error

    return None


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def host_matches(
    host: str,
    domains: List[str],
) -> bool:
    return any(
        host == d or host.endswith("." + d)
        for d in domains
    )


def result_text(
    result: Dict[str, Any],
    limit: int,
) -> str:
    content = (result.get("content") or "").strip()
    raw = (result.get("raw_content") or "").strip()

    text = content

    if raw and len(text) < limit:
        text = f"{text}\n{raw}" if text else raw

    return text[:limit]


def requirements_by_id(
    state: CompetitorState,
) -> Dict[str, Dict[str, str]]:
    return {
        r["id"]: r
        for r in state.get("research_requirements", [])
    }


def competitor_requirements(
    state: CompetitorState,
    competitor: str,
) -> List[Dict[str, str]]:
    return [
        r
        for r in state.get("research_requirements", [])
        if r["competitor"] == competitor
    ]


def source_index_for(
    results: List[Dict[str, Any]],
    competitor: str,
) -> Dict[str, Dict[str, Any]]:
    """
    url -> result, restricted to ONE competitor's results.
    """

    index = {}

    for r in results:
        if r.get("competitor") != competitor:
            continue

        url = normalize_url(r.get("url", ""))

        if url:
            index[url] = r

    return index


def canonical_url(url: str) -> str:
    u = (
        normalize_url(url)
        .lower()
        .split("#")[0]
        .split("?")[0]
    )

    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]

    if u.startswith("www."):
        u = u[4:]

    return u.rstrip("/")


def build_evidence(
    results: List[Dict[str, Any]],
):
    """
    Returns:

        evidence_text
        id_map

    The LLM cites short IDs such as S1, S2, etc.
    """

    blocks = []
    id_map = {}

    for r in results:
        text = (r.get("text") or "")[:SOURCE_CHARS]

        if not text:
            continue

        sid = f"S{len(id_map) + 1}"

        id_map[sid] = r

        blocks.append(
            f"SOURCE ID: {sid}\n"
            f"COMPETITOR: {r.get('competitor', '')}\n"
            f"SEARCH TYPE: {r.get('search_type', '')}\n"
            f"TITLE: {r.get('title', '')}\n"
            f"URL: {r.get('url', '')}\n"
            f"SOURCE TEXT:\n{text}"
        )

    return "\n\n----------------------\n\n".join(blocks), id_map


def chunk_results(
    results: List[Dict[str, Any]],
) -> List[List[Dict[str, Any]]]:
    """
    Split results into batches that fit MAX_EVIDENCE_CHARS.
    """

    batches = []
    current = []
    size = 0

    for r in results:
        cost = (
            min(
                len(r.get("text") or ""),
                SOURCE_CHARS,
            )
            + 300
        )

        if current and size + cost > MAX_EVIDENCE_CHARS:
            batches.append(current)
            current = []
            size = 0

        current.append(r)
        size += cost

    if current:
        batches.append(current)

    return batches


def resolve_source(
    ref: str,
    id_map: Dict[str, Dict[str, Any]],
):
    """
    Resolve an LLM reference:

        S3
        [S3]
        URL

    into a source result.
    """

    ref = str(ref).strip()

    m = re.fullmatch(
        r"\[?\s*(S\d+)\s*\]?",
        ref,
        flags=re.IGNORECASE,
    )

    if m:
        return id_map.get(
            m.group(1).upper()
        )

    wanted = canonical_url(ref)

    if wanted:
        for r in id_map.values():
            if canonical_url(
                r.get("url", "")
            ) == wanted:
                return r

    return None


def attach_evidence(
    findings: List[Dict[str, Any]],
    id_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convert LLM-cited source IDs into real evidence.
    """

    processed = []

    for finding in findings:
        refs = finding.pop(
            "evidence_source_ids",
            None,
        ) or []

        evidence = []
        seen = set()

        for ref in refs:
            source = resolve_source(
                ref,
                id_map,
            )

            if not source:
                continue

            url = normalize_url(
                source.get("url", "")
            )

            if not url:
                continue

            if url in seen:
                continue

            seen.add(url)

            evidence.append(
                {
                    "source_title": source.get(
                        "title",
                        "",
                    ),
                    "source_url": url,
                    "supporting_text": (
                        source.get("text") or ""
                    )[:SOURCE_CHARS],
                }
            )

            if len(evidence) >= MAX_EVIDENCE_PER_FINDING:
                break

        if refs and not evidence:
            print(
                f"DEBUG: could not resolve source refs "
                f"{refs} (valid: {list(id_map)})"
            )

        finding["evidence"] = evidence

        finding["evidence_urls"] = [
            e["source_url"]
            for e in evidence
        ]

        processed.append(finding)

    return processed


# ============================================================
# CONFIDENCE / EVIDENCE HELPERS
# ============================================================

def normalize_confidence_value(
    value: Any,
) -> float | None:
    """
    Normalize textual or numeric confidence into [0, 1].

    Accepted examples:

        "very high" -> 0.95
        "high"      -> 0.85
        "medium"    -> 0.65
        "low"       -> 0.35
        0.82        -> 0.82
        "82%"       -> 0.82
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        numeric = float(value)

        if numeric > 1.0 and numeric <= 100.0:
            numeric = numeric / 100.0

        if 0.0 <= numeric <= 1.0:
            return round(numeric, 3)

        return None

    text = str(value).strip().lower()

    if not text:
        return None

    mapping = {
        "very high": 0.95,
        "high": 0.85,
        "medium": 0.65,
        "moderate": 0.65,
        "low": 0.35,
        "very low": 0.15,
        "very_high": 0.95,
        "medium-high": 0.75,
        "medium high": 0.75,
        "medium-low": 0.50,
        "medium low": 0.50,
    }

    if text in mapping:
        return mapping[text]

    if text.endswith("%"):
        try:
            numeric = float(
                text[:-1].strip()
            ) / 100.0

            if 0.0 <= numeric <= 1.0:
                return round(numeric, 3)
        except ValueError:
            pass

    try:
        numeric = float(text)

        if numeric > 1.0 and numeric <= 100.0:
            numeric = numeric / 100.0

        if 0.0 <= numeric <= 1.0:
            return round(numeric, 3)
    except ValueError:
        pass

    return None


def source_evidence_score(
    source: Dict[str, Any],
    competitor: str,
) -> float:
    """
    Estimate the reliability of a source for the competitor research.

    IMPORTANT:

    Third-party sources are NOT treated as invalid.

    The score is based on:
        - whether the source is first-party
        - search quality
        - whether usable content exists
        - whether the source appears direct/relevant

    This is evidence quality, not source ownership.
    """

    url = normalize_url(
        source.get("url", "")
    )

    text = (
        source.get("text")
        or ""
    ).strip()

    title = (
        source.get("title")
        or ""
    ).lower()

    official = is_official(
        url,
        competitor,
    )

    if official:
        score = 0.85

        # Direct official sources such as product,
        # pricing, docs, release notes and announcements
        # are particularly useful.
        direct_keywords = (
            "pricing",
            "price",
            "documentation",
            "docs",
            "release",
            "announcement",
            "product",
            "features",
            "changelog",
            "plans",
        )

        if any(
            keyword in title
            for keyword in direct_keywords
        ):
            score += 0.05

    else:
        # Credible third-party evidence starts here.
        #
        # It is deliberately NOT assigned a low score
        # merely because it is third-party.
        score = 0.70

        tavily_score = source.get("score")

        if isinstance(
            tavily_score,
            (int, float),
        ):
            if tavily_score >= 0.80:
                score += 0.05
            elif tavily_score < 0.30:
                score -= 0.05

    if len(text) < 100:
        score -= 0.10
    elif len(text) >= 500:
        score += 0.02

    return round(
        max(
            0.0,
            min(
                score,
                0.98,
            ),
        ),
        3,
    )


def finding_evidence_quality(
    finding: Dict[str, Any],
    source_index: Dict[str, Dict[str, Any]],
) -> str:
    """
    Describe the ownership mix of the evidence.

    This does NOT say whether third-party evidence is valid.

    Values:
        first_party
        third_party
        mixed
        none
    """

    official_count = 0
    third_party_count = 0

    for evidence in finding.get(
        "evidence",
        [],
    ):
        url = normalize_url(
            evidence.get(
                "source_url",
                "",
            )
        )

        source = source_index.get(url)

        if not source:
            continue

        if is_official(
            url,
            finding.get(
                "competitor",
                "",
            ),
        ):
            official_count += 1
        else:
            third_party_count += 1

    if official_count and third_party_count:
        return "mixed"

    if official_count:
        return "first_party"

    if third_party_count:
        return "third_party"

    return "none"


def calculate_finding_confidence(
    finding: Dict[str, Any],
    source_index: Dict[str, Dict[str, Any]],
) -> float | None:
    """
    Calculate confidence from the actual evidence attached to a finding.

    Multiple independent supporting sources can increase confidence,
    but evidence quantity alone cannot make a weak finding highly
    confident.

    Third-party evidence is fully eligible for scoring.
    """

    competitor = finding.get(
        "competitor",
        "",
    )

    scores = []

    seen_urls = set()

    for evidence in finding.get(
        "evidence",
        [],
    ):
        url = normalize_url(
            evidence.get(
                "source_url",
                "",
            )
        )

        if not url or url in seen_urls:
            continue

        seen_urls.add(url)

        source = source_index.get(url)

        if not source:
            continue

        scores.append(
            source_evidence_score(
                source,
                competitor,
            )
        )

    if not scores:
        return None

    scores.sort(
        reverse=True
    )

    # Strongest source establishes the baseline.
    confidence = scores[0]

    # Additional independent sources provide modest
    # corroboration.
    if len(scores) >= 2:
        confidence += min(
            0.06,
            0.02 * (len(scores) - 1),
        )

    return round(
        min(
            confidence,
            0.98,
        ),
        3,
    )


def ensure_finding_confidence(
    finding: Dict[str, Any],
    source_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Ensure every verified finding has confidence and evidence quality.

    If the verifier schema already returned a confidence value,
    preserve it.

    Otherwise calculate confidence from the actual evidence.

    This is intentionally performed during verification rather
    than only when WorkerResult is created.
    """

    existing = normalize_confidence_value(
        finding.get("confidence")
    )

    calculated = calculate_finding_confidence(
        finding,
        source_index,
    )

    if existing is not None:
        confidence = existing

        # A verifier should never be allowed to claim
        # high confidence when the actual evidence is absent.
        if calculated is not None:
            confidence = min(
                existing,
                calculated,
            )

    else:
        confidence = calculated

    finding["confidence"] = confidence

    finding["evidence_quality"] = finding_evidence_quality(
        finding,
        source_index,
    )

    return finding


def requirement_confidence(
    findings: List[Dict[str, Any]],
    requirement_id: str,
) -> float | None:
    """
    Get the strongest evidence confidence for a requirement.
    """

    values = [
        normalize_confidence_value(
            f.get("confidence")
        )
        for f in findings
        if f.get("requirement_id")
        == requirement_id
    ]

    values = [
        v
        for v in values
        if v is not None
    ]

    if not values:
        return None

    return round(
        max(values),
        3,
    )


def requirement_evidence_quality(
    findings: List[Dict[str, Any]],
    requirement_id: str,
) -> str:
    """
    Determine evidence ownership mix for one requirement.
    """

    relevant = [
        f
        for f in findings
        if f.get("requirement_id")
        == requirement_id
    ]

    qualities = {
        f.get(
            "evidence_quality",
            "none",
        )
        for f in relevant
    }

    qualities.discard("none")

    if not qualities:
        return "none"

    if "mixed" in qualities:
        return "mixed"

    if (
        "first_party" in qualities
        and "third_party" in qualities
    ):
        return "mixed"

    if "first_party" in qualities:
        return "first_party"

    return "third_party"


def finding_view(
    finding: Dict[str, Any],
    text_limit: int = 400,
) -> Dict[str, Any]:
    """
    Compact finding for prompts.

    Includes confidence and evidence quality so downstream
    nodes do not lose verification information.
    """

    return {
        "competitor": finding.get(
            "competitor",
            "",
        ),
        "requirement_id": finding.get(
            "requirement_id",
            "",
        ),
        "finding": (
            finding.get("finding", "") or ""
        )[:text_limit],
        "confidence": normalize_confidence_value(
            finding.get("confidence")
        ),
        "evidence_quality": finding.get(
            "evidence_quality",
            "none",
        ),
        "evidence_urls": [
            e.get("source_url", "")
            for e in finding.get(
                "evidence",
                [],
            )
        ]
        or finding.get(
            "evidence_urls",
            [],
        ),
    }


def is_duplicate(
    new: Dict[str, Any],
    existing: List[Dict[str, Any]],
) -> bool:
    a = new["finding"].strip().lower()

    for old in existing:
        if old["competitor"] != new["competitor"]:
            continue

        if old.get("requirement_id") != new.get(
            "requirement_id"
        ):
            continue

        b = old["finding"].strip().lower()

        if (
            a == b
            or difflib.SequenceMatcher(
                None,
                a,
                b,
            ).ratio()
            >= DUPLICATE_SIMILARITY
        ):
            return True

    return False


def merge_findings(
    existing: List[Dict[str, Any]],
    new: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = list(existing)

    for f in new:
        if not is_duplicate(
            f,
            merged,
        ):
            merged.append(f)

    return merged


def is_official(
    url: str,
    competitor: str,
) -> bool:
    """The URL is on the competitor's own site (domains registered per run; see sources.py)."""
    return _is_official_source(url, competitor)


# ============================================================
# NODE 1: IDENTIFY RESEARCH REQUIREMENTS
# ============================================================

@observe(type="node", name="competitor_intelligence.identify_research_requirements")
def identify_research_requirements_node(
    state: CompetitorState,
):
    span_input = {
        "company": state.get("company"),
        "competitors": state.get("competitors", []),
        "business_context": state.get("business_context", {}),
        "metric_requirements": state.get("metric_requirements", []),
    }
    span_metadata = {"research_stage": state.get("research_stage", 1)}
    update_current_span(input=span_input, metadata=span_metadata)
    log_action("competitor_intelligence", "identifying research requirements")
    _banner("NODE: identify_requirements")

    company = state["company"]

    prompt = requirements_prompt(state)

    result = call_structured(
        prompt,
        ResearchRequirements,
        raise_on_failure=True,
    )

    if result is None:
        raise RuntimeError(
            "Requirement identification failed "
            "(no structured output)."
        )

    canonical = {
        c.lower(): c
        for c in state["competitors"]
    }

    requirements = []
    per_competitor: Dict[str, int] = {}
    seen_text = set()

    # The typed figures the planner asked for come first and take the budget before open exploration.
    for mr in state.get("metric_requirements") or []:
        competitor = canonical.get(str(mr.get("entity", "")).strip().lower())
        if not competitor or per_competitor.get(competitor, 0) >= MAX_REQUIREMENTS_PER_COMPETITOR:
            continue
        text = f"{mr.get('definition') or mr.get('metric_id')} ({mr.get('metric_id')})"
        per_competitor[competitor] = per_competitor.get(competitor, 0) + 1
        seen_text.add((competitor, text.lower()))
        requirements.append({
            "id": f"R{len(requirements) + 1}", "competitor": competitor, "requirement": text,
            "metric_id": mr.get("metric_id"), "required": bool(mr.get("required", True)),
        })

    for item in result.requirements:
        competitor = canonical.get(
            item.competitor.strip().lower()
        )

        if not competitor:
            print(
                "WARNING: dropping requirement for "
                f"unknown competitor: {item.competitor}"
            )
            continue

        # A bounded number of narrow requirements per competitor keeps the loop short.
        key = (competitor, item.requirement.strip().lower())
        if key in seen_text or per_competitor.get(competitor, 0) >= MAX_REQUIREMENTS_PER_COMPETITOR:
            continue
        seen_text.add(key)
        per_competitor[competitor] = per_competitor.get(competitor, 0) + 1

        requirements.append(
            {
                "id": f"R{len(requirements) + 1}",
                "competitor": competitor,
                "requirement": item.requirement.strip(),
            }
        )

    covered = {
        r["competitor"]
        for r in requirements
    }

    for competitor in state["competitors"]:
        if competitor not in covered:
            print(
                "WARNING: no requirements generated "
                f"for {competitor}"
            )

    if not requirements:
        raise RuntimeError(
            "No valid research requirements were generated."
        )

    print("\nDYNAMIC RESEARCH REQUIREMENTS:")

    pprint.pprint(
        requirements,
        sort_dicts=False,
    )

    # save_node_output(
    #     "research_requirements",
    #     {
    #         "research_requirements": requirements
    #     },
    #     state.get(
    #         "research_stage",
    #         1,
    #     ),
    # )

    output = {
        "research_requirements": requirements
    }
    update_current_span(output=output)
    save_competitor_span(
        "competitor_intelligence.identify_research_requirements",
        input=span_input,
        output=output,
        metadata=span_metadata,
    )
    return output


# ============================================================
# NODE 2: GENERATE SEARCH QUERIES
# ============================================================

@observe(type="node", name="competitor_intelligence.generate_queries")
def generate_queries_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "generating search queries")
    stage = state.get(
        "research_stage",
        1,
    )

    strategy = STAGE_STRATEGY.get(
        stage,
        "targeted_missing_requirements",
    )
    span_input = {
        "research_stage": stage,
        "research_strategy": strategy,
        "research_requirements": state.get("research_requirements", []),
        "missing_information": state.get("missing_information", []),
        "executed_query_count": len(state.get("executed_queries", [])),
    }
    span_metadata = {"research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)

    _banner(f"NODE: generate_queries  STAGE {stage}: {strategy.upper()}")

    reqs = requirements_by_id(state)

    if stage == 1:
        targets = [
            {
                "requirement_id": r["id"],
                "competitor": r["competitor"],
                "requirement": r["requirement"],
            }
            for r in reqs.values()
        ]

    else:
        targets = [
            {
                "requirement_id": m["requirement_id"],
                "competitor": m["competitor"],
                "requirement": m["requirement"],
                "missing_reason": m.get(
                    "missing_reason",
                    "",
                ),
            }
            for m in state.get(
                "missing_information",
                [],
            )
            if m["requirement_id"] in reqs
        ]

    if not targets:
        print("No targets for this stage.")

        output = {
            "search_queries": [],
            "research_strategy": strategy,
        }
        update_current_span(output=output)
        save_competitor_span(
            "competitor_intelligence.generate_queries",
            input=span_input,
            output=output,
            metadata=span_metadata,
        )
        return output

    target_competitors = {
        t["competitor"]
        for t in targets
    }

    known_findings = [
        finding_view(
            f,
            300,
        )
        for f in state.get(
            "verified_findings",
            [],
        )
        if f["competitor"] in target_competitors
    ][-60:]

    executed = state.get(
        "executed_queries",
        [],
    )

    previous_searches = [
        {
            "competitor": e["competitor"],
            "query": e["query"],
        }
        for e in executed
        if e["competitor"]
        in target_competitors
    ][-150:]

    info = state.get("competitor_info", {})

    def scope_of(c: str) -> str:
        details = info.get(c, {})
        domains = official_domains(c)
        parts = [f"the company {c}"]
        if domains:
            parts.append(f"official site {', '.join(domains)}")
        if details.get("reason"):
            parts.append(f"known for: {details['reason']}")
        return "; ".join(parts) + f". Only facts about {c}'s own products; do not credit it with a parent or partner company's products."

    scope_text = "\n".join(f"- {c}: {scope_of(c)}" for c in sorted(target_competitors))

    prompt = queries_prompt(
        state,
        stage,
        strategy,
        targets,
        known_findings,
        previous_searches,
        scope_text,
    )

    result = call_structured(
        prompt,
        SearchQueryList,
    )

    valid = {
        t["requirement_id"]: t
        for t in targets
    }

    executed_keys = {
        (
            e["competitor"].strip().lower(),
            e["query"].strip().lower(),
        )
        for e in executed
    }

    queries = []
    seen = set()
    per_req = {}
    # The whole run has a query budget; later stages get what is left.
    budget = max(0, MAX_QUERIES_TOTAL - len(executed))

    for q in (
        result.queries
        if result
        else []
    ):
        rid = q.requirement_id.strip()

        target = valid.get(rid)

        if not target:
            print(
                "WARNING: dropping query with unknown "
                f"requirement_id '{rid}': {q.query}"
            )
            continue

        competitor = target["competitor"]

        text, domain_hint = _query_without_site_operator(q.query.strip())

        key = (
            competitor.lower(),
            text.lower(),
        )

        if (
            not text
            or key in seen
            or key in executed_keys
        ):
            continue

        if (
            per_req.get(rid, 0)
            >= MAX_QUERIES_PER_REQUIREMENT
        ):
            continue

        if len(queries) >= budget:
            break

        seen.add(key)

        per_req[rid] = (
            per_req.get(rid, 0) + 1
        )

        queries.append(
            {
                "competitor": competitor,
                "requirement_id": rid,
                "requirement": target[
                    "requirement"
                ],
                "query": text,
                "domain_hint": domain_hint,
                "purpose": q.purpose,
                "search_type": strategy,
            }
        )

    print("\nGENERATED QUERIES:")

    for q in queries:
        print(
            f'\n[{q["competitor"]}] '
            f'{q["query"]}'
        )

        print(
            f'Requirement {q["requirement_id"]}: '
            f'{q["requirement"]}'
        )

        print(
            f'Purpose: {q["purpose"]}'
        )

    if not queries:
        print(
            "\nWARNING: no valid new queries "
            "survived validation."
        )

    # save_node_output(
    #     "generate_queries",
    #     {
    #         "research_stage": stage,
    #         "research_strategy": strategy,
    #         "search_queries": queries,
    #     },
    #     stage,
    # )

    output = {
        "search_queries": queries,
        "research_strategy": strategy,
    }
    update_current_span(output=output)
    save_competitor_span(
        "competitor_intelligence.generate_queries",
        input=span_input,
        output=output,
        metadata=span_metadata,
    )
    return output


def after_queries_router(
    state: CompetitorState,
):
    if state.get("search_queries"):
        return "search"

    print(
        "\nNo queries to run. "
        "Finishing research loop."
    )

    return "finish"


def _query_without_site_operator(query: str) -> tuple[str, list[str]]:
    """Move any site: operator out of the search text into a domain hint."""
    hinted = re.findall(r"(?:^|\s)site:([a-z0-9.-]+)", query, flags=re.IGNORECASE)
    clean_query = re.sub(
        r"(?:^|\s)site:[a-z0-9.-]+",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    clean_query = re.sub(r"\s+", " ", clean_query).strip()
    domains = sorted({domain_of(domain) for domain in hinted if domain_of(domain)})
    return clean_query, domains


def _search_domains(
    competitor: str,
    query: str,
    strategy: str,
    domain_hint: list[str] | None = None,
) -> list[str]:
    """Use registered official domains, or the planner's explicit site: scope."""
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
# NODE 3: TAVILY SEARCH
# ============================================================

@observe(
    type="tool",
    name="tavily_search",
    description="Search the web for competitor intelligence evidence."
)
def run_tavily(
    query: str,
    stage: int,
    domains: list[str] | None,
):
    params = dict(
        query=query,
        search_depth="advanced",
        max_results=MAX_RESULTS_PER_QUERY,
        include_raw_content=True,
        chunks_per_source=3,
    )

    if domains:
        params["include_domains"] = domains

    span_input = {
        "query": query,
        "stage": stage,
        "domains": domains,
    }
    span_metadata = {
        "stage": stage,
        "domain_restricted": bool(domains),
    }
    try:
        response = tavily().search(**params)
        results = response.get("results", [])
    except Exception as exc:
        save_competitor_span(
            "tavily_search",
            input=span_input,
            metadata=span_metadata,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    span_output = {
        "provider": "tavily",
        "result_count": len(results),
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "score": r.get("score"),
                "published_date": r.get("published_date"),
                "content": sanitize_text(
                    result_text(r, MAX_STORE_CHARS)
                )[0],
            }
            for r in results
        ],
    }
    update_current_span(
        input=span_input,
        output=span_output,
        metadata=span_metadata,
    )
    save_competitor_span(
        "tavily_search",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
    )

    return response


@observe(
    type="tool",
    name="serper_search",
    description="Fallback web search for competitor intelligence evidence.",
)
def run_serper(query: str, stage: int) -> tuple[dict[str, Any] | None, int | None]:
    span_input = {"query": query, "stage": stage}
    span_metadata = {"provider": "serper", "research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    if not serper_key:
        span_output = {"status": "skipped", "reason": "missing_api_key"}
        update_current_span(output=span_output)
        save_competitor_span(
            "serper_search",
            input=span_input,
            output=span_output,
            metadata=span_metadata,
        )
        return None, None

    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": serper_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": MAX_RESULTS_PER_QUERY},
            timeout=15,
        )
    except Exception as exc:
        save_competitor_span(
            "serper_search",
            input=span_input,
            metadata=span_metadata,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    if response.status_code != 200:
        span_output = {"status": response.status_code, "result_count": 0}
        update_current_span(output=span_output)
        save_competitor_span(
            "serper_search",
            input=span_input,
            output=span_output,
            metadata=span_metadata,
        )
        return None, response.status_code

    try:
        data = response.json()
    except Exception as exc:
        save_competitor_span(
            "serper_search",
            input=span_input,
            metadata=span_metadata,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    organic = data.get("organic", [])
    span_output = {
        "status": response.status_code,
        "result_count": len(organic),
        "results": [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "date": item.get("date"),
            }
            for item in organic
        ],
    }
    update_current_span(output=span_output)
    save_competitor_span(
        "serper_search",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
    )
    return data, response.status_code


@observe(
    type="node",
    name="search_node",
    description="Search for competitor intelligence sources using Tavily and Serper."
)
def search_node(state: CompetitorState):
    log_action("competitor_intelligence", "searching sources")

    stage = state.get("research_stage", 1)
    strategy = state.get("research_strategy", "discovery")
    span_input = {
        "research_stage": stage,
        "research_strategy": strategy,
        "search_queries": state.get("search_queries", []),
        "existing_source_count": len(state.get("search_results", [])),
    }
    span_metadata = {"research_stage": stage}
    update_current_span(input=span_input, metadata=span_metadata)

    _banner(f"NODE: search {strategy.upper()}")

    known = {
        (
            r["competitor"],
            normalize_url(r["url"]),
        ): r
        for r in state.get("search_results", [])
        if normalize_url(r.get("url", ""))
    }

    executed = list(state.get("executed_queries", []))
    added = 0

    for item in state.get("search_queries", []):
        query = item["query"]
        competitor = item["competitor"]

        print(f"\n[{competitor}] Query: {query}")

        try:
            domains = _search_domains(
                competitor,
                query,
                strategy,
                item.get("domain_hint"),
            )
            print(f"Domain-restricted search: {domains }")
            response = run_tavily(
                query,
                stage,
                domains or None,
            )

            tavily_results = response.get("results", [])
            if domains and not tavily_results:
                # Nothing on the official site for this query: search the open web instead.
                tavily_results = run_tavily(query, stage, None).get("results", [])

            print(f"Tavily results: {len(tavily_results)}")

            for result in tavily_results:
                url = normalize_url(result.get("url", ""))

                if not url:
                    continue

                key = (competitor, url)

                if key in known:
                    continue

                known[key] = {
                    "competitor": competitor,
                    "requirement_id": item.get(
                        "requirement_id", ""
                    ),
                    "query": query,
                    "purpose": item.get("purpose", ""),
                    "search_type": strategy,
                    "stage": stage,
                    "title": result.get("title", ""),
                    "url": url,
                    # Guardrail: page text is data, never instructions.
                    "text": sanitize_text(result_text(result, MAX_STORE_CHARS))[0],
                    "published_date": result.get(
                        "published_date"
                    ),
                    "score": result.get("score"),
                    "provider": "tavily",
                    "publisher": result.get("publisher"),
                }

                added += 1

        except Exception as e:
            print(
                f"Tavily error for '{query}': {e}. "
                "Attempting Serper fallback..."
            )

            try:
                serper_data, serper_status = run_serper(query, stage)

                if serper_status == 200 and serper_data is not None:
                    serper_items = serper_data.get("organic", [])

                    print(
                        "Serper fallback results: "
                        f"{len(serper_items)}"
                    )

                    for s_res in serper_items:
                        url = normalize_url(s_res.get("link", ""))

                        # Do not reject a source based only on its domain belonging to
                        # another configured competitor.
                        if not url:
                            continue

                        key = (competitor, url)

                        if key in known:
                            continue

                        known[key] = {
                            "competitor": competitor,
                            "requirement_id": item.get("requirement_id", ""),
                            "query": query,
                            "purpose": item.get("purpose", ""),
                            "search_type": strategy,
                            "stage": stage,
                            "title": s_res.get("title", ""),
                            "url": url,
                            "text": s_res.get("snippet", ""),
                            "published_date": s_res.get("date"),
                            "score": 0.8,
                            "provider": "serper",
                            "publisher": s_res.get("displayedLink", ""),
                        }

                        added += 1

                elif serper_status is not None:
                    print(
                        "Serper returned HTTP "
                        f"{serper_status}"
                    )
                else:
                    print(
                        "Serper fallback skipped: "
                        "SERPER_API_KEY is not configured."
                    )

            except Exception as serper_err:
                print(
                    f"Serper fallback error for '{query}': "
                    f"{serper_err}"
                )

        executed.append(
            {
                "competitor": competitor,
                "query": query,
                "stage": stage,
            }
        )

    # Keep only sources worth reading: about this competitor, with text, not a content farm.
    aliases_by = {
        name: (info.get("aliases") or [])
        for name, info in state.get("competitor_info", {}).items()
    }
    dropped = list(state.get("dropped_sources", []))
    for key, item in list(known.items()):
        if item.get("stage") != stage or item.get("judged"):
            continue
        keep, reason = judge_source(item, item["competitor"], aliases_by.get(item["competitor"], []))
        if keep:
            item["judged"] = True
            item["source_class"] = reason
        else:
            del known[key]
            added -= 1
            dropped.append({"competitor": item["competitor"], "url": item["url"], "reason": reason})
    log_action(
        "competitor_intelligence",
        f"kept {added} relevant source(s), set aside {len(dropped) - len(state.get('dropped_sources', []))}",
    )

    results = list(known.values())

    print(f"\nAdded {added} new results.")
    print(f"Total sources: {len(results)}")

    # save_node_output(
    #     "search_results",
    #     {
    #         "research_stage": stage,
    #         "research_strategy": strategy,
    #         "new_sources": added,
    #         "search_results": results,
    #     },
    #     stage,
    # )

    output = {
        "search_results": results,
        "executed_queries": executed,
        "new_sources_this_stage": max(0, added),
        "dropped_sources": dropped[-300:],
    }
    span_output = {
        "research_stage": stage,
        "new_source_count": max(0, added),
        "total_source_count": len(results),
        "executed_query_count": len(executed),
        "dropped_source_count": len(dropped),
        "providers": sorted({item.get("provider", "") for item in results}),
    }
    update_current_span(output=span_output)
    save_competitor_span(
        "competitor_intelligence.search",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
    )
    return output

# ============================================================
# NODE 4: SYNTHESIZE
# Per competitor, only THIS stage's sources
# ============================================================

def synthesize_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "summarizing findings from search results")
    stage = state.get(
        "research_stage",
        1,
    )

    _banner(f"NODE: synthesize  STAGE {stage}")

    guidance = STAGE_SYNTH_GUIDANCE.get(
        stage,
        "STAGE 4+ (gap filling): "
        "produce findings only for information "
        "newly supported by this evidence.",
    )

    previous_findings = state.get(
        "findings",
        [],
    )

    reqs = requirements_by_id(state)

    new_results = [
        r
        for r in state.get(
            "search_results",
            [],
        )
        if r.get("stage") == stage
    ]

    all_new = []

    for competitor in state["competitors"]:
        comp_results = [
            r
            for r in new_results
            if r["competitor"] == competitor
        ]

        comp_reqs = competitor_requirements(
            state,
            competitor,
        )

        if not comp_results or not comp_reqs:
            continue

        already = [
            finding_view(
                f,
                250,
            )
            for f in previous_findings
            if f["competitor"] == competitor
        ][-40:]

        for batch in chunk_results(
            comp_results
        ):
            evidence, id_map = build_evidence(
                batch
            )

            if not id_map:
                continue

            prompt = synthesis_prompt(
                state,
                competitor,
                guidance,
                comp_reqs,
                already,
                evidence,
            )

            result = call_structured(
                prompt,
                FindingList,
            )

            if result is None:
                print(
                    "WARNING: synthesis failed for "
                    f"{competitor} (batch skipped)"
                )
                continue

            batch_findings = []

            for f in result.findings:
                item = f.model_dump()

                item["competitor"] = competitor

                rid = (
                    item.get(
                        "requirement_id"
                    )
                    or ""
                ).strip()

                if (
                    rid not in reqs
                    or reqs[rid]["competitor"]
                    != competitor
                ):
                    print(
                        "WARNING: dropping finding "
                        f"with invalid requirement_id "
                        f"'{rid}'"
                    )
                    continue

                item["requirement_id"] = rid

                batch_findings.append(item)

            batch_findings = attach_evidence(
                batch_findings,
                id_map,
            )

            for f in batch_findings:
                if f.get("evidence"):
                    all_new.append(f)
                else:
                    print(
                        "WARNING: dropping finding "
                        "(no valid evidence attached): "
                        f"{f.get('finding', '')[:120]}"
                    )

    new_unique = merge_findings(
        [],
        all_new,
    )

    # Dedupe against what was VERIFIED, not against every earlier claim.
    # Comparing with previous_findings meant a claim the verifier rejected in
    # stage 1 could never be proposed again with better evidence in stage 2.
    verified_so_far = state.get(
        "verified_findings",
        [],
    )

    candidates = merge_findings(
        verified_so_far,
        new_unique,
    )

    truly_new = candidates[
        len(verified_so_far):
    ]

    findings = previous_findings + truly_new

    print(
        "Evidence-backed findings this stage: "
        f"{len(all_new)}"
    )

    print(
        f"New after dedup: {len(truly_new)}"
    )

    print(
        f"Accumulated findings: {len(findings)}"
    )

    # save_node_output(
    #     "findings",
    #     {
    #         "findings": findings,
    #         "new_findings_this_stage": truly_new,
    #     },
    #     stage,
    # )

    return {
        "findings": findings,
        "new_findings": truly_new,
    }


# ============================================================
# NODE 5: VERIFY
# Only NEW findings, grouped by competitor
# ============================================================

_STOPWORDS = {
    "that", "this", "with", "from", "have", "been", "were", "their", "about", "into", "over",
    "than", "such", "also", "only", "does", "each", "when", "which", "while", "where", "after",
    "before", "between", "without", "including", "across", "offers", "provides", "supports", "using",
}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def grounding_problem(statement: str, source_text: str) -> str | None:
    """Why a finding is not supported by the text it cites, or None when it is.

    Deterministic on purpose: the pipeline's verification stage does the careful review, so the
    worker only refuses what is plainly unsupported. Every number in the statement must appear in
    the sources, and most of its key words must too.
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
def verify_node(
    state: CompetitorState,
):
    """Keep the new findings that their cited sources support, and score their confidence.

    There is no second model pass here any more: the pipeline's verification stage reviews every
    finding from every worker, so a review inside this worker only doubled the cost and the time.
    """
    span_input = {
        "research_stage": state.get("research_stage", 1),
        "new_findings": state.get("new_findings", []),
        "previously_verified_count": len(state.get("verified_findings", [])),
        "search_result_count": len(state.get("search_results", [])),
    }
    span_metadata = {"research_stage": state.get("research_stage", 1)}
    update_current_span(input=span_input, metadata=span_metadata)
    log_action("competitor_intelligence", "checking each finding against the sources it cites")
    stage = state.get("research_stage", 1)
    _banner(f"NODE: ground  STAGE {stage}")

    previously_verified = state.get("verified_findings", [])
    accepted: List[Dict[str, Any]] = []
    rejected = 0
    for f in state.get("new_findings", []):
        competitor = f["competitor"]
        index = source_index_for(state.get("search_results", []), competitor)
        evidence = [
            e for e in f.get("evidence", [])
            if normalize_url(e.get("source_url", "")) in index
        ]
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
    log_action(
        "competitor_intelligence",
        f"{added} finding(s) grounded in their sources this stage, {rejected} set aside",
    )
    # save_node_output("verified_findings", {"verified_findings": verified, "new_this_stage": added}, stage)
    output = {"verified_findings": verified, "new_verified_this_stage": added}
    span_output = {
        "verified_findings": verified,
        "new_verified_this_stage": added,
        "rejected_finding_count": rejected,
    }
    update_current_span(output=span_output)
    save_competitor_span(
        "competitor_intelligence.verify",
        input=span_input,
        output=span_output,
        metadata=span_metadata,
    )
    return output


# ============================================================
# NODE 6: COMPLETENESS CHECK
# ============================================================

def check_research_requirements_node(
    state: CompetitorState,
):
    """Which requirements are established. Deterministic: met once a grounded finding maps to it.

    This used to be a model call that kept finding new \"missing sub-facts\", which is what kept the
    loop running for six stages. A requirement is now open only while nothing grounded covers it.
    """
    log_action("competitor_intelligence", "checking which requirements are still open")
    stage = state.get("research_stage", 1)
    _banner(f"NODE: check_requirements  STAGE {stage}")

    verified = state.get("verified_findings", [])
    checks = []
    from edrak.metrics.registry import get_registry

    for r in state.get("research_requirements", []):
        found = [
            f for f in verified
            if f.get("competitor") == r["competitor"] and f.get("requirement_id") == r["id"]
        ]
        reason = "No source found yet that establishes this."
        defn = get_registry().get(r.get("metric_id"))
        # A figure that must come from the company itself is not met by a third party's page.
        if found and defn and defn.check.get("official_source_required") and not any(
            is_official(e.get("source_url", ""), r["competitor"]) for f in found for e in (f.get("evidence") or [])
        ):
            found, reason = [], "Only third-party sources so far; this figure needs the company's own page."
        checks.append({
            "requirement_id": r["id"],
            "competitor": r["competitor"],
            "requirement": r["requirement"],
            "status": "fulfilled" if found else "missing",
            "evidence_found": f"{len(found)} grounded finding(s)" if found else "",
            "missing_reason": "" if found else reason,
            "confidence": requirement_confidence(found, r["id"]),
            "evidence_quality": requirement_evidence_quality(found, r["id"]),
        })
    missing = [
        {k: c[k] for k in ("requirement_id", "competitor", "requirement", "missing_reason")}
        for c in checks if c["status"] != "fulfilled"
    ]
    log_action(
        "competitor_intelligence",
        f"{len(checks) - len(missing)} of {len(checks)} requirement(s) established",
    )
    # save_node_output("requirement_check", {"checks": checks, "missing_information": missing}, stage)
    return {"requirement_checks": checks, "missing_information": missing}


# ============================================================
# NODE 7: PREPARE NEXT STAGE
# ============================================================

def prepare_next_stage_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "starting the next research stage")
    next_stage = (
        state.get(
            "research_stage",
            1,
        )
        + 1
    )

    _banner(f"NODE: prepare_next_stage  STAGE {next_stage}")

    for item in state.get(
        "missing_information",
        [],
    ):
        print(
            f'\n[{item["competitor"]}] '
            f'{item["requirement_id"]}: '
            f'{item["requirement"]}'
        )

        print(
            f'Reason: '
            f'{item.get("missing_reason", "")}'
        )

    return {
        "research_stage": next_stage
    }


# ============================================================
# ROUTER
# After completeness check
# ============================================================

def research_router(
    state: CompetitorState,
):
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

def consolidate_competitor(
    state: CompetitorState,
    competitor: str,
) -> Dict[str, Any]:
    """
    One merged, evidence-grounded profile per competitor.
    """

    reqs = competitor_requirements(
        state,
        competitor,
    )

    checks = {
        c["requirement_id"]: c
        for c in state.get(
            "requirement_checks",
            [],
        )
    }

    findings = [
        f
        for f in state.get(
            "verified_findings",
            [],
        )
        if f["competitor"] == competitor
    ]

    sources: Dict[
        str,
        Dict[str, str],
    ] = {}

    for f in findings:
        for e in f.get(
            "evidence",
            [],
        ):
            url = normalize_url(
                e.get(
                    "source_url",
                    "",
                )
            )

            if url and url not in sources:
                sources[url] = {
                    "id": (
                        f"S{len(sources) + 1}"
                    ),
                    "title": e.get(
                        "source_title",
                        "",
                    ),
                }

    id_to_url = {
        v["id"]: u
        for u, v in sources.items()
    }

    payload = []

    for r in reqs:
        chk = checks.get(
            r["id"],
            {},
        )

        requirement_findings = [
            f
            for f in findings
            if f.get(
                "requirement_id"
            ) == r["id"]
        ]

        req_confidence = requirement_confidence(
            requirement_findings,
            r["id"],
        )

        req_quality = requirement_evidence_quality(
            requirement_findings,
            r["id"],
        )

        payload.append(
            {
                "requirement_id": r["id"],
                "requirement": r[
                    "requirement"
                ],
                "status": chk.get(
                    "status",
                    "missing",
                ),
                "missing_reason": chk.get(
                    "missing_reason",
                    "",
                ),
                "confidence": req_confidence,
                "evidence_quality": req_quality,
                "findings": [
                    {
                        "finding": f[
                            "finding"
                        ],
                        "confidence": normalize_confidence_value(
                            f.get("confidence")
                        ),
                        "evidence_quality": f.get(
                            "evidence_quality",
                            "none",
                        ),
                        "source_ids": [
                            sources[
                                normalize_url(
                                    e[
                                        "source_url"
                                    ]
                                )
                            ]["id"]
                            for e in f.get(
                                "evidence",
                                [],
                            )
                            if normalize_url(
                                e.get(
                                    "source_url",
                                    "",
                                )
                            )
                            in sources
                        ],
                    }
                    for f in requirement_findings
                ],
            }
        )

    source_list = [
        {
            "id": v["id"],
            "title": v["title"],
            "url": u,
            "first_party": is_official(
                u,
                competitor,
            ),
        }
        for u, v in sources.items()
    ]

    profile = None

    if findings:
        prompt = consolidation_prompt(
            state,
            competitor,
            payload,
            source_list,
        )

        profile = call_structured(
            prompt,
            CompetitorProfile,
        )

    by_req = (
        {
            p.requirement_id: p
            for p in profile.requirements
        }
        if profile
        else {}
    )

    out_reqs = []

    caveats = (
        list(profile.caveats)
        if profile
        else []
    )

    for item in payload:
        rid = item[
            "requirement_id"
        ]

        p = by_req.get(rid)

        union_ids = sorted(
            {
                sid
                for f in item["findings"]
                for sid in f["source_ids"]
            }
        )

        if p and p.summary.strip():
            summary = p.summary.strip()
            release = p.release_status

            ids = [
                str(i)
                .strip()
                .upper()
                for i in p.evidence_source_ids
            ]

        else:
            summary = (
                " ".join(
                    f["finding"]
                    for f in item["findings"]
                )
                or "Not established by the "
                "verified evidence."
            )

            release = "not_stated"
            ids = union_ids

        urls = []

        for sid in ids:
            url = id_to_url.get(sid)

            if url and url not in urls:
                urls.append(url)

        if not urls:
            urls = [
                id_to_url[sid]
                for sid in union_ids
                if sid in id_to_url
            ]

        out_sources = [
            {
                "url": u,
                "title": sources[u]["title"],
                "first_party": is_official(
                    u,
                    competitor,
                ),
            }
            for u in urls
        ]

        if not out_sources:
            quality = "none"

        elif any(
            x["first_party"]
            for x in out_sources
        ) and any(
            not x["first_party"]
            for x in out_sources
        ):
            quality = "mixed"

        elif any(
            x["first_party"]
            for x in out_sources
        ):
            quality = "first_party"

        else:
            quality = "third_party_only"

            caveats.append(
                f"{rid}: supported only by "
                "third-party sources. These sources "
                "were accepted because they directly "
                "supported the research requirement."
            )

        # Use verified evidence confidence as the
        # authoritative requirement confidence.
        confidence = item.get(
            "confidence"
        )

        out_reqs.append(
            {
                "requirement_id": rid,
                "requirement": item[
                    "requirement"
                ],
                "status": item[
                    "status"
                ],
                "missing_reason": item[
                    "missing_reason"
                ],
                "release_status": release,
                "confidence": confidence,
                "evidence_quality": quality,
                "summary": summary,
                "sources": out_sources,
            }
        )

    if profile:
        overview = profile.overview.strip()

    elif findings:
        overview = (
            "Profile synthesis failed; "
            "see per-requirement summaries."
        )

    else:
        overview = (
            "No verified findings were "
            "established for this competitor."
        )

    return {
        "overview": overview,
        "requirements": out_reqs,
        "caveats": list(
            dict.fromkeys(caveats)
        ),
        "verified_findings_count": len(
            findings
        ),
    }


def render_comparison_lines(
    report: Dict[str, Any],
) -> List[str]:
    comp = report.get(
        "comparison"
    )

    if not comp:
        return []

    lines = [
        f"## Comparison: "
        f"{report['target_company']} "
        f"vs competitors",
        "",
    ]

    if comp.get("error"):
        return lines + [
            f"Comparison unavailable: "
            f"{comp['error']}",
            "",
        ]

    lines += [
        comp.get(
            "executive_summary",
            "",
        ),
        "",
    ]

    for d in comp.get(
        "dimensions",
        [],
    ):
        lines += [
            f"### {d['dimension']}",
            "",
            f"**{report['target_company']}:** "
            f"{d['target_position']}",
            "",
        ]

        for a in d["assessments"]:
            links = ", ".join(
                f"[source]({u})"
                for u in a.get(
                    "sources",
                    [],
                )[:3]
            )

            lines.append(
                f"- **{a['competitor']}** "
                f"({a['relative_to_target']}, "
                f"confidence: {a['confidence']}): "
                f"{a['summary']} "
                f"{links}".rstrip()
            )

        lines += [
            "",
            f"_Takeaway:_ "
            f"{d['takeaway']}",
            "",
        ]

    for title, key in (
        (
            f"Where {report['target_company']} "
            "appears ahead",
            "areas_where_target_appears_ahead",
        ),
        (
            "Where competitors appear ahead",
            "areas_where_competitors_appear_ahead",
        ),
        (
            "Limitations",
            "limitations",
        ),
    ):
        items = comp.get(
            key,
            [],
        )

        if items:
            lines.append(
                f"**{title}**"
            )

            lines += [
                f"- {i}"
                for i in items
            ]

            lines.append("")

    return lines


def render_markdown(
    report: Dict[str, Any],
) -> str:
    lines = [
        f"# Competitor intelligence: "
        f"{report['target_company']}",
        "",
        "## Research goal",
        report[
            "research_goal"
        ].strip(),
        "",
        f"Stages completed: "
        f"{report['research_stages_completed']} | "
        f"Queries: "
        f"{report['queries_executed']} | "
        f"Sources: "
        f"{report['total_sources']} | "
        f"Verified findings: "
        f"{report['total_verified_findings']}",
        "",
    ]

    lines += render_comparison_lines(
        report
    )

    for competitor, data in report[
        "competitor_profiles"
    ].items():
        lines += [
            f"## {competitor}",
            "",
            data["overview"],
            "",
        ]

        for r in data[
            "requirements"
        ]:
            confidence = r.get(
                "confidence"
            )

            confidence_text = (
                f"{confidence:.3f}"
                if isinstance(
                    confidence,
                    (int, float),
                )
                else "not established"
            )

            lines.append(
                f"**{r['requirement_id']}. "
                f"{r['requirement']}**  \n"
                f"_{r['status']} | "
                f"{r['release_status']} | "
                f"confidence: "
                f"{confidence_text} | "
                f"evidence: "
                f"{r['evidence_quality']}_"
            )

            lines += [
                "",
                r["summary"],
                "",
            ]

            if (
                r["status"] != "fulfilled"
                and r["missing_reason"]
            ):
                lines += [
                    f"Gap: "
                    f"{r['missing_reason']}",
                    "",
                ]

            for src in r[
                "sources"
            ]:
                tag = (
                    "first-party"
                    if src["first_party"]
                    else "third-party"
                )

                lines.append(
                    f"- "
                    f"[{src['title'] or src['url']}]"
                    f"({src['url']}) "
                    f"({tag})"
                )

            lines.append("")

        if data["caveats"]:
            lines.append(
                "**Caveats**"
            )

            lines += [
                f"- {c}"
                for c in data[
                    "caveats"
                ]
            ]

            lines.append("")

    unresolved = report.get(
        "remaining_unresolved_requirements",
        [],
    )

    if unresolved:
        lines += [
            "## Unresolved requirements",
            "",
        ]

        for m in unresolved:
            lines.append(
                f"- [{m['competitor']}] "
                f"{m['requirement_id']}: "
                f"{m['missing_reason']}"
            )

        lines.append("")

    return "\n".join(lines)


@observe(type="node", name="competitor_intelligence.final_report")
def final_report_node(
    state: CompetitorState,
):
    span_input = {
        "company": state.get("company"),
        "research_goal": state.get("research_goal"),
        "competitors": state.get("competitors", []),
        "research_stage": state.get("research_stage", 1),
        "verified_finding_count": len(state.get("verified_findings", [])),
        "source_count": len(state.get("search_results", [])),
        "missing_information": state.get("missing_information", []),
    }
    update_current_span(input=span_input)
    log_action("competitor_intelligence", "writing the final report")
    _banner("NODE: final_report")

    profiles = {}

    for competitor in state[
        "competitors"
    ]:
        print(
            f"Consolidating profile: "
            f"{competitor}"
        )

        profiles[competitor] = (
            consolidate_competitor(
                state,
                competitor,
            )
        )

    final_report = {
        "agent": "competitor_intelligence",
        "target_company": state[
            "company"
        ],
        "research_goal": state[
            "research_goal"
        ],
        "competitors": state[
            "competitors"
        ],
        "research_stages_completed": state.get(
            "research_stage",
            1,
        ),
        "queries_executed": len(
            state.get(
                "executed_queries",
                [],
            )
        ),
        "total_sources": len(
            state.get(
                "search_results",
                [],
            )
        ),
        "total_verified_findings": len(
            state.get(
                "verified_findings",
                [],
            )
        ),
        "dynamic_research_requirements": state.get(
            "research_requirements",
            [],
        ),
        "competitor_profiles": profiles,
        "remaining_unresolved_requirements": state.get(
            "missing_information",
            [],
        ),
    }

    if INCLUDE_RAW_FINDINGS:
        raw = {
            c: []
            for c in state[
                "competitors"
            ]
        }

        for f in state.get(
            "verified_findings",
            [],
        ):
            raw.setdefault(
                f["competitor"],
                [],
            ).append(
                {
                    "requirement_id": f.get(
                        "requirement_id"
                    ),
                    "finding": f[
                        "finding"
                    ],
                    "confidence": normalize_confidence_value(
                        f.get("confidence")
                    ),
                    "evidence_quality": f.get(
                        "evidence_quality",
                        "none",
                    ),
                    "business_relevance": f.get(
                        "business_relevance",
                        "",
                    ),
                    "evidence_urls": [
                        e["source_url"]
                        for e in f.get(
                            "evidence",
                            [],
                        )
                    ],
                }
            )

        final_report[
            "findings_by_competitor"
        ] = raw

    output = {
        "final_report": final_report
    }

    # save_node_output(
    #     "final_report",
    #     output,
    #     state.get(
    #         "research_stage",
    #         1,
    #     ),
    # )

    # md_path = OUTPUT_DIR / "final_report.md"
    # if _settings.EDRAK_DEBUG:
    #     OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    #     md_path.write_text(render_markdown(final_report), encoding="utf-8")

    # print(
    #     f"Saved markdown report -> "
    #     f"{md_path}"
    # )

    update_current_span(output=output)
    save_competitor_span(
        "competitor_intelligence.final_report",
        input=span_input,
        output=output,
    )
    return output


# ============================================================
# NODE 9: COMPARE TARGET COMPANY WITH COMPETITORS
# ============================================================

# def comparison_node(
#     state: CompetitorState,
# ):
#     log_action("competitor_intelligence", "building the comparison")
#     _banner("NODE: comparison")

#     report = state[
#         "final_report"
#     ]

#     company = state[
#         "company"
#     ]

#     competitors = state[
#         "competitors"
#     ]

#     profiles = report.get(
#         "competitor_profiles",
#         {},
#     )

#     compact = {}
#     req_index = {}

#     for c, prof in profiles.items():
#         compact[c] = {
#             "overview": prof[
#                 "overview"
#             ],
#             "requirements": [
#                 {
#                     k: r[k]
#                     for k in (
#                         "requirement_id",
#                         "requirement",
#                         "status",
#                         "release_status",
#                         "confidence",
#                         "evidence_quality",
#                         "summary",
#                     )
#                 }
#                 for r in prof[
#                     "requirements"
#                 ]
#             ],
#             "caveats": prof[
#                 "caveats"
#             ],
#         }

#         for r in prof[
#             "requirements"
#         ]:
#             req_index[
#                 (
#                     c,
#                     r["requirement_id"],
#                 )
#             ] = r

#     prompt = comparison_prompt(
#         state,
#         compact,
#     )

#     result = call_structured(
#         prompt,
#         ComparisonReport,
#         strong=True,
#     )

#     if result is None:
#         report["comparison"] = {
#             "error": (
#                 "comparison call failed "
#                 "(no structured output)"
#             )
#         }

#         output = {
#             "final_report": report
#         }

#         save_node_output(
#             "final_report",
#             output,
#             state.get(
#                 "research_stage",
#                 1,
#             ),
#         )

#         if _settings.EDRAK_DEBUG:
#             OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
#             (OUTPUT_DIR / "final_report.md").write_text(render_markdown(report), encoding="utf-8")

#         return output

#     canonical = {
#         c.lower(): c
#         for c in competitors
#     }

#     dims = []

#     for d in result.dimensions:
#         assessments: Dict[
#             str,
#             Dict[str, Any],
#         ] = {}

#         for a in d.assessments:
#             comp = canonical.get(
#                 a.competitor.strip().lower()
#             )

#             if not comp or comp in assessments:
#                 continue

#             rids = [
#                 rid
#                 for rid in dict.fromkeys(
#                     x.strip()
#                     for x in a.requirement_ids
#                 )
#                 if (
#                     comp,
#                     rid,
#                 ) in req_index
#             ]

#             cited = [
#                 req_index[
#                     (
#                         comp,
#                         rid,
#                     )
#                 ]
#                 for rid in rids
#             ]

#             relative = (
#                 a.relative_to_target
#             )

#             # ------------------------------------------------
#             # Evidence-grounded comparison confidence
#             # ------------------------------------------------

#             llm_confidence = normalize_confidence_value(
#                 a.confidence
#             )

#             cited_confidences = [
#                 normalize_confidence_value(
#                     r.get("confidence")
#                 )
#                 for r in cited
#             ]

#             cited_confidences = [
#                 value
#                 for value in cited_confidences
#                 if value is not None
#             ]

#             has_evidence = any(
#                 r.get("evidence_quality")
#                 != "none"
#                 for r in cited
#             )

#             if not cited or not has_evidence:
#                 relative = "not_established"
#                 confidence = "low"

#             else:
#                 # The comparison cannot be more confident
#                 # than the evidence underneath it.
#                 #
#                 # Example:
#                 # LLM says "high" = 0.85
#                 # underlying evidence = 0.72
#                 # final = 0.72
#                 #
#                 # Third-party evidence is NOT discounted here.
#                 # Its confidence was already determined during
#                 # verification.
#                 evidence_confidence = min(
#                     cited_confidences
#                 ) if cited_confidences else None

#                 if (
#                     llm_confidence is not None
#                     and evidence_confidence is not None
#                 ):
#                     confidence_value = min(
#                         llm_confidence,
#                         evidence_confidence,
#                     )

#                 elif evidence_confidence is not None:
#                     confidence_value = (
#                         evidence_confidence
#                     )

#                 else:
#                     confidence_value = (
#                         llm_confidence
#                     )

#                 if confidence_value is None:
#                     relative = "not_established"
#                     confidence = "low"
#                 else:
#                     confidence = round(
#                         confidence_value,
#                         3,
#                     )

#             urls = []

#             for r in cited:
#                 for src in r[
#                     "sources"
#                 ]:
#                     if src["url"] not in urls:
#                         urls.append(
#                             src["url"]
#                         )

#             assessments[comp] = {
#                 "competitor": comp,
#                 "relative_to_target": relative,
#                 "confidence": confidence,
#                 "summary": a.summary.strip(),
#                 "requirement_ids": rids,
#                 "sources": urls,
#             }

#         for comp in competitors:
#             if comp not in assessments:
#                 assessments[comp] = {
#                     "competitor": comp,
#                     "relative_to_target": (
#                         "not_established"
#                     ),
#                     "confidence": "low",
#                     "summary": (
#                         "Not assessed for "
#                         "this dimension."
#                     ),
#                     "requirement_ids": [],
#                     "sources": [],
#                 }

#         dims.append(
#             {
#                 "dimension": d.dimension,
#                 "target_position": (
#                     d.target_position.strip()
#                 ),
#                 "assessments": [
#                     assessments[c]
#                     for c in competitors
#                 ],
#                 "takeaway": d.takeaway.strip(),
#             }
#         )

#     limitations = list(
#         result.limitations
#     )

#     third_party_only = sum(
#         1
#         for (_, _), r in req_index.items()
#         if r["evidence_quality"]
#         == "third_party_only"
#     )

#     if third_party_only:
#         limitations.append(
#             f"{third_party_only} competitor "
#             "requirement(s) were supported by "
#             "third-party sources. These sources "
#             "were accepted because the evidence "
#             "was verified as relevant to the "
#             "research requirement."
#         )

#     unresolved = len(
#         report.get(
#             "remaining_unresolved_requirements",
#             [],
#         )
#     )

#     if unresolved:
#         limitations.append(
#             f"{unresolved} requirement(s) "
#             "remained unresolved after research."
#         )

#     report["comparison"] = {
#         "executive_summary": (
#             result.executive_summary.strip()
#         ),
#         "dimensions": dims,
#         "areas_where_target_appears_ahead": (
#             result.areas_where_target_appears_ahead
#         ),
#         "areas_where_competitors_appear_ahead": (
#             result.areas_where_competitors_appear_ahead
#         ),
#         "limitations": list(
#             dict.fromkeys(
#                 limitations
#             )
#         ),
#     }

#     print(
#         f"Comparison built: "
#         f"{len(dims)} dimensions x "
#         f"{len(competitors)} competitors"
#     )

#     output = {
#         "final_report": report
#     }

#     save_node_output(
#         "final_report",
#         output,
#         state.get(
#             "research_stage",
#             1,
#         ),
#     )

#     if _settings.EDRAK_DEBUG:
#         OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
#         (OUTPUT_DIR / "final_report.md").write_text(render_markdown(report), encoding="utf-8")

#     return output


# ============================================================
# WORKER RESULT ADAPTER
# ============================================================

def _source_type_for(url: str, official: bool) -> SourceType:
    path = (urlparse(url).path or "").lower()
    if official:
        if "pric" in path or "plans" in path:
            return SourceType.PRICING_PAGE
        if any(k in path for k in ("release", "changelog", "whats-new", "what-s-new", "updates")):
            return SourceType.RELEASE_NOTES
        if any(k in path for k in ("blog", "news", "press", "announc")):
            return SourceType.ANNOUNCEMENT
        return SourceType.OFFICIAL_DOCUMENTATION
    if any(k in path for k in ("/news", "/article", "/story", "/press")):
        return SourceType.NEWS_ARTICLE
    return SourceType.WEB_PAGE


def _numeric_confidence(value: Any) -> float | None:
    if isinstance(value, str):
        return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(value.lower())
    return normalize_confidence_value(value)


def build_worker_result(
    state: CompetitorState,
) -> WorkerResult:
    """The shared WorkerResult: grounded findings, the sources they cite, and the comparison.

    Only cited sources become evidence, and each one's fact is the passage that supports the
    finding, not the whole page. The comparison with the user's company is returned twice: as
    positioning findings (so verification checks them like any other claim) and as a structured
    matrix in metadata, which the brief's "Alternatives compared" card draws.
    """
    verified_findings = state.get("verified_findings", [])
    search_results = state.get("search_results", [])
    unresolved = state.get("missing_information", [])
    company = state.get("company", "")
    report = state.get("final_report", {}) or {}

    by_key = {
        (r.get("competitor", ""), normalize_url(r.get("url", ""))): r
        for r in search_results
        if normalize_url(r.get("url", ""))
    }
    evidence_for: Dict[tuple, Evidence] = {}

    def evidence_item(competitor: str, url: str, statement: str) -> Evidence | None:
        key = (competitor, normalize_url(url))
        if key in evidence_for:
            return evidence_for[key]
        source = by_key.get(key)
        if not source:
            return None
        text = source.get("text") or ""
        official = is_official(key[1], competitor)
        fact = best_passage(text, statement) or source.get("title") or "Source retrieved by the competitor worker."
        evidence_for[key] = Evidence(
            source_type=_source_type_for(key[1], official),
            source_title=source.get("title") or None,
            source_url=key[1],
            publisher=source.get("publisher") or host_of(key[1]) or None,
            extracted_fact=fact,
            excerpt=text[:1500] or None,
            page_text=text or None,
            metadata={
                "first_party": official,
                "competitor": competitor,
                "requirement_id": source.get("requirement_id", ""),
                "query": source.get("query", ""),
                "stage": source.get("stage", 1),
                "provider": source.get("provider", ""),
                "published_date": source.get("published_date"),
                "score": source.get("score"),
            },
        )
        return evidence_for[key]

    worker_findings: List[Finding] = []
    for raw in verified_findings:
        statement = (raw.get("finding") or "").strip()
        competitor = raw.get("competitor", "")
        if not statement:
            continue
        if competitor and competitor.lower() not in statement.lower():
            statement = f"{competitor}: {statement}"
        refs = []
        for e in raw.get("evidence", []):
            ev = evidence_item(competitor, e.get("source_url", ""), statement)
            if ev and ev.evidence_id not in {r.evidence_id for r in refs}:
                refs.append(EvidenceRef(evidence_id=ev.evidence_id, relation=EvidenceRelation.SUPPORTS))
        if not refs:
            continue
        try:
            category = FindingCategory(raw.get("category") or "other")
        except ValueError:
            category = FindingCategory.OTHER
        if category is FindingCategory.OTHER:
            category = map_finding_category(raw.get("competitive_area", ""))
        confidence = normalize_confidence_value(raw.get("confidence"))
        if confidence is None:
            confidence = calculate_finding_confidence(raw, source_index_for(search_results, competitor))
        worker_findings.append(
            Finding(statement=statement, category=category, evidence_refs=refs, confidence=confidence)
        )

    # The comparison: one positioning finding per established cell, plus the matrix itself.
    comparison = report.get("comparison") or {}
    matrix_dims = []
    for dim in comparison.get("dimensions", []):
        cells = []
        for a in dim.get("assessments", []):
            competitor = a.get("competitor", "")
            ids = []
            for url in a.get("sources", []):
                ev = evidence_item(competitor, url, a.get("summary", ""))
                if ev and ev.evidence_id not in ids:
                    ids.append(ev.evidence_id)
            relative = a.get("relative_to_target", "not_established")
            cells.append({
                "competitor": competitor,
                "relative_to_target": relative,
                "summary": a.get("summary", ""),
                "confidence": _numeric_confidence(a.get("confidence")),
                "evidence_ids": ids,
            })
            if relative != "not_established" and ids and a.get("summary"):
                worker_findings.append(
                    Finding(
                        statement=f"{dim.get('dimension')}: compared with {company}, {competitor} is "
                        f"{relative.replace('_', ' ')}. {a['summary'].strip()}",
                        category=FindingCategory.POSITIONING,
                        evidence_refs=[EvidenceRef(evidence_id=i) for i in ids[:MAX_EVIDENCE_PER_FINDING]],
                        confidence=_numeric_confidence(a.get("confidence")),
                        limitations=["Compares documented facts only; absence of evidence is not absence of a capability."],
                    )
                )
        matrix_dims.append({
            "dimension": dim.get("dimension", ""),
            "target_position": dim.get("target_position", ""),
            "takeaway": dim.get("takeaway", ""),
            "cells": cells,
        })

    if worker_findings:
        status = WorkerStatus.PARTIAL if unresolved else WorkerStatus.COMPLETED
    else:
        status = WorkerStatus.NO_EVIDENCE

    gaps = []
    for item in unresolved:
        gaps.append(f"[{item.get('competitor', '')}] {item.get('requirement', '')}")
    if not worker_findings and state.get("dropped_sources"):
        gaps.append(
            f"{len(state['dropped_sources'])} search result(s) were set aside as off-topic or low quality, "
            "and no usable source remained."
        )

    info = state.get("competitor_info", {})
    output = WorkerResult(
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
                }
                for name in state.get("competitors", [])
            ],
            "research_stages_completed": state.get("research_stage", 1),
            "queries_executed": len(state.get("executed_queries", [])),
            "sources_read": len(search_results),
            "sources_set_aside": len(state.get("dropped_sources", [])),
            "requirement_checks": state.get("requirement_checks", []),
            "comparison_matrix": {
                "company": company,
                "executive_summary": comparison.get("executive_summary", ""),
                "dimensions": matrix_dims,
                "areas_where_target_appears_ahead": comparison.get("areas_where_target_appears_ahead", []),
                "areas_where_competitors_appear_ahead": comparison.get("areas_where_competitors_appear_ahead", []),
                "limitations": comparison.get("limitations", []),
            },
            "competitor_profiles": report.get("competitor_profiles", {}),
        },
    )
    save_node_output("worker_result", output.model_dump(mode="json"), state.get("research_stage", 1))
    return output


# ============================================================
# CATEGORY MAPPING
# ============================================================

def map_finding_category(
    competitive_area: str,
) -> FindingCategory:
    """
    Map the competitor graph's competitive area
    into the shared FindingCategory enum.
    """

    value = (
        competitive_area
        or ""
    ).strip().lower()

    if "pricing" in value or "price" in value:
        return FindingCategory.PRICING_PACKAGING

    if (
        "feature" in value
        or "capability" in value
        or "product" in value
    ):
        return FindingCategory.PRODUCT_FEATURE

    if "position" in value:
        return FindingCategory.POSITIONING

    if (
        "customer" in value
        or "target" in value
    ):
        return FindingCategory.TARGET_CUSTOMER

    if "strength" in value:
        return FindingCategory.STRENGTH

    if (
        "gap" in value
        or "weakness" in value
    ):
        return FindingCategory.GAP

    if "risk" in value:
        return FindingCategory.RISK

    if "opportunity" in value:
        return FindingCategory.OPPORTUNITY

    if (
        "sentiment" in value
        or "review" in value
    ):
        return FindingCategory.CUSTOMER_SENTIMENT

    return FindingCategory.OTHER


# ============================================================
# CONFIDENCE MAPPING
# ============================================================

def map_confidence(
    value: Any,
) -> float | None:
    """
    Backwards-compatible wrapper around the new
    confidence normalization helper.
    """

    return normalize_confidence_value(
        value
    )


def calculate_overall_confidence(
    findings: List[Finding],
) -> float | None:
    """
    Calculate overall worker confidence from
    findings that actually report confidence.
    """

    values = [
        normalize_confidence_value(
            f.confidence
        )
        for f in findings
    ]

    values = [
        value
        for value in values
        if value is not None
    ]

    if not values:
        return None

    return round(
        sum(values) / len(values),
        3,
    )