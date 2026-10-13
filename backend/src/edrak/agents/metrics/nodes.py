"""The metrics agent: the only stage that produces typed numbers.

1. grid     - every (metric, entity) the planner asked for, from the tasks' checklists.
2. harvest  - read the text the other workers already saved (full page text included) and point at
              each grid metric. Customer sentiment and share of voice come from stance labels the
              customer worker saved, counted here in code.
3. check    - found, missing, or found only in a third-party source.
4. hunt     - for what is missing, search with the registry's query hints, the entity's own pages
              first, fetch the page and point again. At most MAX_HUNTS per cell.
5. finish   - outcomes with a reason for every gap, our public figures tagged "public proxy for
              internal", then reconcile (every number checked against its saved source).

A model only points at spans of text. Code parses every value, checks every span, and does all
counting. Nothing here writes prose.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Optional
from urllib.parse import urlparse

from edrak.briefing.extract import Candidates, to_observation
from edrak.contracts import Evidence, WorkerResult, WorkerStatus, WorkerType
from edrak.contracts.evidence import SourceType
from edrak.core.action_log import log_action
from edrak.guardrails.content import sanitize
from edrak.metrics import requirements as req
from edrak.metrics.contracts import (
    GapReason,
    MetricObservation,
    MetricRequirement,
    MetricScope,
    OutcomeStatus,
    RequirementOutcome,
)
from edrak.metrics.registry import MetricDef, get_registry

from .prompts import render
from .state import Cell, MetricsState

logger = logging.getLogger(__name__)

AGENT = "metrics"
MAX_HUNTS = 3  # targeted searches per (metric, entity) after the harvest
WINDOW = 600  # characters each side of a metric mention
MAX_WINDOWS = 16  # passages per model call
MAX_HUNT_CELLS = 20  # cells hunted per round, required ones first
PAGES_PER_HUNT = 3
PARALLEL = 4
_STANCES = ("positive", "neutral", "negative", "mixed")
_OFFICIAL_TYPES = {SourceType.PRICING_PAGE, SourceType.OFFICIAL_DOCUMENTATION, SourceType.RELEASE_NOTES, SourceType.ANNOUNCEMENT}

# Seams for tests and for the network: (prompt) -> Candidates | None, (query) -> [{url,title,snippet}], (url) -> text.
Finder = Callable[[str], Optional[Candidates]]
Searcher = Callable[[str], list[dict[str, str]]]
Fetcher = Callable[[str], str]


# ---------------------------------------------------------------- helpers


def cell_key(metric_id: str, entity: str) -> str:
    return f"{metric_id}|{entity.strip().casefold()}"


def _token(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _host(url: Optional[str]) -> str:
    return (urlparse(url or "").hostname or "").lower()


def is_own_site(url: Optional[str], entity: str) -> bool:
    tok = _token(entity)
    return len(tok) >= 4 and tok in _host(url).replace("-", "").replace(".", "")


def _text(e: Evidence) -> str:
    return e.page_text or f"{e.extracted_fact or ''}\n{e.excerpt or ''}"


def _default_finder(prompt: str) -> Optional[Candidates]:
    from edrak.core.llm import get_structured, schema_instruction

    model = get_structured(Candidates, agent="orchestrator", temperature=0)
    return model.invoke([prompt, schema_instruction(Candidates)])


def _default_search(query: str) -> list[dict[str, str]]:
    from edrak.mcp.paths import ensure_repo_on_path

    ensure_repo_on_path()
    from mcp_servers.web.scrapers import fetch_serper, fetch_web_search

    out: list[dict[str, str]] = []
    try:
        batches, _ = fetch_serper([query])
        for b in batches:
            for r in b.get("organic", []) if isinstance(b, dict) else []:
                out.append({"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Serper search failed: %s", exc)
    if not out:
        try:
            items, _ = fetch_web_search([query])
            out = [{"url": i.get("url", ""), "title": i.get("title", ""), "snippet": i.get("snippet", "")} for i in items]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily search failed: %s", exc)
    return [r for r in out if r.get("url", "").startswith("http")]


def _default_fetch(url: str) -> str:
    from edrak.mcp.paths import ensure_repo_on_path

    ensure_repo_on_path()
    from mcp_servers.web.scrapers import scrape_url_content

    try:
        text = scrape_url_content(url) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetch failed for %s: %s", url, exc)
        return ""
    return "" if text.startswith("Error:") else text


_seams: dict[str, Any] = {"find": _default_finder, "search": _default_search, "fetch": _default_fetch}


def use(find: Optional[Finder] = None, search: Optional[Searcher] = None, fetch: Optional[Fetcher] = None) -> None:
    """Swap the model, search or fetch (tests). Passing nothing restores the defaults."""
    _seams["find"] = find or _default_finder
    _seams["search"] = search or _default_search
    _seams["fetch"] = fetch or _default_fetch


# ---------------------------------------------------------------- what a source is about

# Stock quote, trading and investor-sentiment pages. Their numbers are prices and trader moods, never a market,
# a customer or a product figure, so no metric reads them.
_TICKER_HOSTS = ("stocktwits.com", "robinhood.com", "tradingview.com", "marketbeat.com", "altindex.com", "diplyzer.com",
                 "etoro.com", "investing.com", "macrotrends.net", "stockanalysis.com", "zacks.com", "nasdaq.com",
                 "marketwatch.com", "fool.com", "seekingalpha.com", "simplywall.st", "tikr.com", "perplexity.ai")
_TICKER_PATHS = re.compile(r"/(?:quote|quotes|symbol|stocks?|equities|ticker|markets/stocks)/", re.I)
# Pages that explain a metric rather than report one.
_EXPLAINER = re.compile(r"^(?:what is|what's|how to|how do|need to know|guide to|definition)\b", re.I)
_TOPIC_FILLER = {"market", "markets", "emerging", "global", "the", "and", "for", "of", "in", "new", "solutions", "services"}


def is_noise(url: Optional[str], title: Optional[str] = None) -> bool:
    host = _host(url)
    if any(host == h or host.endswith("." + h) for h in _TICKER_HOSTS):
        return True
    if _TICKER_PATHS.search(urlparse(url or "").path or ""):
        return True
    return False


_REPORTS = re.compile(r"\b(?:size|forecast|report|statistics|outlook|market)\b", re.I)


def is_explainer(title: Optional[str]) -> bool:
    """"What is share of voice?" explains a metric; "What is the sovereign AI market size in 2026?" reports one."""
    t = (title or "").strip()
    return bool(_EXPLAINER.match(t)) and not _REPORTS.search(t)


def topic_words(topic: str) -> list[str]:
    """The words that show a passage is about the topic: "Sovereign AI infrastructure" -> sovereign, infrastructure, ai."""
    words = [w for w in re.findall(r"[a-z0-9]+", (topic or "").lower()) if w not in _TOPIC_FILLER]
    return [w for w in words if len(w) >= 4 or w in {"ai", "ev", "iot", "gpu", "llm", "5g"}]


def on_topic(text: str, words: list[str]) -> bool:
    """True when no topic is known, or the text names at least one topic word (whole words only)."""
    if not words:
        return True
    low = text.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)


# Metrics about a market: a passage must be about the topic, not just the place.
_TOPIC_BOUND = {"market_intelligence"}


# ---------------------------------------------------------------- windows and pointing


# Words that sit next to a figure of each kind, so "the Team plan costs $19" is found for a price.
_UNIT_HINTS = {
    "currency": ["$", "€", "£", "usd", "egp", "sar", "aed", "price", "cost", "per month", "/mo"],
    "percent": ["%", "percent"],
    "count": ["customers", "users", "subscribers", "merchants"],
    "index": ["index", "score", "rating"],
}


def _terms(defn: MetricDef) -> list[str]:
    phrases = [defn.name.get("en", ""), defn.id.replace("_", " "), *defn.aliases]
    words = [w for p in phrases for w in p.split() if len(w) >= 5]
    found = [*phrases, *words, *_UNIT_HINTS.get(defn.unit_kind, [])]
    return [w.lower() for w in dict.fromkeys(w for w in found if w and (len(w) >= 3 or not w.isalnum()))]


# What a figure of each kind looks like, so a passage holding "$245 billion" beats one that only says "market".
_UNIT_RX = {
    "currency": re.compile(r"[$€£]\s?\d|\d[\d,.]*\s?(?:usd|egp|sar|aed|eur|gbp|billion|million|bn|mn)\b|\b(?:usd|egp|sar|aed)\s?\d", re.I),
    "percent": re.compile(r"\d\s?%|\d\s?percent", re.I),
    "count": re.compile(r"\d[\d,.]*\s?(?:k|m|million|thousand)?\+?\s*(?:customers|users|subscribers|merchants|employees|engineers|people)", re.I),
    "index": re.compile(r"\d(?:\.\d)?\s?(?:/\s?\d+|out of \d+|stars?)", re.I),
    "date": re.compile(r"\b(?:19|20)\d\d\b|\bq[1-4]\b", re.I),
}


def _best_chunks(text: str, terms: list[str], unit_rx: Optional[re.Pattern], limit: int = 6) -> list[tuple[int, str]]:
    """The passages most likely to state the figure.

    The text is cut into overlapping tiles. A tile scores by how many of the metric's terms it holds, plus a bonus when a
    figure of the right shape (a currency amount, a percentage) sits in it. Common words such as "market" can no longer
    use up the slots, and a long page can no longer collapse into one giant passage.
    """
    if not text:
        return []
    size, step = 2 * WINDOW, WINDOW
    low = text.lower()
    tiles: list[tuple[int, int, str]] = []
    for a in range(0, max(1, len(text) - WINDOW), step):
        chunk = text[a:a + size]
        if not re.search(r"\d", chunk):
            continue
        hits = sum(1 for t in terms if t in low[a:a + size])
        if not hits:
            continue
        tiles.append((hits + (4 if unit_rx is not None and unit_rx.search(chunk) else 0), a, chunk))
    tiles.sort(key=lambda x: (-x[0], x[1]))
    picked: list[tuple[int, int, str]] = []
    for tile in tiles:
        if all(abs(tile[1] - p[1]) >= size for p in picked):
            picked.append(tile)
        if len(picked) >= limit:
            break
    return [(score, chunk) for score, _, chunk in picked]


def windows_for(defn: MetricDef, entities: list[str], evidence: list[Evidence], topic: str = "") -> list[tuple[Evidence, str]]:
    """Passages that hold a digit, mention the metric and concern one of the entities.

    The entity's own pages come first, then passages that name the entity; within those, the passages that look most
    like the figure being asked for. Stock quote pages and explainers are never read, and a market figure must come
    from a passage (or a page title) about the topic, not only the place.
    """
    terms = _terms(defn)
    unit_rx = _UNIT_RX.get(defn.unit_kind)
    words = topic_words(topic) if defn.owner in _TOPIC_BOUND else []
    scored: list[tuple[int, int, Evidence, str]] = []
    for ev in evidence:
        if ev.metadata.get("computed_by") != "code" and (is_noise(ev.source_url, ev.source_title) or is_explainer(ev.source_title)):
            continue
        text = _text(ev)
        about = " ".join([ev.source_title or "", ev.source_url or "", str(ev.metadata.get("competitor", ""))]).lower()
        own = any(is_own_site(ev.source_url, e) for e in entities)
        for quality, chunk in _best_chunks(text, terms, unit_rx):
            named = any(e.lower() in chunk.lower() or e.lower() in about for e in entities)
            if not named and not own:
                continue
            if not on_topic(f"{ev.source_title or ''} {chunk}", words):
                continue
            scored.append((2 * own + named, quality, ev, chunk))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [(ev, chunk) for _, _, ev, chunk in scored[:MAX_WINDOWS]]


def point(defn: MetricDef, entities: list[str], windows: list[tuple[Evidence, str]], topic: str = "") -> list[MetricObservation]:
    """One model call: which spans state this metric. Code checks each span and parses each value."""
    if not windows:
        return []
    passages = "\n\n".join(f"[{ev.evidence_id}] {ev.source_title or ''} ({ev.source_url or ev.publisher or ''})\n{sanitize(chunk)}"
                           for ev, chunk in windows)
    prompt = render(metric_id=defn.id, definition=defn.definition, aliases=", ".join(defn.aliases) or "none",
                    entities=", ".join(entities), passages=passages, topic=topic or "the company's offering")
    try:
        got = _seams["find"](prompt)
    except Exception as exc:  # noqa: BLE001 - a failed call is a gap, never a guess
        logger.info("Metric pointing failed for %s: %s", defn.id, exc)
        return []
    by_id = {ev.evidence_id: ev for ev, _ in windows}
    canon = {e.casefold(): e for e in entities}
    out: list[MetricObservation] = []
    seen: set[tuple] = set()
    for c in (got.metrics if got else []):
        ev = by_id.get(c.evidence_id)
        name = canon.get((c.entity or "").strip().casefold())
        if ev is None or name is None:
            continue
        c.metric_id, c.entity = defn.id, name
        obs = to_observation(c, ev, allowed={defn.id})
        if obs is None:
            continue
        key = (obs.entity.casefold(), obs.value, obs.label, obs.evidence_id)
        if key not in seen:
            seen.add(key)
            out.append(obs)
    return out


# ---------------------------------------------------------------- stance counts (customer sentiment)


def _pct(n: int, total: int) -> str:
    return f"{round(100 * n / total, 1)}%"


def stance_metrics(result: WorkerResult, wanted: set[str]) -> tuple[list[Evidence], list[MetricObservation]]:
    """Sentiment split and share of voice from the customer worker's stance labels, counted in code.

    Each tally is written into a small evidence record so every number traces to its source text.
    """
    counts: dict[str, dict[str, int]] = result.metadata.get("stance_counts") or {}
    counts = {e: c for e, c in counts.items() if int(c.get("total", 0)) > 0}
    evidence: list[Evidence] = []
    obs: list[MetricObservation] = []
    if not counts:
        return evidence, obs
    if "sentiment_split" in wanted:
        for entity, c in counts.items():
            total = int(c["total"])
            parts = [(s, int(c.get(s, 0))) for s in _STANCES]
            fact = (f"Customer items about {entity} labelled by stance: {total}. "
                    + ", ".join(f"{s.capitalize()} {n} ({_pct(n, total)})" for s, n in parts) + ".")
            ev = Evidence(evidence_id=f"stance-{_token(entity)[:40] or 'entity'}", source_type=SourceType.OTHER,
                          source_title=f"Customer stance count for {entity}", publisher="Edrak customer analysis",
                          extracted_fact=fact, excerpt=fact,
                          metadata={"computed_by": "code", "method": "stance_count", "entity": entity, "counts": c})
            evidence.append(ev)
            for s, n in parts:
                span = f"{s.capitalize()} {n} ({_pct(n, total)})"
                obs.append(MetricObservation(
                    metric_id="sentiment_split", entity=entity, value_text=_pct(n, total), value=round(100 * n / total, 1),
                    unit="%", label=s, evidence_id=ev.evidence_id, source_span=span, sample_size=total,
                    definition="Share of labelled customer items about this company with each stance.",
                    formula=f"items labelled {s} toward {entity} ÷ labelled items about {entity}",
                ))
    if "share_of_voice" in wanted and len(counts) > 1:
        grand = sum(int(c["total"]) for c in counts.values())
        fact = (f"Labelled customer items that mention a tracked company: {grand}. "
                + ", ".join(f"{e} {int(c['total'])} ({_pct(int(c['total']), grand)})" for e, c in counts.items()) + ".")
        ev = Evidence(evidence_id="stance-share-of-voice", source_type=SourceType.OTHER, source_title="Share of customer mentions",
                      publisher="Edrak customer analysis", extracted_fact=fact, excerpt=fact,
                      metadata={"computed_by": "code", "method": "stance_count"})
        evidence.append(ev)
        for e, c in counts.items():
            n = int(c["total"])
            obs.append(MetricObservation(
                metric_id="share_of_voice", entity=e, value_text=_pct(n, grand), value=round(100 * n / grand, 1), unit="%",
                evidence_id=ev.evidence_id, source_span=f"{e} {n} ({_pct(n, grand)})", sample_size=grand,
                definition="Share of labelled customer items that mention each tracked company.",
                formula=f"items about {e} ÷ items about any tracked company",
            ))
    return evidence, obs


# ---------------------------------------------------------------- graph nodes


def build_grid(state: MetricsState) -> MetricsState:
    grid: list[Cell] = []
    seen: set[str] = set()
    for t in state.get("tasks") or []:
        for r in t.requirements:
            k = cell_key(r.metric_id, r.entity)
            if k not in seen:
                seen.add(k)
                grid.append({"task_id": t.task_id, "requirement": r})
    topic = topic_of(state)
    log_action(AGENT, f"grid: {len(grid)} figures for {len({c['requirement'].entity for c in grid})} entities, about {topic or 'no named topic'}")
    return {"grid": grid, "observations": {}, "hunts": {}, "read_official": [], "log": [], "topic": topic}


def topic_of(state: MetricsState) -> str:
    """What the analysis is about, for searches and relevance checks: the offering the planner settled on."""
    for t in state.get("tasks") or []:
        offering = (getattr(t.business_context, "offering", None) or "").strip()
        if offering:
            return offering
    request = state.get("request")
    return ((request.business_context.offering or "") if request else "").strip()


def harvest(state: MetricsState) -> MetricsState:
    reg = get_registry()
    results = list(state.get("results") or [])
    grid = state.get("grid") or []
    by_metric: dict[str, list[str]] = {}
    for c in grid:
        by_metric.setdefault(c["requirement"].metric_id, [])
        if c["requirement"].entity not in by_metric[c["requirement"].metric_id]:
            by_metric[c["requirement"].metric_id].append(c["requirement"].entity)
    evidence_owner = {e.evidence_id: r.task_id for r in results for e in r.evidence}
    all_evidence = [e for r in results for e in r.evidence]
    found: dict[str, list[MetricObservation]] = {}

    # Customer figures counted from stance labels: never from a model.
    counted = {"sentiment_split", "share_of_voice"}
    for r in results:
        if r.worker is WorkerType.CUSTOMER_TRENDS:
            evs, obs = stance_metrics(r, set(by_metric) & counted)
            if evs:
                results = [x if x.task_id != r.task_id else x.model_copy(update={"evidence": [*x.evidence, *[e for e in evs if e.evidence_id not in {y.evidence_id for y in x.evidence}]]}) for x in results]
                found.setdefault(r.task_id, []).extend(obs)

    jobs = [(reg.get(m), ents) for m, ents in by_metric.items() if m not in counted and reg.get(m)]
    topic = state.get("topic") or topic_of(state)

    def run(job: tuple[MetricDef, list[str]]) -> list[MetricObservation]:
        defn, ents = job
        return point(defn, ents, windows_for(defn, ents, all_evidence, topic), topic)

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        for obs_list in pool.map(run, jobs):
            for o in obs_list:
                owner = evidence_owner.get(o.evidence_id)
                if owner:
                    found.setdefault(owner, []).append(o)
    n = sum(len(v) for v in found.values())
    log_action(AGENT, f"harvest: {n} figures found in the sources the workers saved")
    return {"results": results, "observations": found}


def _cell_outcome(cell: Cell, state: MetricsState) -> RequirementOutcome:
    """found / retry / gap for one cell, using the registry's check rule (requirements.check)."""
    r: MetricRequirement = cell["requirement"]
    results = {x.task_id: x for x in state.get("results") or []}
    mine = [o for obs in (state.get("observations") or {}).values() for o in obs
            if o.metric_id == r.metric_id and o.entity.casefold() == r.entity.casefold()]
    owner = results.get(cell["task_id"])
    meta: dict[str, Any] = {"metrics": [o.model_dump(mode="json") for o in mine]}
    customer = next((x for x in results.values() if x.worker is WorkerType.CUSTOMER_TRENDS), None)
    if customer is not None:
        meta["control_summary"] = customer.metadata.get("control_summary") or {}
    pseudo = WorkerResult(task_id=cell["task_id"], worker=owner.worker if owner else WorkerType.COMPETITOR_INTELLIGENCE,
                          status=WorkerStatus.COMPLETED, attempt=1, metadata=meta)
    return req.check(_task_stub(cell, state), pseudo)[0]


