"""External web and data-source fetchers used by the web MCP server."""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Scrapers read keys with os.getenv. Load .env from the repo root, or from
# scripts/.env when that is where the file currently lives.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _env_file in (_REPO_ROOT / ".env", _REPO_ROOT / "scripts" / ".env"):
    if _env_file.is_file():
        load_dotenv(_env_file)
        break

TIMEOUT = 10
REQUEST_DELAY_SECONDS = 2


def _get(url, **kwargs):
    time.sleep(REQUEST_DELAY_SECONDS)
    return requests.get(url, **kwargs)


def _post(url, **kwargs):
    time.sleep(REQUEST_DELAY_SECONDS)
    return requests.post(url, **kwargs)


def _request(method: str, url: str, retries: int = 2, **kwargs):
    """Search/scrape HTTP without the global 2s delay. Back off only on 429."""
    response = None
    for attempt in range(retries):
        try:
            if method == "post":
                response = requests.post(url, **kwargs)
            else:
                response = requests.get(url, **kwargs)
        except requests.RequestException:
            if attempt + 1 >= retries:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 429 and attempt + 1 < retries:
            time.sleep(2 ** (attempt + 1))
            continue
        return response
    return response


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


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _key(name: str, scope: str | None = None) -> tuple[str, str]:
      """Resolve an API key and return it with the variable it came from.

      The second element is the env var name, for logging and quota
      attribution only. A caller that logs a key must log the name, never the
      value. When a scope is given the scoped variable is preferred, so each
      worker can hold its own key for a shared provider, and the bare name
      remains the fallback.

      This mirrors edrak.core.llm.provider_key and is deliberately a separate
      copy rather than an import: this module is a standalone MCP server that
      does not depend on the backend package, so it keeps working on its own.
      The two must agree on the naming rule, so change them together.
      """
      names = [f"{name}_{scope.upper()}"] if scope else []
      names.append(name)
      for candidate in names:
          value = os.getenv(candidate, "").strip()
          if value:
              return value, candidate
      return "", names[0]


def _query_spec(item) -> dict:
    """Accept a keyword string or a planner query object."""
    if isinstance(item, dict):
        text = str(item.get("q") or item.get("query") or "").strip()
        return {**item, "q": text}
    return {"q": str(item).strip()}


_RECENCY_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def get(url, params=None, headers=None, label=""):
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    for attempt in range(2):
        try:
            resp = _get(url, params=params, headers=hdrs, timeout=TIMEOUT)
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
    api_key, key_name = _key("NEWS_API_KEY")
    if not api_key:
        print(f"[SCRAPER]  SKIP NewsAPI: {key_name} is not set")
        return [], []

    print(
            f"[SCRAPER_NEWSAPI] Fetching NewsAPI articles for {len(queries)} queries "
            f"(key: {key_name})..."
        )
    all_articles = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        spec = _query_spec(query)
        if not spec["q"]:
            continue
        language = str(spec.get("language") or "en").split("-")[0]
        if len(language) != 2:
            language = "en"
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": spec["q"],
            "pageSize": 5,
            "language": language,
            "sortBy": "publishedAt",
            "apiKey": api_key,
        }
        resp = _get(url, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[SCRAPER_NEWSAPI] SKIP query '{spec['q']}' (status {resp.status_code})")
            continue
        articles = resp.json()
        articles["query_used"] = spec["q"]
        articles["method"] = "newsapi"
        all_articles.append(articles)
        queries_run.append(spec["q"])

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

    for query in queries[:2]:
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {"query": query, "mode": "timelinevol", "format": "json", "timespan": "12months"}
        resp = None
        for attempt in range(3):
            try:
                resp = _get(url, params=params, headers=HEADERS, timeout=60)
            except requests.RequestException as exc:
                print(f"[SCRAPER]  RETRY query '{query}' ({type(exc).__name__}, attempt {attempt + 1}/3)")
                time.sleep(15)
                continue
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"[SCRAPER]  RETRY query '{query}' (status 429, waiting {wait}s, attempt {attempt + 1}/3)")
                time.sleep(wait)
                resp = None
                continue
            break
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
            resp = _get(url, params=params, headers=HEADERS, timeout=30)
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
            resp = _get(url, params=params, headers=HEADERS, timeout=30)
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
            resp = _get(url, params=params, headers=HEADERS, timeout=30)
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
            resp = _get(url, params=params, headers=HEADERS, timeout=10)
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
            resp = _get(url, headers=HEADERS, timeout=30)
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
            resp = _get(url, params=params, headers=HEADERS, timeout=30)
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
            resp = _get(url, headers=HEADERS, timeout=30)
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
    api_key, key_name = _key("FRED_API_KEY")
    if not api_key:
        print(f"[SCRAPER]  SKIP FRED: {key_name} is not set")
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
            resp = _get(url, params=params, timeout=30)
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
    api_key, key_name = _key("ALPHAVANTAGE_API_KEY")
    if not api_key:
        print(f"[SCRAPER]  SKIP Alpha Vantage: {key_name} is not set")
        return [], []

    print(
        f"[SCRAPER]  Fetching Alpha Vantage overviews for {len(queries)} tickers "
        f"(key: {key_name})..."
    )
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://www.alphavantage.co/query"
        params = {"function": "OVERVIEW", "symbol": query, "apikey": api_key}
        try:
            resp = _get(url, params=params, timeout=30)
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
    api_key, key_name = _key("FINNHUB_API_KEY")
    if not api_key:
        print(f"[SCRAPER]  SKIP Finnhub: {key_name} is not set")
        return [], []

    print(
        f"[SCRAPER]  Fetching Finnhub profiles for {len(queries)} tickers "
        f"(key: {key_name})..."
    )
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        url = "https://finnhub.io/api/v1/stock/profile2"
        params = {"symbol": query, "token": api_key}
        try:
            resp = _get(url, params=params, timeout=10)
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


