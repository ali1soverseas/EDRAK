"""LangChain tool adapters over the shared web MCP capabilities."""

from __future__ import annotations

import json
from typing import List

from langchain_core.tools import tool

from edrak.mcp.paths import ensure_repo_on_path

ensure_repo_on_path()

from mcp_servers.web.scrapers import (  # noqa: E402
    fetch_alphavantage,
    fetch_arxiv,
    fetch_dbnomics,
    fetch_eurostat,
    fetch_finnhub,
    fetch_fred,
    fetch_gdelt,
    fetch_hn_algolia,
    fetch_imf_datamapper,
    fetch_newsapi,
    fetch_openalex,
    fetch_sec_edgar,
    fetch_serper,
    fetch_web_search,
    fetch_wikidata,
    fetch_worldbank,
    scrape_url_content,
    serialize_items,
)


@tool
def tool_newsapi(queries: List[str]) -> str:
    """
    Search NewsAPI for recent English-language news articles.

    Use for: breaking news, company announcements, economic events, market sentiment.
    queries: list of free-text keyword strings, e.g. ["Amazon Egypt e-commerce 2025", "retail growth MENA"].
    Returns: JSON array of articles (title, description, source, published_at, url).
    """
    items, _ = fetch_newsapi(queries)
    return serialize_items(items)


@tool
def tool_gdelt(queries: List[str]) -> str:
    """
    Fetch GDELT news-volume timelines (last 12 months) for each query.

    Use for: measuring media coverage intensity and trend direction over time.
    queries: list of free-text keyword strings, e.g. ["Egypt inflation consumer", "e-commerce MENA"].
    Returns: JSON array of (date, volume_pct, query_used) data points.
    Note: GDELT is slow (~60 s per query); limit to 1-2 queries.
    """
    items, _ = fetch_gdelt(queries)
    return serialize_items(items)


@tool
def tool_hacker_news(queries: List[str]) -> str:
    """
    Search Hacker News (via Algolia) for tech and startup community stories.

    Use for: technology trends, startup ecosystem signals, developer and investor sentiment.
    queries: list of free-text keyword strings, e.g. ["fintech Egypt", "e-commerce emerging markets"].
    Returns: JSON array of stories (title, url, points, num_comments, published_at).
    """
    items, _ = fetch_hn_algolia(queries)
    return serialize_items(items)


@tool
def tool_dbnomics(queries: List[str]) -> str:
    """
    Search DBnomics for macroeconomic datasets (IMF, OECD, Eurostat, national stats offices).

    Use for: discovering statistical series; useful when you need dataset codes for other tools.
    queries: list of free-text keyword strings, e.g. ["Egypt GDP consumption", "MENA retail trade"].
    Returns: JSON array of dataset metadata (provider, dataset_code, name, nb_series).
    """
    items, _ = fetch_dbnomics(queries)
    return serialize_items(items)


@tool
def tool_wikidata(queries: List[str]) -> str:
    """
    Search Wikidata for structured entity information (companies, products, categories).

    Use for: entity disambiguation, company metadata, category hierarchy, concept URIs.
    queries: list of entity names or concepts, e.g. ["Amazon", "e-commerce", "Egypt"].
    Returns: JSON array of entities (id, label, description, url).
    """
    items, _ = fetch_wikidata(queries)
    return serialize_items(items)


@tool
def tool_arxiv(queries: List[str]) -> str:
    """
    Fetch the latest academic papers from arXiv matching each query.

    Use for: emerging research, technology trends, academic evidence for market hypotheses.
    queries: list of free-text keyword strings, e.g. ["fintech emerging markets", "supply chain Egypt"].
    Returns: JSON array of papers (title, summary, authors, published_at, arxiv_id).
    Note: arXiv enforces ~3 s between requests; limit to 2 queries.
    """
    items, _ = fetch_arxiv(queries)
    return serialize_items(items)


@tool
def tool_openalex(queries: List[str]) -> str:
    """
    Fetch open-access academic works and publication-year trends from OpenAlex.

    Use for: academic publication volume trends, open-access research on a topic.
    queries: list of free-text keyword strings, e.g. ["MENA consumer behavior underserved", "digital economy Africa"].
    Returns: JSON array of query results, each with 'results' (works with links) and 'group_by' (pubs per year).
    """
    items, _ = fetch_openalex(queries)
    return serialize_items(items)


@tool
def tool_worldbank(dummy: str = "") -> str:
    """
    Fetch World Bank macro indicators for Egypt (EGY) using the default indicator set.

    Use for: GDP growth, household consumption, inflation, population, internet penetration,
             trade share, exchange rate -- all for Egypt since 2018.
    dummy: ignored; pass an empty string or any value.
    Returns: JSON array of (indicator_name, indicator_code, country, year, value).
    No query needed -- uses built-in Egypt indicator list.
    """
    items, _ = fetch_worldbank()
    return serialize_items(items)


@tool
def tool_fred(series_ids: List[str]) -> str:
    """
    Fetch FRED (Federal Reserve) time-series observations for the given series IDs.

    Use for: US macroeconomic benchmarks such as CPI, PPI, retail sales, interest rates.
    series_ids: list of FRED series IDs, e.g. ["CPIAUCSL", "PPIACO", "RSXFS", "FEDFUNDS"].
    Returns: JSON array of (series_id, date, value) observations from 2018 onward.
    """
    items, _ = fetch_fred(series_ids)
    return serialize_items(items)


@tool
def tool_imf(indicator_codes: List[str]) -> str:
    """
    Fetch IMF DataMapper time series for Egypt using IMF indicator codes.

    Use for: IMF-standardised GDP, inflation, current account, fiscal balance for Egypt.
    indicator_codes: list of IMF indicator codes, e.g. ["NGDPD", "PCPIPCH", "BCA_NGDPD"].
    Returns: JSON array of (country, indicator, year, value) data points.
    """
    items, _ = fetch_imf_datamapper(indicator_codes)
    return serialize_items(items)