def _task_stub(cell: Cell, state: MetricsState):
    task = next(t for t in state.get("tasks") or [] if t.task_id == cell["task_id"])
    return task.model_copy(update={"requirements": [cell["requirement"]], "attempt": 1})


def huntable(defn: Optional[MetricDef]) -> bool:
    """False for figures that are never web-searched.

    Figures computed from the customer worker's own data (stance counts, trend series) are never web-searched:
    a search for "AMD sentiment" finds trader moods, and "NVIDIA search interest" finds stock-ticker trend pages."""
    if defn is None or not defn.query_hints or defn.id in {"sentiment_split", "share_of_voice", "search_interest"}:
        return False
    # Our own figures live in our documents. A web search for "NVIDIA headcount" returns an employee-statistics blog,
    # which is not the capacity this initiative has.
    return defn.owner != "internal_intelligence"


def missing_cells(state: MetricsState) -> list[tuple[Cell, RequirementOutcome]]:
    out = []
    reg = get_registry()
    for c in state.get("grid") or []:
        if not huntable(reg.get(c["requirement"].metric_id)):
            continue
        o = _cell_outcome(c, state)
        if o.status is not OutcomeStatus.FOUND and o.reason in (GapReason.NOT_FOUND, GapReason.ONLY_THIRD_PARTY):
            out.append((c, o))
    return out