def fetch_worldbank(country, indicator_codes):
    country = str(country or "").strip().upper()
    codes = [str(code).strip() for code in (indicator_codes or []) if str(code).strip()]
    if not country or not codes:
        print("[SCRAPER]  SKIP World Bank: country and indicator_codes are required")
        return [], []

    print(f"[SCRAPER]  Fetching World Bank indicators for {len(codes)} codes ({country})...")
    all_items = []
    queries_run = []

    for indicator in codes[:MAX_QUERIES]:
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
        params = {"format": "json", "date": f"{START_YEAR}:{datetime.now().year}", "per_page": 100}
        resp = None
        for attempt in range(3):
            try:
                resp = _get(url, params=params, headers=HEADERS, timeout=60)
            except requests.RequestException as exc:
                print(f"[SCRAPER]  RETRY '{indicator}' ({type(exc).__name__}, attempt {attempt + 1}/3)")
                resp = None
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                print(f"[SCRAPER]  RETRY '{indicator}' (status {resp.status_code}, attempt {attempt + 1}/3)")
                time.sleep(5 * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200:
            print(f"[SCRAPER]  SKIP '{indicator}' (status {getattr(resp, 'status_code', 'no response')})")
            continue
        data = resp.json()
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            print(f"[SCRAPER]  SKIP '{indicator}' (no data returned; the indicator code may be invalid)")
            continue
        result = {"meta": data[0], "data": data[1], "query_used": indicator, "method": "worldbank", "country": country}
        all_items.append(result)
        queries_run.append(indicator)

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
            resp = _get(url, params=params, headers=HEADERS, timeout=30)
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


def _serper_body(spec: dict, *, with_domains: bool) -> dict:
    text = spec["q"]
    domains = [str(domain) for domain in (spec.get("include_domains") or [])][:3]
    if with_domains and domains:
        sites = " OR ".join(f"site:{domain}" for domain in domains)
        text = f"{text} ({sites})"
    body = {"q": text, "num": 10}
    if spec.get("gl"):
        body["gl"] = spec["gl"]
    if spec.get("hl"):
        body["hl"] = spec["hl"]
    tbs = _RECENCY_TBS.get(str(spec.get("recency") or ""))
    if tbs:
        body["tbs"] = tbs
    return body


def _annotate_serper_scores(data: dict) -> None:
    for index, result in enumerate(data.get("organic") or [], 1):
        if not isinstance(result, dict):
            continue
        result.setdefault("position", index)
        if not isinstance(result.get("score"), (int, float)):
            result["score"] = round(max(0.05, 1 - (index - 1) * 0.08), 3)


def fetch_serper(queries):
    api_key, key_name = _key("SERPER_API_KEY", "market")
    if not api_key:
        print(f"[SCRAPER]  SKIP Serper: {key_name} is not set")
        return [], []

    print(
        f"[SCRAPER]  Fetching Serper search results for {len(queries)} queries "
        f"(key: {key_name})..."
    )
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        spec = _query_spec(query)
        if not spec["q"]:
            continue
        data = None
        for with_domains in (True, False):
            if not with_domains and not spec.get("include_domains"):
                break
            try:
                resp = _request(
                    "post",
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    data=json.dumps(_serper_body(spec, with_domains=with_domains)),
                    timeout=30,
                )
                data = resp.json() if resp is not None else {}
            except Exception as exc:
                print(f"[SCRAPER]  SKIP query '{spec['q']}' ({type(exc).__name__}: {exc})")
                data = None
                break
            if data.get("organic") or not with_domains or not spec.get("include_domains"):
                break
            print(f"[SCRAPER]  No Serper hits in include_domains for '{spec['q'][:50]}'; retrying without them")
        if not isinstance(data, dict):
            continue
        _annotate_serper_scores(data)
        data["query_used"] = spec["q"]
        data["method"] = "serper"
        all_items.append(data)
        queries_run.append(spec["q"])

    print(f"[SCRAPER]  OK {len(all_items)} results from {len(queries_run)} Serper queries")
    return all_items, queries_run


def _tavily_body(spec: dict, api_key: str, *, with_domains: bool) -> dict:
    body = {
        "api_key": api_key,
        "query": spec["q"],
        "search_depth": "basic",
        "max_results": 8,
        "include_answer": False,
    }
    recency = str(spec.get("recency") or "")
    if recency in _RECENCY_TBS:
        body["time_range"] = recency
    if spec.get("tavily_country"):
        body["country"] = spec["tavily_country"]
    if with_domains and spec.get("include_domains"):
        body["include_domains"] = list(spec["include_domains"])[:8]
    return body


def _fetch_tavily_search(query: str, api_key: str, spec: dict | None = None) -> list[dict]:
    spec = spec or {"q": query}

    def once(with_domains: bool) -> list[dict]:
        try:
            resp = _request(
                "post",
                "https://api.tavily.com/search",
                json=_tavily_body(spec, api_key, with_domains=with_domains),
                timeout=20,
            )
        except requests.RequestException as exc:
            print(f"[SCRAPER]  SKIP tavily '{spec['q'][:40]}' ({type(exc).__name__})")
            return []
        if resp is None or resp.status_code != 200:
            print(f"[SCRAPER]  SKIP tavily '{spec['q'][:40]}' (status {resp.status_code})")
            return []
        items = []
        for hit in resp.json().get("results", []):
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "").strip()
            if not url.startswith("http") or not title:
                continue
            items.append({
                "title": title,
                "url": url,
                "snippet": hit.get("content") or "",
                "score": hit.get("score") or 0,
                "query_used": spec["q"],
                "method": "web_search",
            })
        return items

    items = once(True)
    if not items and spec.get("include_domains"):
        print(f"[SCRAPER]  No Tavily hits in include_domains for '{spec['q'][:50]}'; retrying without them")
        items = once(False)
    return items


