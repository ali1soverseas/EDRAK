"""External web and data-source fetchers used by the web MCP server."""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

TIMEOUT = 10
MAX_QUERIES = 10
COUNTRY_ISO3 = "EGY"
START_YEAR = 2018
EUROSTAT_FILTERS = {"geo": "EA", "freq": "M", "coicop": "CP00", "unit": "I15"}

WORLDBANK_INDICATORS = {
    "household_consumption_usd": "NE.CON.PRVT.CD",
    "household_consumption_growth": "NE.CON.PRVT.KD.ZG",
    "gdp_growth_pct": "NY.GDP.MKTP.KD.ZG",
    "gdp_per_capita_usd": "NY.GDP.PCAP.CD",
    "inflation_cpi_pct": "FP.CPI.TOTL.ZG",
    "population": "SP.POP.TOTL",
    "internet_users_pct": "IT.NET.USER.ZS",
    "services_pct_of_gdp": "NV.SRV.TOTL.ZS",
    "trade_pct_of_gdp": "NE.TRD.GNFS.ZS",
    "exchange_rate_lcu_per_usd": "PA.NUS.FCRF",
}

UA = os.getenv("SCRAPER_USER_AGENT", "edrak-web/1.0")
HEADERS = {"User-Agent": UA}
DDGS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def get(url, params=None, headers=None, label=""):
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    for attempt in range(2):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=TIMEOUT)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP '{label}' ({type(exc).__name__})")
            return None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(3)
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP '{label}' (status {resp.status_code})")
            return None
        return resp
    return None


def fetch_newsapi(queries):
    api_key = _env("NEWS_API_KEY")
    if not api_key:
        print("[SCRAPER]  SKIP NewsAPI: NEWS_API_KEY is not set")
        return [], []

    print(f"[SCRAPER_NEWSAPI] Fetching NewsAPI articles for {len(queries)} queries...")
    all_articles = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "pageSize": 5,
            "language": "en",
            "sortBy": "publishedAt",
            "apiKey": api_key,
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[SCRAPER_NEWSAPI] SKIP query '{query}' (status {resp.status_code})")
            continue
        articles = resp.json()
        articles["query_used"] = query
        articles["method"] = "newsapi"
        all_articles.append(articles)
        queries_run.append(query)

    print(
        f"[SCRAPER_NEWSAPI] OK {len(all_articles)} responses "
        f"fetched from {len(queries_run)} queries"
    )
    return all_articles, queries_run


def fetch_hn_algolia(queries):
    print(f"[SCRAPER_HN_ALGOLIA] Fetching Hacker News stories for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://hn.algolia.com/api/v1/search"
        params = {"query": query, "tags": "story", "hitsPerPage": 50}
        resp = get(url, params, label=query)
        if resp is None:
            continue
        response = resp.json()
        response["query_used"] = query
        response["method"] = "hn_algolia"
        all_items.append(response)
        queries_run.append(query)

    print(
        f"[SCRAPER_HN_ALGOLIA] OK {len(all_items)} responses "
        f"fetched from {len(queries_run)} queries"
    )
    return all_items, queries_run


def fetch_gdelt(queries):
    print(f"[SCRAPER]  Fetching GDELT timelines for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for i, query in enumerate(queries[:MAX_QUERIES]):
        if i > 0:
            time.sleep(6)
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {"query": query, "mode": "timelinevol", "format": "json", "timespan": "12months"}
        resp = None
        for attempt in range(2):
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
                break
            except requests.RequestException as exc:
                print(f"[SCRAPER]  RETRY query '{query}' ({type(exc).__name__}, attempt {attempt + 1}/2)")
                time.sleep(6)
        if resp is None:
            print(f"[SCRAPER]  SKIP query '{query}' (no response)")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        try:
            data = resp.json()
        except ValueError:
            print(f"[SCRAPER]  SKIP query '{query}' (non-JSON: {resp.text[:80]!r})")
            continue
        data["query_used"] = query
        data["method"] = "gdelt"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} timeline points from {len(queries_run)} queries")
    return all_items, queries_run


def fetch_openalex(queries):
    print(f"[SCRAPER]  Fetching OpenAlex data for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://api.openalex.org/works"
        extra = {"mailto": os.environ["OPENALEX_MAILTO"]} if _env("OPENALEX_MAILTO") else {}
        meta = {}
        results = []
        group_by = []

        params = {"search": query, "group_by": "publication_year", **extra}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f"[SCRAPER]  SKIP year counts for '{query}' (status {resp.status_code})")
            else:
                data = resp.json()
                meta = data.get("meta", {})
                group_by = sorted(data.get("group_by", []), key=lambda g: str(g.get("key")))
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP year counts for '{query}' ({type(exc).__name__})")

        params = {"search": query, "per_page": 25, **extra}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f"[SCRAPER]  SKIP works for '{query}' (status {resp.status_code})")
            else:
                data = resp.json()
                meta = data.get("meta", meta)
                results = data.get("results", [])
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP works for '{query}' ({type(exc).__name__})")

        if results or group_by:
            all_items.append({
                "query_used": query,
                "method": "openalex",
                "meta": meta,
                "results": results,
                "group_by": group_by,
            })
            queries_run.append(query)

    n_works = sum(len(item["results"]) for item in all_items)
    n_buckets = sum(len(item["group_by"]) for item in all_items)
    print(f"[SCRAPER]  OK {n_works} works, {n_buckets} year buckets from {len(queries_run)} queries")
    return all_items, queries_run