def _market(request: Any) -> str:
    bc = getattr(request, "business_context", None) if request else None
    if bc is not None:
        markets = getattr(bc, "markets", None)
        if isinstance(markets, list) and markets:
            return ", ".join(str(m).strip() for m in markets if str(m).strip())
        for name in ("target_market", "market", "region", "country", "geography"):
            v = getattr(bc, name, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _clean(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\{[a-z_]+\}", "", q)).strip()


def _with_topic(q: str, topic: str, defn: MetricDef) -> str:
    """Market and competitor searches name the topic, so "Latin America market size" cannot return retail reports."""
    if not topic or defn.owner == "internal_intelligence" or topic.casefold() in q.casefold():
        return q
    return f"{q} {topic}" if defn.owner != "market_intelligence" else f"{topic} {q}"


def _queries(r: MetricRequirement, defn: MetricDef, offering: str, attempt: int, market: str = "") -> list[str]:
    name = defn.name.get("en", defn.id)
    registry_hints = list(getattr(defn, "query_hints", None) or [])
    bare_registry = bool(registry_hints) and not any("{" in h for h in registry_hints)
    out: list[str] = []
    for h in r.query_hints:
        h = h.strip()
        if not h:
            continue
        filled = h.replace("{offering}", offering).replace("{entity}", r.entity).replace("{market}", market)
        # A bare hint such as "capacity plan" or "headcount" means nothing to a web search without the company.
        if "{" not in h and r.entity.casefold() not in h.casefold() and (bare_registry or len(h.split()) <= 3):
            filled = f"{r.entity} {h}"
        out.append(_clean(filled))
    if not out:
        out = [_clean(f"{r.entity} {name}")]
    if attempt == 2:  # the second hunt widens: the metric's other names (never a short acronym such as "tam")
        out = [_clean(f"{r.entity} {a}") for a in defn.aliases if len(a) >= 5][:2] or out
    elif attempt >= 3:  # the third asks for a recent report on the figure
        out = [_clean(f"{r.entity} {name} {date.today().year}"), _clean(f"{r.entity} {name} report")]
    out = [_clean(_with_topic(q, offering, defn)) for q in out]
    return list(dict.fromkeys(q for q in out if q))[:2]


def hunt(state: MetricsState) -> MetricsState:
    """Targeted search for what the harvest missed. The entity's own pages are read first."""
    reg = get_registry()
    request = state.get("request")
    offering = state.get("topic") or topic_of(state)
    todo = [(c, o) for c, o in missing_cells(state) if (state.get("hunts") or {}).get(cell_key(c["requirement"].metric_id, c["requirement"].entity), 0) < MAX_HUNTS]
    todo.sort(key=lambda co: not co[0]["requirement"].required)
    todo = todo[:MAX_HUNT_CELLS]
    hunts = dict(state.get("hunts") or {})
    read_official = set(state.get("read_official") or [])
    results = {r.task_id: r for r in state.get("results") or []}
    seen_urls = {e.source_url for r in results.values() for e in r.evidence if e.source_url}
    found = {k: list(v) for k, v in (state.get("observations") or {}).items()}

    market = _market(request)

    def one(cell: Cell) -> tuple[Cell, list[Evidence], list[MetricObservation], bool, dict[str, Any]]:
        r = cell["requirement"]
        defn = reg.get(r.metric_id)
        key = cell_key(r.metric_id, r.entity)
        attempt = hunts.get(key, 0) + 1
        queries = _queries(r, defn, offering, attempt, market)
        hits: list[dict[str, str]] = []
        for q in queries:
            hits.extend(_seams["search"](q))
        official_first = "official" in (r.preferred_source_types or defn.preferred_sources)
        hits.sort(key=lambda h: not is_own_site(h.get("url"), r.entity) if official_first else 0)
        evs: list[Evidence] = []
        own = False
        pages = snippets = 0
        for h in hits:
            url = h.get("url", "")
            if not url or url in seen_urls or any(e.source_url == url for e in evs):
                continue
            if is_noise(url, h.get("title")) or is_explainer(h.get("title")):
                continue
            text = _seams["fetch"](url)
            from_snippet = False
            if not text.strip():
                # Paywalled or blocked page (market-research sites often are): the search result itself may state the figure.
                snippet = f"{h.get('title') or ''}. {h.get('snippet') or ''}".strip(" .")
                if not re.search(r"\d", snippet):
                    continue
                text, from_snippet = snippet, True
                snippets += 1
            else:
                pages += 1
            official = is_own_site(url, r.entity)
            own = own or official
            st = SourceType.PRICING_PAGE if official and "pric" in url.lower() else (SourceType.OFFICIAL_DOCUMENTATION if official else SourceType.WEB_PAGE)
            evs.append(Evidence(source_type=st, source_title=h.get("title") or None, source_url=url, publisher=_host(url) or None,
                                extracted_fact=(h.get("snippet") or text[:300]).strip() or "Page fetched by the metrics agent.",
                                excerpt=text[:1500], page_text=text,
                                metadata={"found_by": AGENT, "metric_id": r.metric_id, "competitor": r.entity, "first_party": official,
                                          "snippet_only": from_snippet}))
            if len(evs) >= PAGES_PER_HUNT:
                break
        obs = point(defn, [r.entity], windows_for(defn, [r.entity], evs, offering), offering) if evs else []
        diag = {"queries": queries, "results": len(hits), "pages": pages, "snippets": snippets}
        return cell, evs, obs, own, diag

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        done = list(pool.map(one, [c for c, _ in todo]))
    added = 0
    for cell, evs, obs, own, diag in done:
        key = cell_key(cell["requirement"].metric_id, cell["requirement"].entity)
        hunts[key] = hunts.get(key, 0) + 1
        log_action(AGENT, f"hunt {key} try {hunts[key]}: {len(diag['queries'])} queries {diag['queries']}, {diag['results']} results, "
                          f"{diag['pages']} pages read, {diag['snippets']} from snippets, {len(obs)} figures")
        if own:
            read_official.add(key)
        owner = results.get(cell["task_id"])
        if owner is None or not evs:
            continue
        results[owner.task_id] = owner.model_copy(update={"evidence": [*owner.evidence, *evs]})
        seen_urls.update(e.source_url for e in evs if e.source_url)
        found.setdefault(owner.task_id, []).extend(obs)
        added += len(obs)
    if done and not any(d[4]["results"] for d in done):
        log_action(AGENT, "hunt: the web search returned nothing for every query; check the search provider keys and network")
    log_action(AGENT, f"hunt: searched for {len(todo)} missing figures, found {added}")
    return {"results": list(results.values()), "observations": found, "hunts": hunts, "read_official": sorted(read_official)}


def route_after_check(state: MetricsState) -> str:
    hunts = state.get("hunts") or {}
    pending = [c for c, _ in missing_cells(state)
               if hunts.get(cell_key(c["requirement"].metric_id, c["requirement"].entity), 0) < MAX_HUNTS]
    return "hunt" if pending else "finish"


def finish(state: MetricsState) -> MetricsState:
    """Outcomes with reasons, the public-proxy tag, then every number checked against its source."""
    from edrak.briefing.reconcile import reconcile

    request = state.get("request")
    company = (request.company_profile.name if request else "").strip().casefold()
    hunts = state.get("hunts") or {}
    read_official = set(state.get("read_official") or [])
    observations = state.get("observations") or {}
    outcomes: dict[str, list[RequirementOutcome]] = {}
    for c in state.get("grid") or []:
        o = _cell_outcome(c, state)
        key = cell_key(c["requirement"].metric_id, c["requirement"].entity)
        attempts = 1 + hunts.get(key, 0)
        if o.status is not OutcomeStatus.FOUND:
            reason = o.reason
            if reason is GapReason.NOT_FOUND and key in read_official:
                reason = GapReason.NOT_DISCLOSED  # we read the entity's own pages and it is not there
            o = o.model_copy(update={"status": OutcomeStatus.GAP, "reason": reason})
        outcomes.setdefault(c["task_id"], []).append(o.model_copy(update={"attempts": attempts}))

    fixed: list[WorkerResult] = []
    for r in state.get("results") or []:
        mine = []
        for o in observations.get(r.task_id, []):
            if company and o.entity.strip().casefold() == company and o.scope is MetricScope.OFFICIAL:
                o = o.model_copy(update={"scope": MetricScope.PUBLIC_PROXY})  # our own public figure stands in for internal data
            mine.append(o.model_copy(update={"status": None}))
        meta = dict(r.metadata)
        meta["metrics"] = [o.model_dump(mode="json") for o in mine]
        task = next((t for t in state.get("tasks") or [] if t.task_id == r.task_id), None)
        if task is not None:
            meta["requirements"] = [q.model_dump(mode="json") for q in task.requirements]
            meta["requirement_outcomes"] = [o.model_dump(mode="json") for o in outcomes.get(r.task_id, [])]
        fixed.append(r.model_copy(update={"metadata": meta}))
    fixed, conflicts = reconcile(fixed)
    gaps = sum(1 for v in outcomes.values() for o in v if o.status is OutcomeStatus.GAP)
    log_action(AGENT, f"done: {sum(len(v) for v in outcomes.values()) - gaps} figures found, {gaps} gaps, {len(conflicts)} conflicts")
    return {"results": fixed, "conflicts": conflicts}
