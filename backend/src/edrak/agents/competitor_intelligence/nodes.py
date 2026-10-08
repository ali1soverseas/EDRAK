"""Graph nodes, helpers, routers and report rendering."""

import difflib
import json
import os
import pprint
import re
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse

from edrak.core.action_log import log_action
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
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
    check_prompt,
    comparison_prompt,
    consolidation_prompt,
    queries_prompt,
    requirements_prompt,
    synthesis_prompt,
    verification_prompt,
)

from .state import (
    BatchVerification,
    COMPETITOR_SCOPE,
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
    MAX_RESEARCH_STAGES,
    MAX_RESULTS_PER_QUERY,
    MAX_STORE_CHARS,
    OFFICIAL_DOMAINS,
    REQUIRE_OFFICIAL_SOURCE,
    RequirementCheckList,
    ResearchRequirements,
    SOURCE_CHARS,
    STAGE_STRATEGY,
    SearchQueryList,
    VERIFY_BATCH_SIZE,
)


# ============================================================
# ENVIRONMENT
# ============================================================

# nodes.py -> competitor_intelligence -> agents -> edrak -> src -> backend -> repo root
ENV_PATH = Path(__file__).resolve().parents[5] / ".env"
load_dotenv(ENV_PATH)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is missing")

if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY is missing")


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

tavily = TavilyClient(api_key=TAVILY_API_KEY)

# Cheap model: planning, queries, synthesis
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
    max_retries=4,
)

# Stronger model: verification and completeness
llm_strong = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    api_key=OPENAI_API_KEY,
    max_retries=4,
)


# ============================================================
# OUTPUT
# ============================================================

COMPETITOR_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = COMPETITOR_DIR / "outputs"


def save_node_output(
    node_name: str,
    output: Dict[str, Any],
    research_stage: int = 1,
):
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
):
    """
    Function-calling structured output.

    Returns None if the model never produced a valid structured result.
    """

    model = llm_strong if strong else llm

    structured = model.with_structured_output(
        model_class,
        method="function_calling",
    )

    for attempt in range(retries + 1):
        try:
            result = structured.invoke(prompt)

            if result is not None:
                return result

            print(
                f"WARNING: structured call returned None "
                f"(attempt {attempt + 1})"
            )

        except Exception as e:
            print(
                f"WARNING: structured call failed "
                f"(attempt {attempt + 1}): {e}"
            )

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


def belongs_to_other_competitor(
    url: str,
    competitor: str,
) -> bool:
    """
    True if the URL is on another competitor's official domain.
    """

    own = OFFICIAL_DOMAINS.get(competitor, [])
    host = host_of(url)

    if host_matches(host, own):
        return False

    for other, domains in OFFICIAL_DOMAINS.items():
        if other != competitor and host_matches(host, domains):
            return True

    return False


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
    return host_matches(
        host_of(url),
        OFFICIAL_DOMAINS.get(
            competitor,
            [],
        ),
    )


def has_official_evidence(
    verified: List[Dict[str, Any]],
    competitor: str,
    requirement_id: str,
) -> bool:
    for f in verified:
        if (
            f["competitor"] != competitor
            or f.get("requirement_id")
            != requirement_id
        ):
            continue

        for e in f.get("evidence", []):
            if is_official(
                e.get("source_url", ""),
                competitor,
            ):
                return True

    return False


# ============================================================
# NODE 1: IDENTIFY RESEARCH REQUIREMENTS
# ============================================================