def fetch_arxiv(queries):
    print(f"[SCRAPER]  Fetching arXiv papers for {len(queries)} queries...")
    all_items = []
    queries_run = []
    ns = "{http://www.w3.org/2005/Atom}"

    for i, query in enumerate(queries[:MAX_QUERIES]):
        if i > 0:
            time.sleep(3)
        url = "https://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "max_results": 25,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            print(f"[SCRAPER]  SKIP query '{query}' (invalid XML)")
            continue
        for entry_el in root.findall(f"{ns}entry"):
            entry = {child.tag.split("}", 1)[-1]: child.text for child in entry_el}
            entry["authors"] = [a.findtext(f"{ns}name") for a in entry_el.findall(f"{ns}author")]
            entry["links"] = [
                {"href": lnk.get("href"), "rel": lnk.get("rel"), "type": lnk.get("type")}
                for lnk in entry_el.findall(f"{ns}link")
            ]
            entry["query_used"] = query
            entry["method"] = "arxiv"
            all_items.append(entry)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} papers fetched from {len(queries_run)} queries")
    return all_items, queries_run


def fetch_wikidata(queries):
    print(f"[SCRAPER]  Fetching Wikidata entities for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbsearchentities",
            "search": query,
            "language": "en",
            "format": "json",
            "limit": 20,
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        data["query_used"] = query
        data["method"] = "wikidata"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} entities fetched from {len(queries_run)} queries")
    return all_items, queries_run


def fetch_imf_datamapper(queries):
    print(f"[SCRAPER]  Fetching IMF DataMapper series for {len(queries)} indicators...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = f"https://www.imf.org/external/datamapper/api/v1/{query}/{COUNTRY_ISO3}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        data["query_used"] = query
        data["method"] = "imf_datamapper"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} data points from {len(queries_run)} indicators")
    return all_items, queries_run


def fetch_eurostat(queries):
    print(f"[SCRAPER]  Fetching Eurostat datasets for {len(queries)} codes...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{query}"
        params = {"format": "JSON", "lang": "EN", **EUROSTAT_FILTERS}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        if "dimension" not in data or "time" not in data.get("dimension", {}):
            print(f"[SCRAPER]  SKIP query '{query}' (no time dimension; adjust EUROSTAT_FILTERS)")
            continue
        data["query_used"] = query
        data["method"] = "eurostat"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} data points from {len(queries_run)} datasets")
    return all_items, queries_run


def fetch_sec_edgar(queries):
    print(f"[SCRAPER]  Fetching SEC EDGAR revenue facts for {len(queries)} companies...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(query).zfill(10)}.json"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        time.sleep(0.2)
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        data["query_used"] = query
        data["method"] = "sec_edgar"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} revenue facts from {len(queries_run)} companies")
    return all_items, queries_run


def fetch_fred(queries):
    api_key = _env("FRED_API_KEY")
    if not api_key:
        print("[SCRAPER]  SKIP FRED: FRED_API_KEY is not set")
        return [], []

    print(f"[SCRAPER]  Fetching FRED series for {len(queries)} ids...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": query,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": f"{START_YEAR}-01-01",
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        data["query_used"] = query
        data["method"] = "fred"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} observations from {len(queries_run)} series")
    return all_items, queries_run


