"""Find a company's competitors when the user has not named them (or wants more).

Steps, all bounded:
  1. Three or four web searches built from the company profile: "<company> competitors",
     "<offering> alternatives <market>", "<industry> companies in <market>".
  2. One model call reads the result snippets and names candidate companies, each with a reason
     and the website the snippets show. The model may only name companies that appear in the
     snippets; it never adds names from memory.
  3. Each candidate's website is checked: the homepage must load and its title or domain must
     match the name. Candidates whose site cannot be confirmed are kept only if several
     independent results named them, and with lower confidence.
  4. Official social accounts are read from each confirmed homepage.

Used by onboarding ("Find competitors for me"), by plan drafting when no competitors were given,
and by the competitor worker itself as a fallback.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel, Field

from edrak.agents.competitor_intelligence.socials import USER_AGENT, find_official_socials, homepage_text
from edrak.agents.competitor_intelligence.sources import compact, domain_of, host_of
from edrak.core.action_log import log_action
from edrak.core.llm import get_structured, provider_key, schema_instruction
from edrak.observability import set_tool_provider, traced_tool

logger = logging.getLogger(__name__)

MAX_QUERIES = 4
RESULTS_PER_QUERY = 6


class CompetitorDiscoveryConfigurationError(RuntimeError):
    """Raised when web search or the competitor judge is not configured."""


class CompetitorDiscoveryServiceError(RuntimeError):
    """Raised when a configured search or model service fails."""


class Candidate(BaseModel):
    name: str = Field(description="The competitor company's name, as the sources write it.")
    website: Optional[str] = Field(default=None, description="Its official website if a source shows it, else null.")
    reason: str = Field(description="One sentence: what it offers that overlaps with the company, from the sources.")
    source_numbers: list[int] = Field(default_factory=list, description="Numbers of the search results that name it.")


class Candidates(BaseModel):
    competitors: list[Candidate] = Field(default_factory=list)


def _queries(company: str, offerings: list[str], industry: Optional[str], markets: list[str],
             description: Optional[str]) -> list[str]:
    market = markets[0] if markets else ""
    offering = offerings[0] if offerings else (industry or "")
    queries = [f"{company} competitors"]
    if offering:
        queries.append(f"{offering} alternatives {market}".strip())
        queries.append(f"top {offering} companies {market}".strip())
    elif description:
        queries.append(" ".join(description.split()[:8]) + " companies")
    if industry and market:
        queries.append(f"{industry} startups {market}")
    seen: list[str] = []
    for q in queries:
        if q.lower() not in {s.lower() for s in seen}:
            seen.append(q)
    return seen[:MAX_QUERIES]


@traced_tool("competitor_web_search")
def web_search(query: str, max_results: int = RESULTS_PER_QUERY) -> list[dict[str, str]]:
    """Tavily first, Serper as a fallback. Returns [{title, url, content}]."""
    key, tavily_variable = provider_key("TAVILY_API_KEY", "competitor")
    tavily_error: Exception | None = None
    if key:
        try:
            from tavily import TavilyClient

            set_tool_provider("tavily")
            response = TavilyClient(api_key=key).search(query=query, search_depth="basic", max_results=max_results)
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                for r in response.get("results", [])
            ]
        except Exception as exc:  # noqa: BLE001 - fall through to Serper
            tavily_error = exc
            logger.info("Tavily search failed for %r: %s", query, exc)
    key, serper_variable = provider_key("SERPER_API_KEY", "competitor")
    if not key:
        if tavily_error is not None:
            raise CompetitorDiscoveryServiceError(
                "Tavily competitor search failed and Serper is not configured."
            ) from tavily_error
        raise CompetitorDiscoveryConfigurationError(
            "Competitor search needs TAVILY_API_KEY or SERPER_API_KEY in the project .env."
        )

    set_tool_provider("serper", fallback=tavily_error is not None)
    try:
        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=15,
        )
        if resp.status_code != 200:
            raise CompetitorDiscoveryServiceError(
                f"Serper competitor search returned HTTP {resp.status_code}."
            )
        return [
            {"title": r.get("title", ""), "url": r.get("link", ""), "content": r.get("snippet", "")}
            for r in resp.json().get("organic", [])
        ]
    except httpx.HTTPError as exc:
        logger.info("Serper search failed for %r using %s: %s", query, serper_variable, exc)
        if tavily_error is not None:
            raise CompetitorDiscoveryServiceError(
                "Tavily and Serper competitor search requests failed."
            ) from exc
        raise CompetitorDiscoveryServiceError("Serper competitor search request failed.") from exc


def _prompt(company: str, profile_lines: str, results: list[dict[str, str]], exclude: list[str], limit: int) -> str:
    listing = "\n\n".join(
        f"[{i}] {r['title']}\nURL: {r['url']}\n{(r['content'] or '')[:600]}" for i, r in enumerate(results, start=1)
    )
    return f"""You find the competitors of a company from web search results.