def identify_research_requirements_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "identifying research requirements")
    _banner("NODE: identify_requirements")

    company = state["company"]

    prompt = requirements_prompt(state)

    result = call_structured(
        prompt,
        ResearchRequirements,
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

    save_node_output(
        "research_requirements",
        {
            "research_requirements": requirements
        },
        state.get(
            "research_stage",
            1,
        ),
    )

    return {
        "research_requirements": requirements
    }


# ============================================================
# NODE 2: GENERATE SEARCH QUERIES
# ============================================================

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

        return {
            "search_queries": [],
            "research_strategy": strategy,
        }

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

    scope_text = "\n".join(
        f"- {c}: "
        f"{COMPETITOR_SCOPE.get(c, 'Research the competitor broadly according to the specific requirement.')}"
        for c in sorted(target_competitors)
    )

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

        text = q.query.strip()

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

    save_node_output(
        "generate_queries",
        {
            "research_stage": stage,
            "research_strategy": strategy,
            "search_queries": queries,
        },
        stage,
    )

    return {
        "search_queries": queries,
        "research_strategy": strategy,
    }


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


# ============================================================
# NODE 3: TAVILY SEARCH
# ============================================================

def run_tavily(
    query: str,
    stage: int,
    domains: List[str] | None,
):
    params = dict(
        query=query,
        search_depth="advanced",
        max_results=MAX_RESULTS_PER_QUERY,
        include_raw_content=True,
        chunks_per_source=3,
    )

    if stage == 1:
        params["time_range"] = "year"

    if domains:
        params["include_domains"] = domains

    return tavily.search(**params)


def search_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "searching sources")
    stage = state.get(
        "research_stage",
        1,
    )

    strategy = state.get(
        "research_strategy",
        "discovery",
    )

    _banner(f"NODE: search  {strategy.upper()}")

    known = {
        (
            r["competitor"],
            normalize_url(r["url"]),
        ): r
        for r in state.get(
            "search_results",
            [],
        )
        if normalize_url(
            r.get("url", "")
        )
    }

    executed = list(
        state.get(
            "executed_queries",
            [],
        )
    )

    added = 0

    for item in state.get(
        "search_queries",
        [],
    ):
        query = item["query"]
        competitor = item["competitor"]

        print(
            f"\n[{competitor}] Query: {query}"
        )

        domains = (
            OFFICIAL_DOMAINS.get(competitor)
            if stage in (2, 3)
            else None
        )

        try:
            response = run_tavily(
                query,
                stage,
                domains,
            )

            tavily_results = response.get(
                "results",
                [],
            )

            if domains and not tavily_results:
                print(
                    "No results on official domains; "
                    "retrying unrestricted."
                )

                response = run_tavily(
                    query,
                    stage,
                    None,
                )

                tavily_results = response.get(
                    "results",
                    [],
                )

            print(
                f"Tavily results: "
                f"{len(tavily_results)}"
            )

            for result in tavily_results:
                url = normalize_url(
                    result.get("url", "")
                )

                if not url:
                    continue

                if belongs_to_other_competitor(
                    url,
                    competitor,
                ):
                    print(
                        "Skipping cross-competitor source: "
                        f"{url}"
                    )
                    continue

                key = (
                    competitor,
                    url,
                )

                if key in known:
                    continue

                known[key] = {
                    "competitor": competitor,
                    "requirement_id": item.get(
                        "requirement_id",
                        "",
                    ),
                    "query": query,
                    "purpose": item.get(
                        "purpose",
                        "",
                    ),
                    "search_type": strategy,
                    "stage": stage,
                    "title": result.get(
                        "title",
                        "",
                    ),
                    "url": url,
                    "text": result_text(
                        result,
                        MAX_STORE_CHARS,
                    ),
                    "published_date": result.get(
                        "published_date"
                    ),
                    "score": result.get(
                        "score"
                    ),
                    "provider": "tavily",
                    "publisher": result.get(
                        "publisher"
                    ),
                }

                added += 1

        except Exception as e:
            print(
                f"Tavily error for '{query}': {e}"
            )

        executed.append(
            {
                "competitor": competitor,
                "query": query,
                "stage": stage,
            }
        )

    results = list(
        known.values()
    )

    print(
        f"\nAdded {added} new results."
    )

    print(
        f"Total sources: {len(results)}"
    )

    save_node_output(
        "search_results",
        {
            "research_stage": stage,
            "research_strategy": strategy,
            "new_sources": added,
            "search_results": results,
        },
        stage,
    )

    return {
        "search_results": results,
        "executed_queries": executed,
        "new_sources_this_stage": added,
    }


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

    findings = merge_findings(
        previous_findings,
        new_unique,
    )

    truly_new = findings[
        len(previous_findings):
    ]

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

    save_node_output(
        "findings",
        {
            "findings": findings,
            "new_findings_this_stage": truly_new,
        },
        stage,
    )

    return {
        "findings": findings,
        "new_findings": truly_new,
    }


