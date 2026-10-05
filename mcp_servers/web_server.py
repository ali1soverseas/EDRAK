"""MCP server for web search, page fetch, and external data sources."""

from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP

from mcp_servers.web.scrapers import (
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

mcp = FastMCP("edrak-web")


@mcp.tool()
def tool_newsapi(queries: List[str]) -> str:
    """Search NewsAPI for recent English-language news articles."""
    items, _ = fetch_newsapi(queries)
    return serialize_items(items)


@mcp.tool()
def tool_gdelt(queries: List[str]) -> str:
    """Fetch GDELT news-volume timelines for each query."""
    items, _ = fetch_gdelt(queries)
    return serialize_items(items)


@mcp.tool()
def tool_hacker_news(queries: List[str]) -> str:
    """Search Hacker News for tech and startup community stories."""
    items, _ = fetch_hn_algolia(queries)
    return serialize_items(items)


@mcp.tool()
def tool_dbnomics(queries: List[str]) -> str:
    """Search DBnomics for macroeconomic datasets."""
    items, _ = fetch_dbnomics(queries)
    return serialize_items(items)


@mcp.tool()
def tool_wikidata(queries: List[str]) -> str:
    """Search Wikidata for structured entity information."""
    items, _ = fetch_wikidata(queries)
    return serialize_items(items)


@mcp.tool()
def tool_arxiv(queries: List[str]) -> str:
    """Fetch the latest academic papers from arXiv matching each query."""
    items, _ = fetch_arxiv(queries)
    return serialize_items(items)


@mcp.tool()
def tool_openalex(queries: List[str]) -> str:
    """Fetch open-access academic works and publication-year trends from OpenAlex."""
    items, _ = fetch_openalex(queries)
    return serialize_items(items)


@mcp.tool()
def tool_worldbank(dummy: str = "") -> str:
    """Fetch World Bank macro indicators using the default indicator set."""
    items, _ = fetch_worldbank()
    return serialize_items(items)


@mcp.tool()
def tool_fred(series_ids: List[str]) -> str:
    """Fetch FRED time-series observations for the given series IDs."""
    items, _ = fetch_fred(series_ids)
    return serialize_items(items)


@mcp.tool()
def tool_imf(indicator_codes: List[str]) -> str:
    """Fetch IMF DataMapper time series using IMF indicator codes."""
    items, _ = fetch_imf_datamapper(indicator_codes)
    return serialize_items(items)


@mcp.tool()
def tool_eurostat(dataset_codes: List[str]) -> str:
    """Fetch Eurostat time-series data using dataset codes."""
    items, _ = fetch_eurostat(dataset_codes)
    return serialize_items(items)


@mcp.tool()
def tool_alphavantage(tickers: List[str]) -> str:
    """Fetch company overview data from Alpha Vantage for the given tickers."""
    items, _ = fetch_alphavantage(tickers)
    return serialize_items(items)


@mcp.tool()
def tool_finnhub(tickers: List[str]) -> str:
    """Fetch company profile data from Finnhub for the given tickers."""
    items, _ = fetch_finnhub(tickers)
    return serialize_items(items)


@mcp.tool()
def tool_sec_edgar(cik_numbers: List[str]) -> str:
    """Fetch annual revenue facts from SEC EDGAR XBRL for the given CIK numbers."""
    items, _ = fetch_sec_edgar(cik_numbers)
    return serialize_items(items)


@mcp.tool()
def tool_serper(queries: List[str]) -> str:
    """Google Search via the Serper API."""
    items, _ = fetch_serper(queries)
    return serialize_items(items)


@mcp.tool()
def tool_web_search(queries: List[str]) -> str:
    """Free web search via DuckDuckGo."""
    items, _ = fetch_web_search(queries)
    return serialize_items(items)


@mcp.tool()
def tool_scrape_url(url: str) -> str:
    """Fetch and extract readable text from a public web page."""
    return scrape_url_content(url)


if __name__ == "__main__":
    mcp.run()