THE COMPANY
{profile_lines}

SEARCH RESULTS
{listing}

RULES
- Name only companies that appear in the search results above. Never add names from memory.
- A competitor is another company that sells a similar offering to similar customers. A product
  of {company} itself is not a competitor; neither is a directory, a media site or a review site.
- Do not name these, they are already known or are the company itself: {", ".join(exclude) or "(none)"}.
- `website`: only when a result shows the company's own site (its URL or a link in the text).
- `reason`: one plain sentence from the results on what it offers that overlaps.
- `source_numbers`: the [numbers] of the results that name it.
- At most {limit} competitors, the most relevant first. Return an empty list if none are clear.
"""


_GENERIC = {
    "with", "from", "that", "this", "your", "their", "company", "companies", "platform", "solution",
    "solutions", "services", "service", "online", "digital", "business", "businesses", "best", "leading",
}


def relevance_terms(offerings: list[str], industry: Optional[str], description: Optional[str]) -> set[str]:
    """Words that a competitor's homepage should share with the company's business."""
    text = " ".join([*offerings, industry or "", description or ""]).lower()
    words = {w for w in re.findall(r"[a-z\u0600-\u06ff]{4,}", text) if w not in _GENERIC}
    # Also the stem, so "payment" matches "payments" and "invoicing" matches "invoice".
    return words | {w[:5] for w in words if len(w) > 6}


def _site_matches(name: str, site: str, client: httpx.Client, terms: set[str]) -> bool:
    """The site loads, carries the company's name, and is in the same line of business.

    An exact name-to-domain match (fawry.com for Fawry) is enough. A looser one (khaznadatacenters.com
    for Khazna) must also show the company's business words on its homepage, which is what keeps a
    same-name company in another industry, or a person's site, out.
    """
    host = domain_of(site)
    key = compact(name)
    if not host or len(key) < 3:
        return False
    page = homepage_text(site, client=client)
    if page is None:
        return False
    label = compact(host.split(".")[0])
    exact = label == key or label in (f"{key}app", f"get{key}", f"{key}hq", f"{key}eg")
    if not (exact or key in compact(page[:300]) or key[:6] in label):
        return False
    if not terms:
        return True
    shares_business = any(t in page for t in terms)
    return shares_business if not exact else (shares_business or key in compact(page[:300]))


def _find_site(name: str, market: str, hint: str = "") -> Optional[str]:
    """The company's own site from one search: the first result whose domain carries its name."""
    key = compact(name)[:6]
    for r in web_search(f"{name} {hint} official website {market}".replace("  ", " ").strip(), max_results=5):
        host = host_of(r["url"])
        if key and key in compact(host.split(".")[0] if host.count(".") else host):
            return f"https://{host}"
    return None


def _confirm(candidate: dict[str, Any], client: httpx.Client, market: str, terms: set[str], hint: str) -> dict[str, Any]:
    """Find and check the website, then read the official socials from it."""
    name = candidate["name"]
    site = candidate.get("website")
    if site and not _site_matches(name, site, client, terms):
        site = None
    if not site:
        found = _find_site(name, market, hint)
        site = found if found and _site_matches(name, found, client, terms) else None
    candidate["website"] = f"https://{domain_of(site)}" if site else None
    candidate["website_confirmed"] = bool(site)
    candidate["links"] = find_official_socials(candidate["website"], client=client) if site else []
    return candidate


