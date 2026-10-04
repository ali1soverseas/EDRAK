"""Graph nodes, helpers, routers and report rendering."""

import difflib
import json
import os
import pprint
import re
from urllib.parse import urlparse
from typing import List, Dict, Any

from langchain_openai import ChatOpenAI
from tavily import TavilyClient
from dotenv import load_dotenv

from state import (
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
    OUTPUT_DIR,
    REQUIRE_OFFICIAL_SOURCE,
    RequirementCheckList,
    ResearchRequirements,
    SOURCE_CHARS,
    STAGE_STRATEGY,
    SearchQueryList,
    VERIFY_BATCH_SIZE,
)
from prompts import (
    STAGE_SYNTH_GUIDANCE,
    check_prompt,
    comparison_prompt,
    consolidation_prompt,
    queries_prompt,
    requirements_prompt,
    synthesis_prompt,
    verification_prompt,
)


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is missing")

if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY is missing")

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

# Stronger model: the two strict reasoning steps (verification, completeness)
llm_strong = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    api_key=OPENAI_API_KEY,
    max_retries=4,
)

from pathlib import Path
import json

# ============================================================
# OUTPUT
# ============================================================
COMPETITOR_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = COMPETITOR_DIR / "outputs"


def save_node_output(
    node_name: str,
    output: Dict[str, Any],
    research_stage: int = 1
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    file_path = OUTPUT_DIR / f"stage_{research_stage}_{node_name}.json"

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            indent=4,
            ensure_ascii=False,
            default=str
        )

    print(f"Saved node output -> {file_path}")


# ============================================================
# HELPERS
# ============================================================