def fetch_web_search(queries):
    api_key, key_name = _key("TAVILY_API_KEY", "market")
    if not api_key:
        print(f"[SCRAPER]  SKIP Tavily: {key_name} is not set")
        return [], []
    print(f"[SCRAPER]  key: {key_name}")

    print(f"[SCRAPER]  Fetching Tavily search results for {len(queries)} queries...")
    all_items = []
    queries_run = []

    for query in queries[:MAX_QUERIES]:
        spec = _query_spec(query)
        if not spec["q"]:
            continue

        items = _fetch_tavily_search(spec["q"], api_key, spec)

        if items:
            all_items.extend(items)
            queries_run.append(spec["q"])
        print(f"[SCRAPER]  [{spec['q'][:50]}] {len(items)} results")

    print(f"[SCRAPER]  OK {len(all_items)} results from {len(queries_run)} queries")
    return all_items, queries_run


def scrape_url_content(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = _request("get", url, headers=headers, timeout=TIMEOUT)
    if response is None or response.status_code != 200:
        status = getattr(response, "status_code", "none")
        return f"Error: Failed to retrieve page (Status {status})"
    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup(["script", "style"]):
        script.extract()
    return soup.get_text(separator="\n", strip=True)


def serialize_items(items: list) -> str:
    raw = json.dumps(items[:50], ensure_ascii=False)
    if len(raw) > 40_000:
        raw = raw[:40_000] + "\n... [truncated]"
    return raw