def discover_competitors(
    *,
    company: str,
    description: Optional[str] = None,
    offerings: Iterable[str] = (),
    industry: Optional[str] = None,
    markets: Iterable[str] = (),
    target_customers: Optional[str] = None,
    exclude: Iterable[str] = (),
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Competitors found on the web, each {name, website, reason, confidence, links, evidence_urls}."""
    offerings, markets = list(offerings), list(markets)
    tavily_key, _ = provider_key("TAVILY_API_KEY", "competitor")
    serper_key, _ = provider_key("SERPER_API_KEY", "competitor")
    if not tavily_key and not serper_key:
        raise CompetitorDiscoveryConfigurationError(
            "Competitor search needs TAVILY_API_KEY or SERPER_API_KEY in the project .env."
        )
    model_key, _ = provider_key("OLLAMA_API_KEY", "competitor")
    if not model_key:
        raise CompetitorDiscoveryConfigurationError(
            "Competitor discovery needs OLLAMA_API_KEY (or OLLAMA_API_KEY_COMPETITOR) "
            "to evaluate web results."
        )
    exclude_list = [company, *[e for e in exclude if e]]
    log_action("competitor_intelligence", f"looking for competitors of {company}")
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for query in _queries(company, offerings, industry, markets, description):
        log_action("competitor_intelligence", f"searching: {query}")
        for r in web_search(query):
            if r["url"] and r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                results.append(r)
    if not results:
        return []

    profile_lines = "\n".join(
        line for line in (
            f"Name: {company}",
            f"What it does: {description}" if description else "",
            f"Offerings: {', '.join(offerings)}" if offerings else "",
            f"Industry: {industry}" if industry else "",
            f"Markets: {', '.join(markets)}" if markets else "",
            f"Target customers: {target_customers}" if target_customers else "",
        ) if line
    )
    try:
        reply = get_structured(Candidates, agent="competitor", temperature=0).invoke(
            [_prompt(company, profile_lines, results, exclude_list, limit), schema_instruction(Candidates)]
        )
    except Exception as exc:  # noqa: BLE001 - convert model failures to a user-visible discovery error
        logger.warning("Competitor discovery failed: %s", exc)
        raise CompetitorDiscoveryServiceError(
            "Could not evaluate competitor search results. Check the configured competitor model."
        ) from exc
    if reply is None:
        return []

    excluded = {compact(e) for e in exclude_list}
    candidates: list[dict[str, Any]] = []
    for c in reply.competitors:
        name = c.name.strip()
        if not name or compact(name) in excluded or compact(name) in {compact(x["name"]) for x in candidates}:
            continue
        sources = [results[n - 1] for n in c.source_numbers if 1 <= n <= len(results)]
        # The name must really appear in the cited results: a guard against invented names.
        named_in = [r for r in sources if compact(name) in compact(f"{r['title']} {r['content']}")] or [
            r for r in results if compact(name) in compact(f"{r['title']} {r['content']}")
        ]
        if not named_in:
            continue
        website = c.website
        if not website:
            own = next((r["url"] for r in results if compact(name)[:6] and compact(name)[:6] in compact(host_of(r["url"]))), None)
            website = f"https://{host_of(own)}" if own else None
        candidates.append({
            "name": name,
            "website": website,
            "reason": c.reason.strip(),
            "evidence_urls": [r["url"] for r in named_in[:3]],
            "mentions": len(named_in),
        })
        if len(candidates) >= limit:
            break

    with httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            market = markets[0] if markets else ""
            terms = relevance_terms(offerings, industry, description)
            hint = offerings[0] if offerings else (industry or "")
            confirmed = list(pool.map(lambda cand: _confirm(cand, client, market, terms, hint), candidates))

    out: list[dict[str, Any]] = []
    for c in confirmed:
        # Every candidate is named in at least one result. A confirmed site and more mentions raise
        # the confidence; the person reviews the list before anything uses it.
        confidence = 0.45 + (0.25 if c["website_confirmed"] else 0) + min(0.15, 0.05 * (c["mentions"] - 1))
        out.append({
            "name": c["name"],
            "website": c["website"],
            "reason": c["reason"],
            "confidence": round(min(confidence, 0.9), 2),
            "links": c["links"],
            "evidence_urls": c["evidence_urls"],
        })
    log_action("competitor_intelligence", f"found {len(out)} competitor(s): {', '.join(c['name'] for c in out) or 'none'}")
    return out
