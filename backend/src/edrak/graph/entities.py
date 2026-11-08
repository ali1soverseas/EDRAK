"""Entity naming and matching shared by ingestion, retrieval and agent context.

Pure Python: no Neo4j, ChromaDB or EDRAK imports, so it is cheap to test.

The graph keys every Company/Product/Market/Topic by a slug of its name. Ingestion
and retrieval must produce the same slug for the same name, which is why both go
through ``slugify`` here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlsplit

COMPANY = "Company"
PRODUCT = "Product"
MARKET = "Market"
TOPIC = "Topic"
SEGMENT = "CustomerSegment"
FEATURE = "Feature"
PRICING_PLAN = "PricingPlan"
REGULATION = "Regulation"
THEME = "Theme"
PLATFORM = "Platform"
INDUSTRY = "Industry"

# Order matters: resolve() prefers earlier labels when one name fits several.
ENTITY_LABELS = (
    COMPANY, PRODUCT, PRICING_PLAN, FEATURE, MARKET, REGULATION, INDUSTRY, SEGMENT, THEME, TOPIC, PLATFORM,
)

# Labels that come from a curated catalog rather than from the BusinessRequest, and how a catalog
# entry is attached to its owner: (owner)-[:REL]->(entity).
CATALOG_LABELS = (PRODUCT, FEATURE, PRICING_PLAN, REGULATION, THEME, PLATFORM, INDUSTRY)
OWNER_RELATIONSHIPS = {PRODUCT: "OFFERS", FEATURE: "HAS_FEATURE", PRICING_PLAN: "HAS_PLAN", INDUSTRY: "IN_INDUSTRY"}

# Labels a MetricObservation, MetricRequirement or stance tally may point at. Market entities only:
# a figure is always "of" something the request or the catalog named.
MEASURABLE_LABELS = (COMPANY, PRODUCT, PRICING_PLAN, FEATURE, MARKET, SEGMENT)

# Run-scoped labels written from worker metadata (see ingestion/writers.py). They are observations
# made by one run, never entities: each carries request_id and workspace_id and links to entities
# only when the request or the catalog already knows them.
DATA_SOURCE = "DataSource"  # a company database or REST API the internal worker reads (CompanyProfile.db_config/api_config)
DATA_ASSET = "DataAsset"  # one table or endpoint of a DataSource
ASSESSMENT = "Assessment"  # one competitor-vs-pilot cell of the competitor worker's comparison matrix
THEME_OBSERVATION = "ThemeObservation"  # one theme aggregate counted by the customer worker in one run
SEARCH_TERM = "SearchTerm"  # a keyword whose search interest was measured
TREND_SERIES = "TrendSeries"  # one search-interest series for a SearchTerm in one run

# Internal evidence channels that come from a live system instead of an uploaded document.
DATA_CHANNELS = {"sql_database": "table", "rest_api": "endpoint"}

_PRIMITIVES = (str, int, float, bool)
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac"}  # co.uk, com.eg, ...


# ----------------------------------------------------------------------------- ids


def slugify(name: str | None) -> str:
    """'Advanced Micro Devices' -> 'advanced-micro-devices'. Keeps non-Latin letters (Arabic names)."""
    text = (name or "").strip().lower().replace("&", " and ")
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE)
    return re.sub(r"[-_]+", "-", text).strip("-")


def stable_id(*parts: Any) -> str:
    """Deterministic 16-hex id from parts, so re-ingesting the same thing MERGEs onto one node."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def result_key(task_id: str, attempt: int | None) -> str:
    return f"{task_id}|{attempt or 1}"


def requirement_key(task_id: str, metric_id: str, entity: str) -> str:
    return f"{task_id}|{metric_id}|{slugify(entity)}"


def data_source_id(workspace_id: str | None, channel: str, name: str) -> str:
    """DataSource ids are scoped to a workspace: two tenants may both call their database 'sales_db'."""
    return stable_id(workspace_id, channel, name)


# ----------------------------------------------------------------------------- values