def call_structured(prompt: str, model_class, strong: bool = False, retries: int = 1):
    """
    Function-calling structured output. Returns None if the model never
    produced a valid call (callers MUST handle None).
    """
    model = llm_strong if strong else llm
    structured = model.with_structured_output(model_class, method="function_calling")

    for attempt in range(retries + 1):
        try:
            result = structured.invoke(prompt)
            if result is not None:
                return result
            print(f"WARNING: structured call returned None (attempt {attempt + 1})")
        except Exception as e:
            print(f"WARNING: structured call failed (attempt {attempt + 1}): {e}")
    return None


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def host_matches(host: str, domains: List[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def belongs_to_other_competitor(url: str, competitor: str) -> bool:
    """True if the URL is on another competitor's official domain only."""
    own = OFFICIAL_DOMAINS.get(competitor, [])
    host = host_of(url)
    if host_matches(host, own):
        return False
    for other, domains in OFFICIAL_DOMAINS.items():
        if other != competitor and host_matches(host, domains):
            return True
    return False


def result_text(result: Dict[str, Any], limit: int) -> str:
    content = (result.get("content") or "").strip()
    raw = (result.get("raw_content") or "").strip()
    text = content
    if raw and len(text) < limit:
        text = f"{text}\n{raw}" if text else raw
    return text[:limit]


def requirements_by_id(state: CompetitorState) -> Dict[str, Dict[str, str]]:
    return {r["id"]: r for r in state.get("research_requirements", [])}


def competitor_requirements(state: CompetitorState, competitor: str) -> List[Dict[str, str]]:
    return [r for r in state.get("research_requirements", []) if r["competitor"] == competitor]


def source_index_for(results: List[Dict[str, Any]], competitor: str) -> Dict[str, Dict[str, Any]]:
    """url -> result, restricted to ONE competitor's results."""
    index = {}
    for r in results:
        if r.get("competitor") != competitor:
            continue
        url = normalize_url(r.get("url", ""))
        if url:
            index[url] = r
    return index


def canonical_url(url: str) -> str:
    u = normalize_url(url).lower().split("#")[0].split("?")[0]
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def build_evidence(results: List[Dict[str, Any]]):
    """Returns (evidence_text, id_map). The LLM cites short IDs (S1, S2...)."""
    blocks, id_map = [], {}
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


def chunk_results(results: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Split results into batches that fit MAX_EVIDENCE_CHARS (no truncation)."""
    batches, current, size = [], [], 0
    for r in results:
        cost = min(len(r.get("text") or ""), SOURCE_CHARS) + 300
        if current and size + cost > MAX_EVIDENCE_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(r)
        size += cost
    if current:
        batches.append(current)
    return batches


def resolve_source(ref: str, id_map: Dict[str, Dict[str, Any]]):
    """Resolve an LLM reference (S3, [S3], or a URL) to a source result."""
    ref = str(ref).strip()
    m = re.fullmatch(r"\[?\s*(S\d+)\s*\]?", ref, flags=re.IGNORECASE)
    if m:
        return id_map.get(m.group(1).upper())
    wanted = canonical_url(ref)
    if wanted:
        for r in id_map.values():
            if canonical_url(r.get("url", "")) == wanted:
                return r
    return None


def attach_evidence(
    findings: List[Dict[str, Any]],
    id_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert LLM-cited source IDs into real evidence."""
    processed = []
    for finding in findings:
        refs = finding.pop("evidence_source_ids", None) or []
        evidence, seen = [], set()
        for ref in refs:
            source = resolve_source(ref, id_map)
            if not source:
                continue
            url = normalize_url(source.get("url", ""))
            if url in seen:
                continue
            seen.add(url)
            evidence.append(
                {
                    "source_title": source.get("title", ""),
                    "source_url": url,
                    "supporting_text": (source.get("text") or "")[:SOURCE_CHARS],
                }
            )
            if len(evidence) >= MAX_EVIDENCE_PER_FINDING:
                break
        if refs and not evidence:
            print(f"DEBUG: could not resolve source refs {refs} (valid: {list(id_map)})")
        finding["evidence"] = evidence
        finding["evidence_urls"] = [e["source_url"] for e in evidence]
        processed.append(finding)
    return processed


def finding_view(finding: Dict[str, Any], text_limit: int = 400) -> Dict[str, Any]:
    """Compact finding for prompts: no supporting_text blobs."""
    return {
        "competitor": finding.get("competitor", ""),
        "requirement_id": finding.get("requirement_id", ""),
        "finding": (finding.get("finding", "") or "")[:text_limit],
        "evidence_urls": [e.get("source_url", "") for e in finding.get("evidence", [])]
        or finding.get("evidence_urls", []),
    }


def is_duplicate(new: Dict[str, Any], existing: List[Dict[str, Any]]) -> bool:
    a = new["finding"].strip().lower()
    for old in existing:
        if old["competitor"] != new["competitor"]:
            continue
        if old.get("requirement_id") != new.get("requirement_id"):
            continue
        b = old["finding"].strip().lower()
        if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= DUPLICATE_SIMILARITY:
            return True
    return False


def merge_findings(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(existing)
    for f in new:
        if not is_duplicate(f, merged):
            merged.append(f)
    return merged


def is_official(url: str, competitor: str) -> bool:
    return host_matches(host_of(url), OFFICIAL_DOMAINS.get(competitor, []))


def has_official_evidence(verified: List[Dict[str, Any]], competitor: str, requirement_id: str) -> bool:
    for f in verified:
        if f["competitor"] != competitor or f.get("requirement_id") != requirement_id:
            continue
        for e in f.get("evidence", []):
            if is_official(e.get("source_url", ""), competitor):
                return True
    return False


# ============================================================
# NODE 1: IDENTIFY RESEARCH REQUIREMENTS
# ============================================================

def identify_research_requirements_node(state: CompetitorState):
    print("\n" + "=" * 80)
    print("IDENTIFYING RESEARCH REQUIREMENTS")
    print("=" * 80)

    company = state["company"]

    prompt = requirements_prompt(state)

    result = call_structured(prompt, ResearchRequirements)
    if result is None:
        raise RuntimeError("Requirement identification failed (no structured output).")

    canonical = {c.lower(): c for c in state["competitors"]}
    requirements = []

    for item in result.requirements:
        competitor = canonical.get(item.competitor.strip().lower())
        if not competitor:
            print(f"WARNING: dropping requirement for unknown competitor: {item.competitor}")
            continue
        requirements.append(
            {
                "id": f"R{len(requirements) + 1}",
                "competitor": competitor,
                "requirement": item.requirement.strip(),
            }
        )

    covered = {r["competitor"] for r in requirements}
    for competitor in state["competitors"]:
        if competitor not in covered:
            print(f"WARNING: no requirements generated for {competitor}")

    if not requirements:
        raise RuntimeError("No valid research requirements were generated.")

    print("\nDYNAMIC RESEARCH REQUIREMENTS:")
    pprint.pprint(requirements, sort_dicts=False)

    save_node_output(
        "research_requirements",
        {"research_requirements": requirements},
        state.get("research_stage", 1),
    )

    return {"research_requirements": requirements}


# ============================================================
# NODE 2: GENERATE SEARCH QUERIES
# ============================================================

def generate_queries_node(state: CompetitorState):
    stage = state.get("research_stage", 1)
    strategy = STAGE_STRATEGY.get(stage, "targeted_missing_requirements")

    print("\n" + "=" * 80)
    print(f"RESEARCH STAGE {stage}: {strategy.upper()}")
    print("=" * 80)

    reqs = requirements_by_id(state)

    # Stage 1 targets everything. Stage 2+ targets ONLY unfulfilled requirements.
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
                "missing_reason": m.get("missing_reason", ""),
            }
            for m in state.get("missing_information", [])
            if m["requirement_id"] in reqs
        ]

    if not targets:
        print("No targets for this stage.")
        return {"search_queries": [], "research_strategy": strategy}

    target_competitors = {t["competitor"] for t in targets}

    known_findings = [
        finding_view(f, 300)
        for f in state.get("verified_findings", [])
        if f["competitor"] in target_competitors
    ][-60:]

    executed = state.get("executed_queries", [])
    previous_searches = [
        {"competitor": e["competitor"], "query": e["query"]}
        for e in executed
        if e["competitor"] in target_competitors
    ][-150:]

    scope_text = "\n".join(
        f"- {c}: {COMPETITOR_SCOPE[c]}"
        for c in sorted(target_competitors)
        if c in COMPETITOR_SCOPE
    )

    prompt = queries_prompt(state, stage, strategy, targets, known_findings, previous_searches, scope_text)

    result = call_structured(prompt, SearchQueryList)

    valid = {t["requirement_id"]: t for t in targets}
    executed_keys = {
        (e["competitor"].strip().lower(), e["query"].strip().lower()) for e in executed
    }

    queries, seen, per_req = [], set(), {}

    for q in (result.queries if result else []):
        rid = q.requirement_id.strip()
        target = valid.get(rid)
        if not target:
            print(f"WARNING: dropping query with unknown requirement_id '{rid}': {q.query}")
            continue

        competitor = target["competitor"]  # competitor always comes from the requirement
        text = q.query.strip()
        key = (competitor.lower(), text.lower())

        if not text or key in seen or key in executed_keys:
            continue
        if per_req.get(rid, 0) >= MAX_QUERIES_PER_REQUIREMENT:
            continue

        seen.add(key)
        per_req[rid] = per_req.get(rid, 0) + 1

        queries.append(
            {
                "competitor": competitor,
                "requirement_id": rid,
                "requirement": target["requirement"],
                "query": text,
                "purpose": q.purpose,
                "search_type": strategy,
            }
        )

    print("\nGENERATED QUERIES:")
    for q in queries:
        print(f'\n[{q["competitor"]}] {q["query"]}')
        print(f'Requirement {q["requirement_id"]}: {q["requirement"]}')
        print(f'Purpose: {q["purpose"]}')

    if not queries:
        print("\nWARNING: no valid new queries survived validation.")

    save_node_output(
        "generate_queries",
        {
            "research_stage": stage,
            "research_strategy": strategy,
            "search_queries": queries,
        },
        stage,
    )

    return {"search_queries": queries, "research_strategy": strategy}


def after_queries_router(state: CompetitorState):
    if state.get("search_queries"):
        return "search"
    print("\nNo queries to run. Finishing research loop.")
    return "finish"


# ============================================================
# NODE 3: TAVILY SEARCH
# ============================================================

def run_tavily(query: str, stage: int, domains: List[str] | None):
    params = dict(
        query=query,
        search_depth="advanced",
        max_results=MAX_RESULTS_PER_QUERY,
        include_raw_content=True,
        chunks_per_source=3,
    )
    # Recency window only for discovery. Docs/pricing pages are often older.
    if stage == 1:
        params["time_range"] = "year"
    if domains:
        params["include_domains"] = domains
    return tavily.search(**params)


def search_node(state: CompetitorState):
    stage = state.get("research_stage", 1)
    strategy = state.get("research_strategy", "discovery")

    print("\n" + "=" * 80)
    print(f"SEARCH - {strategy.upper()}")
    print("=" * 80)

    # Keyed by (competitor, url): a URL is never silently re-labelled
    known = {
        (r["competitor"], normalize_url(r["url"])): r
        for r in state.get("search_results", [])
        if normalize_url(r.get("url", ""))
    }

    executed = list(state.get("executed_queries", []))
    added = 0

    for item in state.get("search_queries", []):
        query = item["query"]
        competitor = item["competitor"]

        print(f"\n[{competitor}] Query: {query}")

        domains = OFFICIAL_DOMAINS.get(competitor) if stage in (2, 3) else None

        try:
            response = run_tavily(query, stage, domains)
            tavily_results = response.get("results", [])

            # Fallback: domain-restricted search found nothing
            if domains and not tavily_results:
                print("No results on official domains; retrying unrestricted.")
                response = run_tavily(query, stage, None)
                tavily_results = response.get("results", [])

            print(f"Tavily results: {len(tavily_results)}")

            for result in tavily_results:
                url = normalize_url(result.get("url", ""))
                if not url:
                    continue

                # Drop pages that live on ANOTHER competitor's official domain
                if belongs_to_other_competitor(url, competitor):
                    print(f"Skipping cross-competitor source: {url}")
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
                    "title": result.get("title", ""),
                    "url": url,
                    "text": result_text(result, MAX_STORE_CHARS),
                    "published_date": result.get("published_date"),
                    "score": result.get("score"),
                    "provider": "tavily",
                }
                added += 1

        except Exception as e:
            print(f"Tavily error for '{query}': {e}")

        # Record the query even if it returned only known URLs or failed
        executed.append(
            {"competitor": competitor, "query": query, "stage": stage}
        )

    results = list(known.values())

    print(f"\nAdded {added} new results.")
    print(f"Total sources: {len(results)}")

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
# NODE 4: SYNTHESIZE (per competitor, only THIS stage's sources)
# ============================================================

def synthesize_node(state: CompetitorState):
    stage = state.get("research_stage", 1)

    print("\n" + "=" * 80)
    print(f"SYNTHESIZE - STAGE {stage}")
    print("=" * 80)

    company = state["company"]
    guidance = STAGE_SYNTH_GUIDANCE.get(
        stage,
        "STAGE 4+ (gap filling): produce findings only for information newly supported by this evidence.",
    )

    previous_findings = state.get("findings", [])
    reqs = requirements_by_id(state)
    new_results = [r for r in state.get("search_results", []) if r.get("stage") == stage]

    all_new = []

    for competitor in state["competitors"]:
        comp_results = [r for r in new_results if r["competitor"] == competitor]
        comp_reqs = competitor_requirements(state, competitor)

        if not comp_results or not comp_reqs:
            continue

        already = [
            finding_view(f, 250)
            for f in previous_findings
            if f["competitor"] == competitor
        ][-40:]

        for batch in chunk_results(comp_results):
            evidence, id_map = build_evidence(batch)
            if not id_map:
                continue

            prompt = synthesis_prompt(state, competitor, guidance, comp_reqs, already, evidence)

            result = call_structured(prompt, FindingList)
            if result is None:
                print(f"WARNING: synthesis failed for {competitor} (batch skipped)")
                continue

            batch_findings = []
            for f in result.findings:
                item = f.model_dump()
                item["competitor"] = competitor

                rid = (item.get("requirement_id") or "").strip()
                if rid not in reqs or reqs[rid]["competitor"] != competitor:
                    print(f"WARNING: dropping finding with invalid requirement_id '{rid}'")
                    continue
                item["requirement_id"] = rid
                batch_findings.append(item)

            batch_findings = attach_evidence(batch_findings, id_map)

            for f in batch_findings:
                if f.get("evidence"):
                    all_new.append(f)
                else:
                    print(
                        "WARNING: dropping finding (no valid evidence attached): "
                        f"{f.get('finding', '')[:120]}"
                    )

    # Within-stage dedup, then merge with earlier findings
    new_unique = merge_findings([], all_new)
    findings = merge_findings(previous_findings, new_unique)
    truly_new = findings[len(previous_findings):]

    print(f"Evidence-backed findings this stage: {len(all_new)}")
    print(f"New after dedup: {len(truly_new)}")
    print(f"Accumulated findings: {len(findings)}")

    save_node_output(
        "findings",
        {"findings": findings, "new_findings_this_stage": truly_new},
        stage,
    )

    return {"findings": findings, "new_findings": truly_new}


# ============================================================
# NODE 5: VERIFY (only NEW findings, grouped by competitor)
# ============================================================

def verify_batch(state: CompetitorState, competitor: str, batch: List[Dict[str, Any]], index):
    cited = {}
    for finding in batch:
        for ev in finding.get("evidence", []):
            url = normalize_url(ev.get("source_url", ""))
            if url in index:
                cited[url] = index[url]

    if not cited:
        return []

    id_map = {f"S{i + 1}": src for i, src in enumerate(cited.values())}
    url_to_id = {normalize_url(src["url"]): sid for sid, src in id_map.items()}

    sources = "\n\n---\n\n".join(
        f"SOURCE ID: {sid}\nURL: {src['url']}\nTITLE: {src['title']}\n"
        f"SOURCE TEXT:\n{(src.get('text') or '')[:SOURCE_CHARS]}"
        for sid, src in id_map.items()
    )

    proposed = [
        {
            "competitor": f["competitor"],
            "requirement_id": f["requirement_id"],
            "finding": f["finding"],
            "competitive_area": f["competitive_area"],
            "business_relevance": f["business_relevance"],
            "evidence_source_ids": [
                url_to_id[normalize_url(e["source_url"])]
                for e in f.get("evidence", [])
                if normalize_url(e["source_url"]) in url_to_id
            ],
        }
        for f in batch
    ]

    prompt = verification_prompt(state, competitor, proposed, sources)

    result = call_structured(prompt, BatchVerification, strong=True)
    if result is None:
        print(f"WARNING: verification failed for a {competitor} batch; findings not accepted")
        return []

    allowed_ids = {f["requirement_id"] for f in batch}
    verified = []

    for f in result.verified_findings:
        item = f.model_dump()
        item["competitor"] = competitor

        if item.get("requirement_id") not in allowed_ids:
            print(f"WARNING: verifier returned unknown requirement_id {item.get('requirement_id')}")
            continue

        item = attach_evidence([item], id_map)[0]
        if item.get("evidence"):
            verified.append(item)

    return verified


def verify_node(state: CompetitorState):
    stage = state.get("research_stage", 1)

    print("\n" + "=" * 80)
    print(f"VERIFY - STAGE {stage}")
    print("=" * 80)

    previously_verified = state.get("verified_findings", [])
    new_findings = state.get("new_findings", [])

    newly_verified = []

    for competitor in state["competitors"]:
        index = source_index_for(state.get("search_results", []), competitor)

        comp_new = []
        for f in new_findings:
            if f["competitor"] != competitor:
                continue
            evidence = [
                e for e in f.get("evidence", [])
                if normalize_url(e.get("source_url", "")) in index
            ]
            if evidence:
                comp_new.append({**f, "evidence": evidence})

        for i in range(0, len(comp_new), VERIFY_BATCH_SIZE):
            batch = comp_new[i:i + VERIFY_BATCH_SIZE]
            newly_verified.extend(verify_batch(state, competitor, batch, index))

    verified = merge_findings(previously_verified, newly_verified)
    added = len(verified) - len(previously_verified)

    print(f"Newly verified this stage: {added}")
    print(f"Total verified findings: {len(verified)}")

    for f in verified[len(previously_verified):]:
        print(f"\n[{f['competitor']}] ({f['requirement_id']}) {f['finding']}")
        for e in f.get("evidence", []):
            print(f"  - {e['source_url']}")

    save_node_output(
        "verified_findings",
        {"verified_findings": verified, "new_this_stage": added},
        stage,
    )

    return {"verified_findings": verified, "new_verified_this_stage": added}


# ============================================================
# NODE 6: COMPLETENESS CHECK
# ============================================================

def check_research_requirements_node(state: CompetitorState):
    stage = state.get("research_stage", 1)

    print("\n" + "=" * 80)
    print(f"CHECKING RESEARCH COMPLETENESS - STAGE {stage}")
    print("=" * 80)

    requirements = state.get("research_requirements", [])
    verified = state.get("verified_findings", [])

    # Fulfilled stays fulfilled; only re-check what is still pending
    prev_checks = {c["requirement_id"]: c for c in state.get("requirement_checks", [])}
    checks_by_id = {
        rid: c for rid, c in prev_checks.items() if c["status"] == "fulfilled"
    }
    pending = [r for r in requirements if r["id"] not in checks_by_id]

    if pending:
        pending_ids = {r["id"] for r in pending}
        pending_comps = {r["competitor"] for r in pending}
        verified_view = [
            finding_view(f, 600) for f in verified if f["competitor"] in pending_comps
        ]

        prompt = check_prompt(state, stage, pending, verified_view)

        result = call_structured(prompt, RequirementCheckList, strong=True)

        returned: Dict[str, Dict[str, Any]] = {}
        for c in (result.checks if result else []):
            if c.requirement_id in pending_ids and c.requirement_id not in returned:
                returned[c.requirement_id] = c.model_dump()

        verified_ids = {(f["competitor"], f.get("requirement_id")) for f in verified}

        for r in pending:
            c = returned.get(r["id"])

            # Any combination the LLM skipped counts as missing
            if c is None:
                c = {
                    "requirement_id": r["id"],
                    "status": "missing",
                    "evidence_found": "",
                    "missing_reason": "Not evaluated by checker.",
                }

            # Python guard: fulfilled needs at least one verified finding mapped to it
            if c["status"] == "fulfilled" and (r["competitor"], r["id"]) not in verified_ids:
                c["status"] = "missing"
                c["missing_reason"] = "Checker claimed fulfilled but no verified finding maps to this requirement."

            # Python guard: stay "missing" until a first-party source backs it
            if (
                REQUIRE_OFFICIAL_SOURCE
                and c["status"] == "fulfilled"
                and stage < MAX_RESEARCH_STAGES
                and not has_official_evidence(verified, r["competitor"], r["id"])
            ):
                c["status"] = "missing"
                c["missing_reason"] = (
                    "Only third-party sources so far. Need first-party confirmation "
                    "(official documentation, pricing page, release notes or "
                    "announcement) of: "
                    + ((c.get("evidence_found") or "").strip() or r["requirement"])
                )

            if c["status"] == "missing" and not (c.get("missing_reason") or "").strip():
                c["missing_reason"] = "Checker gave no reason; treat the whole requirement as not yet established."

            c["competitor"] = r["competitor"]
            c["requirement"] = r["requirement"]
            checks_by_id[r["id"]] = c

    checks = [checks_by_id[r["id"]] for r in requirements]

    missing = [
        {
            "requirement_id": c["requirement_id"],
            "competitor": c["competitor"],
            "requirement": c["requirement"],
            "missing_reason": c.get("missing_reason", ""),
        }
        for c in checks
        if c["status"] != "fulfilled"
    ]

    print("\nREQUIREMENT STATUS:")
    for c in checks:
        print(f'[{c["competitor"]}] {c["requirement_id"]}: {c["status"]}')
        if c["status"] == "missing":
            print(f'   Reason: {c.get("missing_reason", "")}')

    print(f"\nTotal missing requirements: {len(missing)} / {len(checks)}")

    save_node_output(
        "requirement_check",
        {"checks": checks, "missing_information": missing},
        stage,
    )

    return {"requirement_checks": checks, "missing_information": missing}


# ============================================================
# NODE 7: PREPARE NEXT STAGE
# ============================================================

def prepare_next_stage_node(state: CompetitorState):
    next_stage = state.get("research_stage", 1) + 1

    print("\n" + "=" * 80)
    print(f"MOVING TO RESEARCH STAGE {next_stage}")
    print("=" * 80)

    for item in state.get("missing_information", []):
        print(f'\n[{item["competitor"]}] {item["requirement_id"]}: {item["requirement"]}')
        print(f'Reason: {item.get("missing_reason", "")}')

    return {"research_stage": next_stage}


# ============================================================
# ROUTER (after completeness check)
# ============================================================

def research_router(state: CompetitorState):
    stage = state.get("research_stage", 1)
    missing = state.get("missing_information", [])

    if not missing:
        print("\nAll requirements fulfilled.")
        return "finish"

    if stage >= MAX_RESEARCH_STAGES:
        print(f"\nReached max stages. Unresolved requirements: {len(missing)}")
        return "finish"

    # Early exit when a stage produced nothing new
    if (
        stage > 1
        and state.get("new_sources_this_stage", 0) == 0
        and state.get("new_verified_this_stage", 0) == 0
    ):
        print(f"\nNo progress this stage. Unresolved requirements: {len(missing)}")
        return "finish"

    print(f"\nResearch incomplete. Continuing to stage {stage + 1}.")
    return "research_again"


# ============================================================
# NODE 8: FINAL REPORT
# ============================================================

def consolidate_competitor(state: CompetitorState, competitor: str) -> Dict[str, Any]:
    """One merged, evidence-grounded profile per competitor."""
    reqs = competitor_requirements(state, competitor)
    checks = {c["requirement_id"]: c for c in state.get("requirement_checks", [])}
    findings = [
        f for f in state.get("verified_findings", []) if f["competitor"] == competitor
    ]

    # Per-competitor source IDs (S1, S2...), deduplicated by URL
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
        payload.append(
            {
                "requirement_id": r["id"],
                "requirement": r["requirement"],
                "status": chk.get("status", "missing"),
                "missing_reason": chk.get("missing_reason", ""),
                "findings": [
                    {
                        "finding": f["finding"],
                        "source_ids": [
                            sources[normalize_url(e["source_url"])]["id"]
                            for e in f.get("evidence", [])
                            if normalize_url(e.get("source_url", "")) in sources
                        ],
                    }
                    for f in findings
                    if f.get("requirement_id") == r["id"]
                ],
            }
        )

    source_list = [
        {
            "id": v["id"],
            "title": v["title"],
            "url": u,
            "first_party": is_official(u, competitor),
        }
        for u, v in sources.items()
    ]

    profile = None
    if findings:
        prompt = consolidation_prompt(state, competitor, payload, source_list)
        profile = call_structured(prompt, CompetitorProfile)

    by_req = {p.requirement_id: p for p in profile.requirements} if profile else {}

    out_reqs = []
    caveats = list(profile.caveats) if profile else []

    for item in payload:
        rid = item["requirement_id"]
        p = by_req.get(rid)

        union_ids = sorted({sid for f in item["findings"] for sid in f["source_ids"]})

        if p and p.summary.strip():
            summary, release = p.summary.strip(), p.release_status
            ids = [str(i).strip().upper() for i in p.evidence_source_ids]
        else:
            summary = (
                " ".join(f["finding"] for f in item["findings"])
                or "Not established by the verified evidence."
            )
            release, ids = "not_stated", union_ids

        urls = []
        for sid in ids:
            url = id_to_url.get(sid)
            if url and url not in urls:
                urls.append(url)
        if not urls:
            urls = [id_to_url[sid] for sid in union_ids if sid in id_to_url]

        out_sources = [
            {
                "url": u,
                "title": sources[u]["title"],
                "first_party": is_official(u, competitor),
            }
            for u in urls
        ]

        if not out_sources:
            quality = "none"
        elif any(x["first_party"] for x in out_sources):
            quality = "first_party"
        else:
            quality = "third_party_only"
            caveats.append(f"{rid}: supported only by third-party sources.")

        out_reqs.append(
            {
                "requirement_id": rid,
                "requirement": item["requirement"],
                "status": item["status"],
                "missing_reason": item["missing_reason"],
                "release_status": release,
                "evidence_quality": quality,
                "summary": summary,
                "sources": out_sources,
            }
        )

    if profile:
        overview = profile.overview.strip()
    elif findings:
        overview = "Profile synthesis failed; see per-requirement summaries."
    else:
        overview = "No verified findings were established for this competitor."

    return {
        "overview": overview,
        "requirements": out_reqs,
        "caveats": list(dict.fromkeys(caveats)),
        "verified_findings_count": len(findings),
    }


def render_comparison_lines(report: Dict[str, Any]) -> List[str]:
    comp = report.get("comparison")
    if not comp:
        return []

    lines = [f"## Comparison: {report['target_company']} vs competitors", ""]

    if comp.get("error"):
        return lines + [f"Comparison unavailable: {comp['error']}", ""]

    lines += [comp.get("executive_summary", ""), ""]

    for d in comp.get("dimensions", []):
        lines += [f"### {d['dimension']}", "", f"**{report['target_company']}:** {d['target_position']}", ""]
        for a in d["assessments"]:
            links = ", ".join(
                f"[source]({u})" for u in a.get("sources", [])[:3]
            )
            lines.append(
                f"- **{a['competitor']}** ({a['relative_to_target']}, confidence: "
                f"{a['confidence']}): {a['summary']} {links}".rstrip()
            )
        lines += ["", f"_Takeaway:_ {d['takeaway']}", ""]

    for title, key in (
        (f"Where {report['target_company']} appears ahead", "areas_where_target_appears_ahead"),
        ("Where competitors appear ahead", "areas_where_competitors_appear_ahead"),
        ("Limitations", "limitations"),
    ):
        items = comp.get(key, [])
        if items:
            lines.append(f"**{title}**")
            lines += [f"- {i}" for i in items]
            lines.append("")

    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# Competitor intelligence: {report['target_company']}",
        "",
        "## Research goal",
        report["research_goal"].strip(),
        "",
        f"Stages completed: {report['research_stages_completed']} | "
        f"Queries: {report['queries_executed']} | "
        f"Sources: {report['total_sources']} | "
        f"Verified findings: {report['total_verified_findings']}",
        "",
    ]

    lines += render_comparison_lines(report)

    for competitor, data in report["competitor_profiles"].items():
        lines += [f"## {competitor}", "", data["overview"], ""]

        for r in data["requirements"]:
            lines.append(
                f"**{r['requirement_id']}. {r['requirement']}**  \n"
                f"_{r['status']} | {r['release_status']} | evidence: {r['evidence_quality']}_"
            )
            lines += ["", r["summary"], ""]
            if r["status"] != "fulfilled" and r["missing_reason"]:
                lines += [f"Gap: {r['missing_reason']}", ""]
            for src in r["sources"]:
                tag = "first-party" if src["first_party"] else "third-party"
                lines.append(f"- [{src['title'] or src['url']}]({src['url']}) ({tag})")
            lines.append("")

        if data["caveats"]:
            lines.append("**Caveats**")
            lines += [f"- {c}" for c in data["caveats"]]
            lines.append("")

    unresolved = report.get("remaining_unresolved_requirements", [])
    if unresolved:
        lines += ["## Unresolved requirements", ""]
        for m in unresolved:
            lines.append(f"- [{m['competitor']}] {m['requirement_id']}: {m['missing_reason']}")
        lines.append("")

    return "\n".join(lines)