# ============================================================
# NODE 5: VERIFY
# Only NEW findings, grouped by competitor
# ============================================================

def verify_batch(
    state: CompetitorState,
    competitor: str,
    batch: List[Dict[str, Any]],
    index,
):
    cited = {}

    for finding in batch:
        for ev in finding.get(
            "evidence",
            [],
        ):
            url = normalize_url(
                ev.get(
                    "source_url",
                    "",
                )
            )

            if url in index:
                cited[url] = index[url]

    if not cited:
        return []

    id_map = {
        f"S{i + 1}": src
        for i, src in enumerate(
            cited.values()
        )
    }

    url_to_id = {
        normalize_url(src["url"]): sid
        for sid, src in id_map.items()
    }

    sources = "\n\n---\n\n".join(
        f"SOURCE ID: {sid}\n"
        f"URL: {src['url']}\n"
        f"TITLE: {src['title']}\n"
        f"SOURCE TEXT:\n"
        f"{(src.get('text') or '')[:SOURCE_CHARS]}"
        for sid, src in id_map.items()
    )

    proposed = [
        {
            "competitor": f["competitor"],
            "requirement_id": f[
                "requirement_id"
            ],
            "finding": f["finding"],
            "competitive_area": f[
                "competitive_area"
            ],
            "business_relevance": f[
                "business_relevance"
            ],
            "evidence_source_ids": [
                url_to_id[
                    normalize_url(
                        e["source_url"]
                    )
                ]
                for e in f.get(
                    "evidence",
                    [],
                )
                if normalize_url(
                    e["source_url"]
                ) in url_to_id
            ],
        }
        for f in batch
    ]

    prompt = verification_prompt(
        state,
        competitor,
        proposed,
        sources,
    )

    result = call_structured(
        prompt,
        BatchVerification,
        strong=True,
    )

    if result is None:
        print(
            "WARNING: verification failed for "
            f"a {competitor} batch; findings "
            "not accepted"
        )
        return []

    allowed_ids = {
        f["requirement_id"]
        for f in batch
    }

    verified = []

    for f in result.verified_findings:
        item = f.model_dump()

        item["competitor"] = competitor

        if (
            item.get("requirement_id")
            not in allowed_ids
        ):
            print(
                "WARNING: verifier returned unknown "
                f"requirement_id "
                f"{item.get('requirement_id')}"
            )
            continue

        item = attach_evidence(
            [item],
            id_map,
        )[0]

        if not item.get("evidence"):
            print(
                "WARNING: verifier accepted a finding "
                "without resolvable evidence; "
                "finding discarded."
            )
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Establish confidence HERE, at verification time.
        #
        # The verifier's confidence is preserved when valid,
        # but it is constrained by the actual evidence.
        #
        # Third-party sources are valid and can receive
        # strong confidence when they directly support
        # the finding.
        # ----------------------------------------------------

        item = ensure_finding_confidence(
            item,
            index,
        )

        if item.get("confidence") is None:
            print(
                "WARNING: verified finding has no "
                "usable confidence; evidence was present "
                "but confidence could not be calculated."
            )

        print(
            f"\nVerified finding: "
            f"{item.get('finding', '')[:160]}"
        )

        print(
            f"Confidence: "
            f"{item.get('confidence')}"
        )

        print(
            f"Evidence quality: "
            f"{item.get('evidence_quality')}"
        )

        verified.append(item)

    return verified