def get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model or a dict (OrchestrationResult carries some stages as dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def iso(value: Any) -> str | None:
    """Datetimes become UTC ISO strings, so they sort correctly as Neo4j strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def host_of(url: str | None) -> str | None:
    """Public web host without 'www.'. None for internal://, file:// and other non-web schemes."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def primitive_props(values: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Keep only what Neo4j can store as a property: primitives and lists of primitives.

    Enums become their value, datetimes ISO strings, None is dropped, nested maps are skipped
    (Neo4j has no map properties).
    """
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        value = enum_value(value)
        if isinstance(value, datetime):
            value = iso(value)
        if value is None:
            continue
        if isinstance(value, _PRIMITIVES):
            out[f"{prefix}{key}"] = value
        elif isinstance(value, (list, tuple, set)):
            items = [iso(v) if isinstance(v, datetime) else enum_value(v) for v in value]
            items = [v for v in items if isinstance(v, _PRIMITIVES)]
            if items and len({type(v) for v in items}) > 1:
                items = [str(v) for v in items]  # Neo4j lists must be homogeneous
            out[f"{prefix}{key}"] = items
    return out


def website_stem(url: str | None) -> str | None:
    """The registrable name of a site, used as an extra matching term.

    'https://about.gitlab.com' -> 'gitlab' (not 'about'), 'bitbucket.org' -> 'bitbucket',
    'shop.example.co.uk' -> 'example'.
    """
    host = host_of(url)
    if not host:
        return None
    labels = host.split(".")
    if len(labels) < 2:
        return None
    stem = labels[-2]
    if stem in _SECOND_LEVEL and len(labels) >= 3:
        stem = labels[-3]
    return stem if len(stem) >= 4 and stem not in _SECOND_LEVEL else None


# ----------------------------------------------------------------------------- matching


@dataclass(frozen=True)
class EntityRef:
    label: str  # one of ENTITY_LABELS
    id: str  # slug
    name: str
    terms: tuple[str, ...]  # names and aliases to look for in text
    role: str = ""  # pilot | competitor | product | offering | market | topic | segment | catalog


class EntityIndex:
    """The entities one request talks about, and a matcher that finds them in free text.

    Built from a BusinessRequest. Ingestion uses it to draw (Finding)-[:ABOUT]->(entity)
    and (Evidence)-[:MENTIONS]->(Company) edges; it only ever links to entities the
    request named, never to names a model invented.
    """

    MIN_TERM_CHARS = 2
    MIN_TOPIC_CHARS = 4

    def __init__(self, refs: Iterable[EntityRef] = ()) -> None:
        self._refs: dict[tuple[str, str], EntityRef] = {}
        self._patterns: list[tuple[re.Pattern[str], EntityRef]] = []
        for ref in refs:
            self.add(ref)

    def add(self, ref: EntityRef) -> None:
        key = (ref.label, ref.id)
        if not ref.id or key in self._refs:
            return  # first role wins: the pilot stays the pilot even if also listed as a target
        self._refs[key] = ref
        floor = self.MIN_TOPIC_CHARS if ref.label == TOPIC else self.MIN_TERM_CHARS
        terms = sorted({t.strip() for t in ref.terms if t and len(t.strip()) >= floor}, key=len, reverse=True)
        if terms:
            pattern = r"(?<!\w)(?:%s)(?!\w)" % "|".join(re.escape(t) for t in terms)
            self._patterns.append((re.compile(pattern, re.IGNORECASE), ref))

    @property
    def refs(self) -> list[EntityRef]:
        return list(self._refs.values())

    def by_role(self, role: str) -> list[EntityRef]:
        return [r for r in self._refs.values() if r.role == role]

    def by_label(self, label: str) -> list[EntityRef]:
        return [r for r in self._refs.values() if r.label == label]

    @property
    def pilot(self) -> EntityRef | None:
        found = self.by_role("pilot")
        return found[0] if found else None

    def match(self, *texts: str | None) -> list[EntityRef]:
        text = " ".join(t for t in texts if t)
        if not text:
            return []
        return [ref for pattern, ref in self._patterns if pattern.search(text)]

    def resolve(self, name: str | None) -> EntityRef | None:
        """Exact lookup by slug, name or alias. Companies win over other labels."""
        if not name:
            return None
        slug, lower = slugify(name), name.strip().lower()
        candidates = [
            r for r in self._refs.values() if r.id == slug or lower in {t.lower() for t in r.terms}
        ]
        candidates.sort(key=lambda r: ENTITY_LABELS.index(r.label))
        return candidates[0] if candidates else None

    def extend_catalog(self, entries: Iterable[Any]) -> list[EntityRef]:
        """Add catalog entities (features, plans, regulations, themes, platforms, extra products).

        Each entry is a dict or object with ``label``, ``name`` and optional ``id`` and ``aliases``.
        Returns the refs that were added.
        """
        added = []
        for entry in entries or []:
            label, name = get(entry, "label"), get(entry, "name")
            if label not in CATALOG_LABELS:
                raise ValueError(f"catalog label must be one of {CATALOG_LABELS}, got {label!r}")
            if not name:
                raise ValueError(f"catalog entry without a name: {entry!r}")
            ref = EntityRef(label, get(entry, "id") or slugify(name), name, (name, *(get(entry, "aliases") or [])), "catalog")
            before = len(self._refs)
            self.add(ref)
            if len(self._refs) > before:
                added.append(ref)
        return added

    @classmethod
    def from_request(cls, request: Any, catalog: Iterable[Any] | None = None) -> EntityIndex:
        profile = get(request, "company_profile")
        context = get(request, "business_context")
        refs: list[EntityRef] = []

        name = get(profile, "name") or "Unnamed company"
        stem = website_stem(get(profile, "website"))
        refs.append(
            EntityRef(COMPANY, slugify(name), name, (name, *(get(profile, "aliases") or []), *([stem] if stem else [])), "pilot")
        )

        for comp in get(context, "competitors") or []:
            cname = get(comp, "name")
            if cname:
                cstem = website_stem(get(comp, "website"))
                refs.append(EntityRef(COMPANY, slugify(cname), cname, (cname, *([cstem] if cstem else [])), "competitor"))
        for cname in get(context, "targets") or []:
            refs.append(EntityRef(COMPANY, slugify(cname), cname, (cname,), "competitor"))

        for product in get(profile, "products") or []:
            refs.append(EntityRef(PRODUCT, slugify(product), product, (product,), "product"))
        offering = get(context, "offering")
        if offering:
            refs.append(EntityRef(PRODUCT, slugify(offering), offering, (offering,), "offering"))
        for market in get(context, "markets") or []:
            refs.append(EntityRef(MARKET, slugify(market), market, (market,), "market"))
        for topic in get(context, "focus_areas") or []:
            refs.append(EntityRef(TOPIC, slugify(topic), topic, (topic,), "topic"))
        segment = get(context, "target_customers")
        if segment:
            refs.append(EntityRef(SEGMENT, slugify(segment), segment, (segment,), "segment"))
        index = cls(refs)
        index.extend_catalog(catalog or [])
        return index