def final_report_node(state: CompetitorState):
    print("\n" + "=" * 80)
    print("FINAL REPORT")
    print("=" * 80)

    profiles = {}
    for competitor in state["competitors"]:
        print(f"Consolidating profile: {competitor}")
        profiles[competitor] = consolidate_competitor(state, competitor)

    final_report = {
        "agent": "competitor_intelligence",
        "target_company": state["company"],
        "research_goal": state["research_goal"],
        "competitors": state["competitors"],
        "research_stages_completed": state.get("research_stage", 1),
        "queries_executed": len(state.get("executed_queries", [])),
        "total_sources": len(state.get("search_results", [])),
        "total_verified_findings": len(state.get("verified_findings", [])),
        "dynamic_research_requirements": state.get("research_requirements", []),
        "competitor_profiles": profiles,
        "remaining_unresolved_requirements": state.get("missing_information", []),
    }

    if INCLUDE_RAW_FINDINGS:
        raw = {c: [] for c in state["competitors"]}
        for f in state.get("verified_findings", []):
            raw.setdefault(f["competitor"], []).append(
                {
                    "requirement_id": f.get("requirement_id"),
                    "finding": f["finding"],
                    "business_relevance": f.get("business_relevance", ""),
                    "evidence_urls": [e["source_url"] for e in f.get("evidence", [])],
                }
            )
        final_report["findings_by_competitor"] = raw

    output = {"final_report": final_report}
    save_node_output("final_report", output, state.get("research_stage", 1))

    md_path = OUTPUT_DIR / "final_report.md"
    md_path.write_text(render_markdown(final_report), encoding="utf-8")
    print(f"Saved markdown report -> {md_path}")

    return output