def verify_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "checking findings against sources")
    stage = state.get(
        "research_stage",
        1,
    )

    _banner(f"NODE: verify  STAGE {stage}")

    previously_verified = state.get(
        "verified_findings",
        [],
    )

    new_findings = state.get(
        "new_findings",
        [],
    )

    newly_verified = []

    for competitor in state["competitors"]:
        index = source_index_for(
            state.get(
                "search_results",
                [],
            ),
            competitor,
        )

        comp_new = []

        for f in new_findings:
            if f["competitor"] != competitor:
                continue

            evidence = [
                e
                for e in f.get(
                    "evidence",
                    [],
                )
                if normalize_url(
                    e.get(
                        "source_url",
                        "",
                    )
                ) in index
            ]

            if evidence:
                comp_new.append(
                    {
                        **f,
                        "evidence": evidence,
                    }
                )

        for i in range(
            0,
            len(comp_new),
            VERIFY_BATCH_SIZE,
        ):
            batch = comp_new[
                i:i + VERIFY_BATCH_SIZE
            ]

            newly_verified.extend(
                verify_batch(
                    state,
                    competitor,
                    batch,
                    index,
                )
            )

    verified = merge_findings(
        previously_verified,
        newly_verified,
    )

    # --------------------------------------------------------
    # Normalize old findings too.
    #
    # This matters when the graph resumes or when findings
    # were produced before the confidence logic existed.
    # --------------------------------------------------------

    normalized_verified = []

    for finding in verified:
        competitor = finding.get(
            "competitor",
            "",
        )

        index = source_index_for(
            state.get(
                "search_results",
                [],
            ),
            competitor,
        )

        finding = ensure_finding_confidence(
            finding,
            index,
        )

        normalized_verified.append(
            finding
        )

    verified = normalized_verified

    added = (
        len(verified)
        - len(previously_verified)
    )

    print(
        f"Newly verified this stage: {added}"
    )

    print(
        f"Total verified findings: "
        f"{len(verified)}"
    )

    for f in verified[
        len(previously_verified):
    ]:
        print(
            f"\n[{f['competitor']}] "
            f"({f['requirement_id']}) "
            f"{f['finding']}"
        )

        print(
            f"  Confidence: "
            f"{f.get('confidence')}"
        )

        print(
            f"  Evidence quality: "
            f"{f.get('evidence_quality')}"
        )

        for e in f.get(
            "evidence",
            [],
        ):
            print(
                f"  - {e['source_url']}"
            )

    save_node_output(
        "verified_findings",
        {
            "verified_findings": verified,
            "new_this_stage": added,
        },
        stage,
    )

    return {
        "verified_findings": verified,
        "new_verified_this_stage": added,
    }


# ============================================================
# NODE 6: COMPLETENESS CHECK
# ============================================================