@tool
def tool_eurostat(dataset_codes: List[str]) -> str:
    """
    Fetch Eurostat time-series data for the Euro area using dataset codes.

    Use for: Euro-area price indices, consumption, trade -- useful as global pricing benchmarks.
    dataset_codes: list of Eurostat dataset codes, e.g. ["prc_hicp_midx", "sts_trtu_m"].
    Returns: JSON array of (dataset, geo, period, value) data points.
    """
    items, _ = fetch_eurostat(dataset_codes)
    return serialize_items(items)


@tool
def tool_alphavantage(tickers: List[str]) -> str:
    """
    Fetch company overview data from Alpha Vantage for the given stock tickers.

    Use for: market cap, revenue TTM, profit margin, P/E ratio, sector, industry of public companies.
    tickers: list of stock ticker symbols, e.g. ["AMZN", "WMT", "SHOP", "JD"].
    Returns: JSON array of company overview records.
    Note: free tier allows ~25 requests/day.
    """
    items, _ = fetch_alphavantage(tickers)
    return serialize_items(items)


@tool
def tool_finnhub(tickers: List[str]) -> str:
    """
    Fetch company profile data from Finnhub for the given stock tickers.

    Use for: company country, exchange, industry classification, market cap, IPO date, website.
    tickers: list of stock ticker symbols, e.g. ["AMZN", "WMT", "NOON"].
    Returns: JSON array of company profile records.
    Note: free tier allows ~60 requests/minute.
    """
    items, _ = fetch_finnhub(tickers)
    return serialize_items(items)


@tool
def tool_sec_edgar(cik_numbers: List[str]) -> str:
    """
    Fetch annual (10-K) revenue facts from SEC EDGAR XBRL for the given company CIK numbers.

    Use for: historical annual revenue of US-listed companies from SEC filings.
    cik_numbers: list of CIK numbers as strings, e.g. ["1018724"] (Amazon), ["104169"] (Walmart).
    Returns: JSON array of (company, cik, concept, fiscal_year, period_end, value_usd, filed).
    """
    items, _ = fetch_sec_edgar(cik_numbers)
    return serialize_items(items)


@tool
def tool_serper(queries: List[str]) -> str:
    """
    Google Search via the Serper API — returns rich, up-to-date results.

    Use for: any broad web research, news, product pages, competitor info, market data.
    Prefer this over tool_web_search when you need high-quality Google results.
    queries: list of natural-language search strings, e.g. ["Amazon Egypt 2025 market share"].
    Returns: JSON array of results (title, url, snippet, position) plus any answer box.
    """
    items, _ = fetch_serper(queries)
    return serialize_items(items)


@tool
def tool_web_search(queries: List[str]) -> str:
    """
    Web search via Tavily. Requires TAVILY_API_KEY.

    Use for: general-purpose web search when Serper is unavailable or as a fallback.
    queries: list of natural-language search strings, e.g. ["Egypt e-commerce growth 2025"].
    Returns: JSON array of results (title, url, snippet).
    Note: keep queries to 2-3 so the search is not rate-limited.
    """
    items, _ = fetch_web_search(queries)
    return serialize_items(items)


def extract_urls_from_tool_result(tool_name: str, raw_json_str: str) -> list:
    """Deterministically walk known tool JSON shapes and collect http(s) URLs."""
    try:
        data = json.loads(raw_json_str)
    except (json.JSONDecodeError, TypeError):
        return []

    urls: list[str] = []

    def _add(value):
        if isinstance(value, str) and value.startswith("http") and value not in urls:
            urls.append(value)

    items = data if isinstance(data, list) else [data]

    if tool_name == "tool_newsapi":
        for batch in items:
            for article in batch.get("articles", []):
                _add(article.get("url"))
    elif tool_name == "tool_hacker_news":
        for batch in items:
            for hit in batch.get("hits", []):
                _add(hit.get("url") or hit.get("story_url"))
    elif tool_name == "tool_serper":
        for batch in items:
            for result in batch.get("organic", []):
                _add(result.get("link"))
            for sitelink in batch.get("sitelinks", []):
                _add(sitelink.get("link"))
    elif tool_name == "tool_web_search":
        for item in items:
            _add(item.get("url"))
    elif tool_name == "tool_openalex":
        for batch in items:
            for work in batch.get("results", []):
                loc = work.get("primary_location") or {}
                _add(loc.get("landing_page_url"))
                _add(loc.get("pdf_url"))
                oa = work.get("open_access") or {}
                _add(oa.get("oa_url"))
    elif tool_name == "tool_arxiv":
        for entry in items:
            for link in entry.get("links", []):
                href = link.get("href", "")
                if href.startswith("http"):
                    _add(href)
    elif tool_name in ("tool_alphavantage", "tool_finnhub"):
        for item in items:
            _add(item.get("website") or item.get("weburl"))

    return urls


ALL_SCRAPER_TOOLS = [
    tool_newsapi,
    tool_gdelt,
    tool_hacker_news,
    tool_dbnomics,
    tool_wikidata,
    tool_arxiv,
    tool_openalex,
    tool_worldbank,
    tool_fred,
    tool_imf,
    tool_eurostat,
    tool_alphavantage,
    tool_finnhub,
    tool_sec_edgar,
    tool_serper,
    tool_web_search,
]

TOOL_MAP = {tool.name: tool for tool in ALL_SCRAPER_TOOLS}

__all__ = [
    "ALL_SCRAPER_TOOLS",
    "TOOL_MAP",
    "extract_urls_from_tool_result",
    "scrape_url_content",
]