# ============================================================
# NODE 9: COMPARE TARGET COMPANY WITH COMPETITORS
# ============================================================

def comparison_node(state: CompetitorState):
    print("\n" + "=" * 80)
    print("COMPARISON")
    print("=" * 80)

    report = state["final_report"]
    company = state["company"]
    competitors = state["competitors"]
    profiles = report.get("competitor_profiles", {})

    compact = {}
    req_index = {}
    for c, prof in profiles.items():
        compact[c] = {
            "overview": prof["overview"],
            "requirements": [
                {
                    k: r[k]
                    for k in (
                        "requirement_id",
                        "requirement",
                        "status",
                        "release_status",
                        "evidence_quality",
                        "summary",
                    )
                }
                for r in prof["requirements"]
            ],
            "caveats": prof["caveats"],
        }
        for r in prof["requirements"]:
            req_index[(c, r["requirement_id"])] = r

    prompt = comparison_prompt(state, compact)

    result = call_structured(prompt, ComparisonReport, strong=True)

    if result is None:
        report["comparison"] = {"error": "comparison call failed (no structured output)"}
        output = {"final_report": report}
        save_node_output("final_report", output, state.get("research_stage", 1))
        (OUTPUT_DIR / "final_report.md").write_text(render_markdown(report), encoding="utf-8")
        return output

    canonical = {c.lower(): c for c in competitors}
    dims = []

    for d in result.dimensions:
        assessments: Dict[str, Dict[str, Any]] = {}

        for a in d.assessments:
            comp = canonical.get(a.competitor.strip().lower())
            if not comp or comp in assessments:
                continue

            rids = [
                rid
                for rid in dict.fromkeys(x.strip() for x in a.requirement_ids)
                if (comp, rid) in req_index
            ]
            cited = [req_index[(comp, rid)] for rid in rids]

            relative, confidence = a.relative_to_target, a.confidence

            if not cited or all(r["evidence_quality"] == "none" for r in cited):
                relative, confidence = "not_established", "low"
            else:
                qualities = [r["evidence_quality"] for r in cited]
                if all(q == "first_party" for q in qualities):
                    cap = "high"
                elif any(q == "first_party" for q in qualities):
                    cap = "medium"
                else:
                    cap = "low"
                confidence = min(confidence, cap, key=CONFIDENCE_RANK.get)

            urls = []
            for r in cited:
                for src in r["sources"]:
                    if src["url"] not in urls:
                        urls.append(src["url"])

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
                    "relative_to_target": "not_established",
                    "confidence": "low",
                    "summary": "Not assessed for this dimension.",
                    "requirement_ids": [],
                    "sources": [],
                }

        dims.append(
            {
                "dimension": d.dimension,
                "target_position": d.target_position.strip(),
                "assessments": [assessments[c] for c in competitors],
                "takeaway": d.takeaway.strip(),
            }
        )

    limitations = list(result.limitations)
    third_party_only = sum(
        1 for (_, _), r in req_index.items() if r["evidence_quality"] == "third_party_only"
    )
    if third_party_only:
        limitations.append(
            f"{third_party_only} competitor requirement(s) rest on third-party sources only; "
            "confidence on those comparisons is capped."
        )
    unresolved = len(report.get("remaining_unresolved_requirements", []))
    if unresolved:
        limitations.append(f"{unresolved} requirement(s) remained unresolved after research.")

    report["comparison"] = {
        "executive_summary": result.executive_summary.strip(),
        "dimensions": dims,
        "areas_where_target_appears_ahead": result.areas_where_target_appears_ahead,
        "areas_where_competitors_appear_ahead": result.areas_where_competitors_appear_ahead,
        "limitations": list(dict.fromkeys(limitations)),
    }

    print(f"Comparison built: {len(dims)} dimensions x {len(competitors)} competitors")

    output = {"final_report": report}
    save_node_output("final_report", output, state.get("research_stage", 1))
    (OUTPUT_DIR / "final_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Saved markdown report -> {OUTPUT_DIR / 'final_report.md'}")

    return output