def check_research_requirements_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "checking which requirements are still open")
    stage = state.get(
        "research_stage",
        1,
    )

    _banner(f"NODE: check_requirements  STAGE {stage}")

    requirements = state.get(
        "research_requirements",
        [],
    )

    verified = state.get(
        "verified_findings",
        [],
    )

    prev_checks = {
        c["requirement_id"]: c
        for c in state.get(
            "requirement_checks",
            [],
        )
    }

    checks_by_id = {
        rid: c
        for rid, c in prev_checks.items()
        if c["status"] == "fulfilled"
    }

    pending = [
        r
        for r in requirements
        if r["id"] not in checks_by_id
    ]

    if pending:
        pending_ids = {
            r["id"]
            for r in pending
        }

        pending_comps = {
            r["competitor"]
            for r in pending
        }

        verified_view = [
            finding_view(
                f,
                600,
            )
            for f in verified
            if f["competitor"]
            in pending_comps
        ]

        prompt = check_prompt(
            state,
            stage,
            pending,
            verified_view,
        )

        result = call_structured(
            prompt,
            RequirementCheckList,
            strong=True,
        )

        returned: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for c in (
            result.checks
            if result
            else []
        ):
            if (
                c.requirement_id
                in pending_ids
                and c.requirement_id
                not in returned
            ):
                returned[
                    c.requirement_id
                ] = c.model_dump()

        verified_ids = {
            (
                f["competitor"],
                f.get(
                    "requirement_id"
                ),
            )
            for f in verified
        }

        for r in pending:
            c = returned.get(
                r["id"]
            )

            if c is None:
                c = {
                    "requirement_id": r["id"],
                    "status": "missing",
                    "evidence_found": "",
                    "missing_reason": (
                        "Not evaluated by checker."
                    ),
                }

            if (
                c["status"] == "fulfilled"
                and (
                    r["competitor"],
                    r["id"],
                )
                not in verified_ids
            ):
                c["status"] = "missing"

                c["missing_reason"] = (
                    "Checker claimed fulfilled but "
                    "no verified finding maps to "
                    "this requirement."
                )

            # ------------------------------------------------
            # IMPORTANT:
            #
            # Third-party evidence IS VALID.
            #
            # There is intentionally NO requirement here
            # that evidence must come from the competitor.
            # ------------------------------------------------

            requirement_findings = [
                f
                for f in verified
                if f.get(
                    "competitor"
                ) == r["competitor"]
                and f.get(
                    "requirement_id"
                ) == r["id"]
            ]

            confidence = requirement_confidence(
                requirement_findings,
                r["id"],
            )

            evidence_quality = requirement_evidence_quality(
                requirement_findings,
                r["id"],
            )

            c["confidence"] = confidence
            c["evidence_quality"] = evidence_quality

            if (
                c["status"] == "missing"
                and not (
                    c.get(
                        "missing_reason"
                    )
                    or ""
                ).strip()
            ):
                c["missing_reason"] = (
                    "Checker gave no reason; "
                    "treat the whole requirement "
                    "as not yet established."
                )

            c["competitor"] = r[
                "competitor"
            ]

            c["requirement"] = r[
                "requirement"
            ]

            checks_by_id[
                r["id"]
            ] = c

    checks = [
        checks_by_id[r["id"]]
        for r in requirements
    ]

    # Normalize confidence for requirements that were
    # already fulfilled in previous stages.
    for c in checks:
        if c.get("confidence") is None:
            requirement_findings = [
                f
                for f in verified
                if f.get(
                    "competitor"
                ) == c.get(
                    "competitor"
                )
                and f.get(
                    "requirement_id"
                ) == c.get(
                    "requirement_id"
                )
            ]

            c["confidence"] = requirement_confidence(
                requirement_findings,
                c["requirement_id"],
            )

            c["evidence_quality"] = requirement_evidence_quality(
                requirement_findings,
                c["requirement_id"],
            )

    missing = [
        {
            "requirement_id": c[
                "requirement_id"
            ],
            "competitor": c[
                "competitor"
            ],
            "requirement": c[
                "requirement"
            ],
            "missing_reason": c.get(
                "missing_reason",
                "",
            ),
        }
        for c in checks
        if c["status"] != "fulfilled"
    ]

    print("\nREQUIREMENT STATUS:")

    for c in checks:
        print(
            f'[{c["competitor"]}] '
            f'{c["requirement_id"]}: '
            f'{c["status"]}'
        )

        print(
            f'   Confidence: '
            f'{c.get("confidence")}'
        )

        print(
            f'   Evidence quality: '
            f'{c.get("evidence_quality", "none")}'
        )

        if c["status"] == "missing":
            print(
                f'   Reason: '
                f'{c.get("missing_reason", "")}'
            )

    print(
        f"\nTotal missing requirements: "
        f"{len(missing)} / {len(checks)}"
    )

    save_node_output(
        "requirement_check",
        {
            "checks": checks,
            "missing_information": missing,
        },
        stage,
    )

    return {
        "requirement_checks": checks,
        "missing_information": missing,
    }


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
    stage = state.get(
        "research_stage",
        1,
    )

    missing = state.get(
        "missing_information",
        [],
    )

    if not missing:
        print(
            "\nAll requirements fulfilled."
        )
        return "finish"

    if stage >= MAX_RESEARCH_STAGES:
        print(
            f"\nReached max stages. "
            f"Unresolved requirements: "
            f"{len(missing)}"
        )
        return "finish"

    if (
        stage > 1
        and state.get(
            "new_sources_this_stage",
            0,
        )
        == 0
        and state.get(
            "new_verified_this_stage",
            0,
        )
        == 0
    ):
        print(
            f"\nNo progress this stage. "
            f"Unresolved requirements: "
            f"{len(missing)}"
        )
        return "finish"

    print(
        f"\nResearch incomplete. "
        f"Continuing to stage {stage + 1}."
    )

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