def fetch_alphavantage(queries):
    api_key = _env("ALPHAVANTAGE_API_KEY")
    if not api_key:
        print("[SCRAPER]  SKIP Alpha Vantage: ALPHAVANTAGE_API_KEY is not set")
        return [], []

    print(f"[SCRAPER]  Fetching Alpha Vantage overviews for {len(queries)} tickers...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://www.alphavantage.co/query"
        params = {"function": "OVERVIEW", "symbol": query, "apikey": api_key}
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        if "Symbol" not in data:
            print(
                f"[SCRAPER]  SKIP query '{query}' "
                f"({(data.get('Note') or data.get('Information') or 'no data')[:80]})"
            )
            continue
        data["query_used"] = query
        data["method"] = "alphavantage"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} company overviews from {len(queries_run)} tickers")
    return all_items, queries_run


def fetch_finnhub(queries):
    api_key = _env("FINNHUB_API_KEY")
    if not api_key:
        print("[SCRAPER]  SKIP Finnhub: FINNHUB_API_KEY is not set")
        return [], []

    print(f"[SCRAPER]  Fetching Finnhub profiles for {len(queries)} tickers...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://finnhub.io/api/v1/stock/profile2"
        params = {"symbol": query, "token": api_key}
        try:
            resp = requests.get(url, params=params, timeout=10)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        if not data:
            print(f"[SCRAPER]  SKIP query '{query}' (empty profile)")
            continue
        data["query_used"] = query
        data["method"] = "finnhub"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} profiles from {len(queries_run)} tickers")
    return all_items, queries_run


def fetch_worldbank(indicators=None):
    indicators = indicators or WORLDBANK_INDICATORS
    print(f"[SCRAPER]  Fetching World Bank indicators for {len(indicators)} codes ({COUNTRY_ISO3})...")
    all_items = []
    queries_run = []

    for name, indicator in list(indicators.items())[:MAX_QUERIES]:
        url = f"https://api.worldbank.org/v2/country/{COUNTRY_ISO3}/indicator/{indicator}"
        params = {"format": "json", "date": f"{START_YEAR}:{datetime.now().year}", "per_page": 100}
        resp = None
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
            except requests.RequestException as exc:
                print(f"[SCRAPER]  RETRY '{name}' ({type(exc).__name__}, attempt {attempt + 1}/3)")
                resp = None
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                print(f"[SCRAPER]  RETRY '{name}' (status {resp.status_code}, attempt {attempt + 1}/3)")
                time.sleep(5 * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200:
            print(f"[SCRAPER]  SKIP '{name}' (status {getattr(resp, 'status_code', 'no response')})")
            continue
        data = resp.json()
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            print(f"[SCRAPER]  SKIP '{name}' (no data returned; the indicator code may be invalid)")
            continue
        result = {"meta": data[0], "data": data[1], "query_used": name, "method": "worldbank"}
        all_items.append(result)
        queries_run.append(name)

    print(f"[SCRAPER]  OK {len(all_items)} data points from {len(queries_run)} indicators")
    return all_items, queries_run


def fetch_dbnomics(queries):
    print(f"[SCRAPER]  Fetching DBnomics datasets for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://api.db.nomics.world/v22/search"
        params = {"q": query, "limit": 20}
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__})")
            continue
        if resp.status_code != 200:
            print(f"[SCRAPER]  SKIP query '{query}' (status {resp.status_code})")
            continue
        data = resp.json()
        data["query_used"] = query
        data["method"] = "dbnomics"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} datasets fetched from {len(queries_run)} queries")
    return all_items, queries_run


def fetch_serper(queries):
    api_key = _env("SERPER_API_KEY")
    if not api_key:
        print("[SCRAPER]  SKIP Serper: SERPER_API_KEY is not set")
        return [], []

    print(f"[SCRAPER]  Fetching Serper search results for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                data=json.dumps({"q": query, "num": 10}),
                timeout=30,
            )
            data = resp.json()
        except Exception as exc:
            print(f"[SCRAPER]  SKIP query '{query}' ({type(exc).__name__}: {exc})")
            continue
        data["query_used"] = query
        data["method"] = "serper"
        all_items.append(data)
        queries_run.append(query)

    print(f"[SCRAPER]  OK {len(all_items)} results from {len(queries_run)} Serper queries")
    return all_items, queries_run


def _unwrap_ddg_url(href: str) -> str:
    if href.startswith("//duckduckgo.com/l/"):
        qs = parse_qs(urlparse("https:" + href).query)
        return qs.get("uddg", [href])[0]
    return href