def final_report_node(
    state: CompetitorState,
):
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

    save_node_output(
        "final_report",
        output,
        state.get(
            "research_stage",
            1,
        ),
    )

    md_path = (
        OUTPUT_DIR
        / "final_report.md"
    )

    md_path.write_text(
        render_markdown(
            final_report
        ),
        encoding="utf-8",
    )

    print(
        f"Saved markdown report -> "
        f"{md_path}"
    )

    return output


# ============================================================
# NODE 9: COMPARE TARGET COMPANY WITH COMPETITORS
# ============================================================

def comparison_node(
    state: CompetitorState,
):
    log_action("competitor_intelligence", "building the comparison")
    _banner("NODE: comparison")

    report = state[
        "final_report"
    ]

    company = state[
        "company"
    ]

    competitors = state[
        "competitors"
    ]

    profiles = report.get(
        "competitor_profiles",
        {},
    )

    compact = {}
    req_index = {}

    for c, prof in profiles.items():
        compact[c] = {
            "overview": prof[
                "overview"
            ],
            "requirements": [
                {
                    k: r[k]
                    for k in (
                        "requirement_id",
                        "requirement",
                        "status",
                        "release_status",
                        "confidence",
                        "evidence_quality",
                        "summary",
                    )
                }
                for r in prof[
                    "requirements"
                ]
            ],
            "caveats": prof[
                "caveats"
            ],
        }

        for r in prof[
            "requirements"
        ]:
            req_index[
                (
                    c,
                    r["requirement_id"],
                )
            ] = r

    prompt = comparison_prompt(
        state,
        compact,
    )

    result = call_structured(
        prompt,
        ComparisonReport,
        strong=True,
    )

    if result is None:
        report["comparison"] = {
            "error": (
                "comparison call failed "
                "(no structured output)"
            )
        }

        output = {
            "final_report": report
        }

        save_node_output(
            "final_report",
            output,
            state.get(
                "research_stage",
                1,
            ),
        )

        (
            OUTPUT_DIR
            / "final_report.md"
        ).write_text(
            render_markdown(report),
            encoding="utf-8",
        )

        return output

    canonical = {
        c.lower(): c
        for c in competitors
    }

    dims = []

    for d in result.dimensions:
        assessments: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for a in d.assessments:
            comp = canonical.get(
                a.competitor.strip().lower()
            )

            if not comp or comp in assessments:
                continue

            rids = [
                rid
                for rid in dict.fromkeys(
                    x.strip()
                    for x in a.requirement_ids
                )
                if (
                    comp,
                    rid,
                ) in req_index
            ]

            cited = [
                req_index[
                    (
                        comp,
                        rid,
                    )
                ]
                for rid in rids
            ]

            relative = (
                a.relative_to_target
            )

            # ------------------------------------------------
            # Evidence-grounded comparison confidence
            # ------------------------------------------------

            llm_confidence = normalize_confidence_value(
                a.confidence
            )

            cited_confidences = [
                normalize_confidence_value(
                    r.get("confidence")
                )
                for r in cited
            ]

            cited_confidences = [
                value
                for value in cited_confidences
                if value is not None
            ]

            has_evidence = any(
                r.get("evidence_quality")
                != "none"
                for r in cited
            )

            if not cited or not has_evidence:
                relative = "not_established"
                confidence = "low"

            else:
                # The comparison cannot be more confident
                # than the evidence underneath it.
                #
                # Example:
                # LLM says "high" = 0.85
                # underlying evidence = 0.72
                # final = 0.72
                #
                # Third-party evidence is NOT discounted here.
                # Its confidence was already determined during
                # verification.
                evidence_confidence = min(
                    cited_confidences
                ) if cited_confidences else None

                if (
                    llm_confidence is not None
                    and evidence_confidence is not None
                ):
                    confidence_value = min(
                        llm_confidence,
                        evidence_confidence,
                    )

                elif evidence_confidence is not None:
                    confidence_value = (
                        evidence_confidence
                    )

                else:
                    confidence_value = (
                        llm_confidence
                    )

                if confidence_value is None:
                    relative = "not_established"
                    confidence = "low"
                else:
                    confidence = round(
                        confidence_value,
                        3,
                    )

            urls = []

            for r in cited:
                for src in r[
                    "sources"
                ]:
                    if src["url"] not in urls:
                        urls.append(
                            src["url"]
                        )

            assessments[comp] = {
                "competitor": comp,
                "relative_to_target": relative,
                "confidence": confidence,
                "summary": a.summary.strip(),
                "requirement_ids": rids,
                "sources": urls,
            }

        for comp in competitors:
            if comp not in assessments:
                assessments[comp] = {
                    "competitor": comp,
                    "relative_to_target": (
                        "not_established"
                    ),
                    "confidence": "low",
                    "summary": (
                        "Not assessed for "
                        "this dimension."
                    ),
                    "requirement_ids": [],
                    "sources": [],
                }

        dims.append(
            {
                "dimension": d.dimension,
                "target_position": (
                    d.target_position.strip()
                ),
                "assessments": [
                    assessments[c]
                    for c in competitors
                ],
                "takeaway": d.takeaway.strip(),
            }
        )

    limitations = list(
        result.limitations
    )

    third_party_only = sum(
        1
        for (_, _), r in req_index.items()
        if r["evidence_quality"]
        == "third_party_only"
    )

    if third_party_only:
        limitations.append(
            f"{third_party_only} competitor "
            "requirement(s) were supported by "
            "third-party sources. These sources "
            "were accepted because the evidence "
            "was verified as relevant to the "
            "research requirement."
        )

    unresolved = len(
        report.get(
            "remaining_unresolved_requirements",
            [],
        )
    )

    if unresolved:
        limitations.append(
            f"{unresolved} requirement(s) "
            "remained unresolved after research."
        )

    report["comparison"] = {
        "executive_summary": (
            result.executive_summary.strip()
        ),
        "dimensions": dims,
        "areas_where_target_appears_ahead": (
            result.areas_where_target_appears_ahead
        ),
        "areas_where_competitors_appear_ahead": (
            result.areas_where_competitors_appear_ahead
        ),
        "limitations": list(
            dict.fromkeys(
                limitations
            )
        ),
    }

    print(
        f"Comparison built: "
        f"{len(dims)} dimensions x "
        f"{len(competitors)} competitors"
    )

    output = {
        "final_report": report
    }

    save_node_output(
        "final_report",
        output,
        state.get(
            "research_stage",
            1,
        ),
    )

    (
        OUTPUT_DIR
        / "final_report.md"
    ).write_text(
        render_markdown(report),
        encoding="utf-8",
    )

    print(
        f"Saved markdown report -> "
        f"{OUTPUT_DIR / 'final_report.md'}"
    )

    return output


# ============================================================
# WORKER RESULT ADAPTER
# ============================================================

def build_worker_result(
    state: CompetitorState,
) -> WorkerResult:
    """
    Convert the competitor graph state into the shared WorkerResult contract.

    The graph's final_report remains an internal rich report.
    WorkerResult is the standardized output returned to the orchestrator.

    Confidence should already have been established during verification.
    This function only normalizes/uses that value as a final safety layer.
    """

    verified_findings = state.get(
        "verified_findings",
        [],
    )

    search_results = state.get(
        "search_results",
        [],
    )

    unresolved = state.get(
        "missing_information",
        [],
    )

    # --------------------------------------------------------
    # 1. Build unique Evidence objects
    # --------------------------------------------------------

    evidence_by_url: Dict[str, Evidence] = {}

    for result in search_results:
        url = normalize_url(
            result.get("url", "")
        )

        if not url:
            continue

        if url in evidence_by_url:
            continue

        source_type = (
            SourceType.OFFICIAL_DOCUMENTATION
            if is_official(
                url,
                result.get("competitor", ""),
            )
            else SourceType.WEB_PAGE
        )

        evidence_by_url[url] = Evidence(
            source_type=source_type,
            source_title=result.get("title") or None,
            source_url=url,
            publisher=result.get("publisher"),
            extracted_fact=(
                result.get("text")
                or "Evidence retrieved from the source."
            )[:SOURCE_CHARS],
            excerpt=(
                result.get("text") or ""
            )[:SOURCE_CHARS],
            metadata={
                "competitor": result.get(
                    "competitor",
                    "",
                ),
                "requirement_id": result.get(
                    "requirement_id",
                    "",
                ),
                "query": result.get(
                    "query",
                    "",
                ),
                "search_type": result.get(
                    "search_type",
                    "",
                ),
                "stage": result.get(
                    "stage",
                    1,
                ),
                "provider": result.get(
                    "provider",
                    "",
                ),
                "published_date": result.get(
                    "published_date"
                ),
                "score": result.get(
                    "score"
                ),
            },
        )

    # --------------------------------------------------------
    # 2. Convert verified findings
    # --------------------------------------------------------

    worker_findings = []

    for raw_finding in verified_findings:
        statement = (
            raw_finding.get("finding")
            or ""
        ).strip()

        if not statement:
            continue

        category = map_finding_category(
            raw_finding.get(
                "competitive_area",
                "",
            )
        )

        evidence_refs = []

        for evidence in raw_finding.get(
            "evidence",
            [],
        ):
            url = normalize_url(
                evidence.get(
                    "source_url",
                    "",
                )
            )

            source = evidence_by_url.get(url)

            if not source:
                continue

            evidence_refs.append(
                EvidenceRef(
                    evidence_id=source.evidence_id,
                    relation=EvidenceRelation.SUPPORTS,
                )
            )

        if not evidence_refs:
            continue

        # Confidence was established during verification.
        confidence = normalize_confidence_value(
            raw_finding.get("confidence")
        )

        # Safety fallback only.
        #
        # Normally this should already exist because verify_batch()
        # calls ensure_finding_confidence().
        if confidence is None:
            competitor = raw_finding.get(
                "competitor",
                "",
            )

            index = source_index_for(
                search_results,
                competitor,
            )

            confidence = calculate_finding_confidence(
                raw_finding,
                index,
            )

        worker_findings.append(
            Finding(
                statement=statement,
                category=category,
                evidence_refs=evidence_refs,
                confidence=confidence,
                limitations=[],
            )
        )

    # --------------------------------------------------------
    # 3. Determine worker status
    # --------------------------------------------------------

    if worker_findings:
        if unresolved:
            status = WorkerStatus.PARTIAL
        else:
            status = WorkerStatus.COMPLETED

    else:
        status = WorkerStatus.NO_EVIDENCE

    # --------------------------------------------------------
    # 4. Convert unresolved requirements into gaps
    # --------------------------------------------------------

    gaps = []

    for item in unresolved:
        competitor = item.get(
            "competitor",
            "",
        )

        requirement = item.get(
            "requirement",
            "",
        )

        reason = item.get(
            "missing_reason",
            "",
        )

        gap = (
            f"[{competitor}] "
            f"{requirement}"
        )

        if reason:
            gap += f" — {reason}"

        gaps.append(gap)

    # --------------------------------------------------------
    # 5. Overall confidence
    # --------------------------------------------------------

    overall_confidence = calculate_overall_confidence(
        worker_findings
    )

    # --------------------------------------------------------
    # 6. WorkerResult
    # --------------------------------------------------------

    output = WorkerResult(
        task_id=state["task_id"],
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=status,
        attempt=1,
        findings=worker_findings,
        evidence=list(
            evidence_by_url.values()
        ),
        gaps=gaps,
        conflicts=[],
        confidence=overall_confidence,
        metadata={
            "company": state.get(
                "company",
                "",
            ),
            "competitors": state.get(
                "competitors",
                [],
            ),
            "research_goal": state.get(
                "research_goal",
                "",
            ),
            "research_focus": state.get(
                "research_focus",
                "",
            ),
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
                search_results
            ),
            "total_verified_findings": len(
                verified_findings
            ),
            "requirement_checks": state.get(
                "requirement_checks",
                [],
            ),
            "competitor_profiles": (
                state.get(
                    "final_report",
                    {}
                ).get(
                    "competitor_profiles",
                    {},
                )
            ),
        },
    )

    save_node_output(
        "worker_result",
        output.model_dump(mode="json"),
        state.get(
            "research_stage",
            1,
        ),
    )

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