def _parse_ddg_html_results(html: str, query: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    results = soup.select(".result") or soup.select(".web-result")
    for result in results[:10]:
        title_el = result.select_one(".result__title a") or result.select_one("a.result__a")
        snippet_el = result.select_one(".result__snippet")
        if not title_el:
            continue
        items.append({
            "title": title_el.get_text(strip=True),
            "url": _unwrap_ddg_url(title_el.get("href", "")),
            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            "query_used": query,
            "method": "web_search",
        })
    if items:
        return items

    for link in soup.select("a.result-link")[:10]:
        href = _unwrap_ddg_url(link.get("href", ""))
        snippet_el = link.find_parent("tr")
        snippet = ""
        if snippet_el is not None:
            next_row = snippet_el.find_next_sibling("tr")
            if next_row is not None:
                snippet = next_row.get_text(" ", strip=True)
        items.append({
            "title": link.get_text(strip=True),
            "url": href,
            "snippet": snippet,
            "query_used": query,
            "method": "web_search",
        })
    return items


def _request_with_retry(method: str, url: str, *, label: str, retries: int = 3, **kwargs):
    """Retry 202/429/5xx. DuckDuckGo uses 202 for bot-challenge / throttle pages."""
    resp = None
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, timeout=15, **kwargs)
        except requests.RequestException as exc:
            print(f"[SCRAPER]  RETRY {label} ({type(exc).__name__}, attempt {attempt + 1}/{retries})")
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (202, 429, 500, 502, 503, 504) and attempt < retries - 1:
            wait = 2 ** attempt
            print(
                f"[SCRAPER]  {label} status {resp.status_code}; "
                f"retry in {wait}s ({attempt + 1}/{retries})"
            )
            time.sleep(wait)
            continue
        return resp
    return resp


def _fetch_wikipedia_search(query: str) -> list[dict]:
    """Key-free fallback when DuckDuckGo answers 202 instead of results."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 8,
                "format": "json",
                "utf8": 1,
            },
            headers=HEADERS,
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"[SCRAPER]  SKIP wikipedia '{query}' ({type(exc).__name__})")
        return []
    if resp.status_code != 200:
        print(f"[SCRAPER]  SKIP wikipedia '{query}' (status {resp.status_code})")
        return []
    hits = resp.json().get("query", {}).get("search", [])
    items = []
    for hit in hits:
        title = hit.get("title") or ""
        if not title:
            continue
        page = title.replace(" ", "_")
        items.append({
            "title": title,
            "url": f"https://en.wikipedia.org/wiki/{page}",
            "snippet": BeautifulSoup(hit.get("snippet", ""), "html.parser").get_text(" ", strip=True),
            "query_used": query,
            "method": "web_search",
        })
    return items


def fetch_web_search(queries):
    print(f"[SCRAPER]  Fetching web search results for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for i, query in enumerate(queries[:MAX_QUERIES]):
        if i > 0:
            time.sleep(2)

        items = []
        resp = _request_with_retry(
            "POST",
            "https://html.duckduckgo.com/html/",
            label=f"html.duckduckgo.com '{query[:40]}'",
            data={"q": query, "kl": "us-en"},
            headers=DDGS_HEADERS,
        )
        if resp is not None and resp.status_code == 200:
            items = _parse_ddg_html_results(resp.text, query)
        elif resp is not None:
            print(f"[SCRAPER]  html.duckduckgo.com finished with status {resp.status_code}; trying lite")

        if not items:
            lite = _request_with_retry(
                "GET",
                "https://lite.duckduckgo.com/lite/",
                label=f"lite.duckduckgo.com '{query[:40]}'",
                params={"q": query},
                headers=DDGS_HEADERS,
            )
            if lite is not None and lite.status_code == 200:
                items = _parse_ddg_html_results(lite.text, query)
            elif lite is not None:
                print(f"[SCRAPER]  lite.duckduckgo.com finished with status {lite.status_code}")

        if not items:
            print(f"[SCRAPER]  DuckDuckGo challenged; falling back to Wikipedia for '{query[:50]}'")
            items = _fetch_wikipedia_search(query)

        if items:
            all_items.extend(items)
            queries_run.append(query)
        print(f"[SCRAPER]  [{query[:50]}] {len(items)} results")

    print(f"[SCRAPER]  OK {len(all_items)} results from {len(queries_run)} queries")
    return all_items, queries_run


def scrape_url_content(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    if response.status_code != 200:
        return f"Error: Failed to retrieve page (Status {response.status_code})"
    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup(["script", "style"]):
        script.extract()
    return soup.get_text(separator="\n", strip=True)


def serialize_items(items: list) -> str:
    raw = json.dumps(items[:50], ensure_ascii=False)
    if len(raw) > 40_000:
        raw = raw[:40_000] + "\n... [truncated]"
    return raw
