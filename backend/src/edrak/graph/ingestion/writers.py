"""Writers: one function per EDRAK contract.

Every statement is a MERGE on a stable key, so re-ingesting a run (a retry, a replay
from an artifact) updates nodes instead of duplicating them.

Contracts are read through ``entities.get`` so the same writer accepts the Pydantic
model or its ``model_dump`` dict: OrchestrationResult carries cross-signal and
decision analysis as plain dicts.

What is deliberately NOT written:
* ``Evidence.page_text`` (up to 20k chars per item; the graph keeps fact + excerpt).
* Nested maps (Neo4j has no map properties); primitive metadata is kept as ``meta_*``.
* Secrets: ``DatabaseConnectionConfig.connection_uri``, ``RESTAPIConfig.auth_headers``,
  ``base_url`` and the OpenAPI spec. A DataSource node carries its symbolic id only.
"""

from __future__ import annotations

import logging
from typing import Any

from ..connection import GraphConnection
from ..entities import (
    CATALOG_LABELS,
    COMPANY,
    DATA_CHANNELS,
    ENTITY_LABELS,
    FEATURE,
    MARKET,
    MEASURABLE_LABELS,
    OWNER_RELATIONSHIPS,
    PRICING_PLAN,
    PRODUCT,
    THEME,
    EntityIndex,
    data_source_id,
    enum_value,
    get,
    host_of,
    iso,
    now_iso,
    primitive_props,
    requirement_key,
    result_key,
    slugify,
    stable_id,
)

logger = logging.getLogger(__name__)

INTERNAL_SOURCE_TYPES = {"internal_document", "synthetic_internal"}
INTERNAL_WORKER = "internal_intelligence"
COMPETITOR_WORKER = "competitor_intelligence"
CUSTOMER_WORKER = "customer_trends"
_STANCES = ("positive", "neutral", "negative", "mixed")


def _dedupe(expr: str, addition: str) -> str:
    """Cypher for 'list union without duplicates' (no APOC needed)."""
    return f"reduce(acc = [], x IN coalesce({expr}, []) + {addition} | CASE WHEN x IN acc THEN acc ELSE acc + x END)"


# ============================================================================= request + plan

Q_REQUEST = f"""
MERGE (r:BusinessRequest {{request_id: $rid}})
SET r += $props
MERGE (p:Company {{id: $pilot.id}})
SET p.name = coalesce(p.name, $pilot.name),
    p.website = coalesce($pilot.website, p.website),
    p.notes = coalesce($pilot.notes, p.notes),
    p.aliases = {_dedupe("p.aliases", "$pilot.aliases")}
MERGE (r)-[:ABOUT_COMPANY]->(p)
FOREACH (h IN $pilot.domains |
  MERGE (s:Source {{host: h}})
  MERGE (p)-[:OWNS_DOMAIN]->(s))
FOREACH (_ IN CASE WHEN $ws IS NULL THEN [] ELSE [1] END |
  MERGE (w:Workspace {{id: $ws}})
  MERGE (r)-[:IN_WORKSPACE]->(w)
  MERGE (w)-[:OWNS_PROFILE]->(p)
  FOREACH (d IN $data_sources |
    MERGE (ds:DataSource {{id: d.id}})
    SET ds += d.props
    MERGE (w)-[:CONNECTS]->(ds)))
FOREACH (_ IN CASE WHEN $user IS NULL THEN [] ELSE [1] END |
  MERGE (u:User {{id: $user}})
  MERGE (u)-[:CREATED]->(r))
FOREACH (a IN $pilot.accounts |
  MERGE (s:SocialAccount {{url: a.url}}) SET s.platform = a.platform
  MERGE (p)-[:HAS_ACCOUNT]->(s))
FOREACH (pr IN $products |
  MERGE (x:Product {{id: pr.id}}) ON CREATE SET x.name = pr.name
  MERGE (p)-[:OFFERS]->(x))
FOREACH (c IN $competitors |
  MERGE (x:Company {{id: c.id}})
  SET x.name = coalesce(x.name, c.name),
      x.website = coalesce(c.website, x.website),
      x.aliases = {_dedupe("x.aliases", "c.aliases")}
  MERGE (r)-[rel:COMPARES_AGAINST]->(x) SET rel.discovered_by = c.discovered_by
  FOREACH (h IN c.domains |
    MERGE (s:Source {{host: h}})
    MERGE (x)-[:OWNS_DOMAIN]->(s))
  FOREACH (a IN c.accounts |
    MERGE (s:SocialAccount {{url: a.url}}) SET s.platform = a.platform
    MERGE (x)-[:HAS_ACCOUNT]->(s)))
FOREACH (m IN $markets |
  MERGE (x:Market {{id: m.id}}) ON CREATE SET x.name = m.name
  MERGE (r)-[:IN_MARKET]->(x))
FOREACH (t IN $topics |
  MERGE (x:Topic {{id: t.id}}) ON CREATE SET x.name = t.name
  MERGE (r)-[:FOCUSES_ON]->(x))
FOREACH (o IN $offering |
  MERGE (x:Product {{id: o.id}}) ON CREATE SET x.name = o.name
  MERGE (r)-[:ASSESSES]->(x))
FOREACH (s IN $segment |
  MERGE (x:CustomerSegment {{id: s.id}}) ON CREATE SET x.name = s.name
  MERGE (r)-[:TARGETS]->(x))
"""


def _domains(*urls: str | None) -> list[str]:
    """Web hosts a company owns, without 'www.'. Evidence PUBLISHED_BY one of them is first-party."""
    hosts = set()
    for url in urls:
        if url:
            host = host_of(url if "://" in url else f"https://{url}")
            if host:
                hosts.add(host)
    return sorted(hosts)


def _data_sources(profile: Any, workspace_id: str | None) -> list[dict[str, Any]]:
    """CompanyProfile.db_config / api_config -> DataSource rows. Symbolic ids and descriptions only:
    connection URIs, base URLs, specs and auth headers never reach the graph."""
    rows = []
    db = get(profile, "db_config")
    if db is not None and get(db, "db_id"):
        rows.append(
            {
                "id": data_source_id(workspace_id, "sql_database", get(db, "db_id")),
                "props": primitive_props(
                    {
                        "name": get(db, "db_id"),
                        "kind": "sql_database",
                        "description": get(db, "description"),
                        "allowed_tables": list(get(db, "allowed_tables") or []),
                        "workspace_id": workspace_id,
                    }
                ),
            }
        )
    api = get(profile, "api_config")
    if api is not None and get(api, "api_id"):
        rows.append(
            {
                "id": data_source_id(workspace_id, "rest_api", get(api, "api_id")),
                "props": primitive_props(
                    {
                        "name": get(api, "api_id"),
                        "kind": "rest_api",
                        "description": get(api, "description"),
                        "workspace_id": workspace_id,
                    }
                ),
            }
        )
    return rows


def _accounts(owner: Any) -> list[dict[str, Any]]:
    return [
        {"url": get(a, "url"), "platform": enum_value(get(a, "platform"))}
        for a in get(owner, "social_accounts") or []
        if get(a, "url")
    ]


def ingest_request(
    conn: GraphConnection,
    request: Any,
    *,
    workspace_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
    index: EntityIndex | None = None,
) -> EntityIndex:
    """BusinessRequest -> request node, the pilot company, competitors, markets, topics, offering,
    the domains each company owns and the workspace's data sources."""
    index = index or EntityIndex.from_request(request)
    profile, context = get(request, "company_profile"), get(request, "business_context")
    pilot = index.pilot
    workspace_id = workspace_id or get(profile, "workspace_id")

    props = primitive_props(
        {
            "goal": get(request, "goal"),
            "use_case": get(context, "use_case"),
            "trigger": get(context, "trigger"),
            "time_window_days": get(context, "time_window_days"),
            "constraints": list(get(context, "constraints") or []),
            "focus_areas": list(get(context, "focus_areas") or []),
            "created_at": get(request, "created_at"),
            "status": status,
            "workspace_id": workspace_id,
            "ingested_at": now_iso(),
        }
    )

    competitors: dict[str, dict[str, Any]] = {}
    for comp in get(context, "competitors") or []:
        ref = index.resolve(get(comp, "name"))
        if ref is None or ref.role == "pilot":
            continue
        competitors[ref.id] = {
            "id": ref.id,
            "name": ref.name,
            "aliases": [t for t in ref.terms if t != ref.name],
            "website": get(comp, "website"),
            "discovered_by": get(comp, "discovered_by") or "user",
            "accounts": _accounts(comp),
            "domains": _domains(get(comp, "website")),
        }
    for ref in index.by_role("competitor"):  # names that only came through `targets`
        competitors.setdefault(
            ref.id,
            {"id": ref.id, "name": ref.name, "aliases": [], "website": None, "discovered_by": "user", "accounts": [], "domains": []},
        )

    def nodes(role: str) -> list[dict[str, str]]:
        return [{"id": r.id, "name": r.name} for r in index.by_role(role)]

    conn.write(
        Q_REQUEST,
        rid=get(request, "request_id"),
        props=props,
        ws=workspace_id,
        user=user_id,
        pilot={
            "id": pilot.id,
            "name": pilot.name,
            "aliases": list(get(profile, "aliases") or []),
            "website": get(profile, "website"),
            "notes": get(profile, "notes"),
            "accounts": _accounts(profile),
            "domains": _domains(get(profile, "website")),
        },
        data_sources=_data_sources(profile, workspace_id),
        products=nodes("product"),
        competitors=list(competitors.values()),
        markets=nodes("market"),
        topics=nodes("topic"),
        offering=nodes("offering"),
        segment=nodes("segment"),
    )
    return index


Q_PLAN = """
MATCH (r:BusinessRequest {request_id: $rid})
MERGE (pl:ResearchPlan {plan_id: $plan_id})
SET pl += $plan_props
MERGE (r)-[:HAS_PLAN]->(pl)
FOREACH (t IN $tasks |
  MERGE (rt:ResearchTask {task_id: t.task_id})
  SET rt += t.props
  MERGE (pl)-[h:HAS_TASK]->(rt) SET h.position = t.position
  MERGE (wk:Worker {type: t.worker})
  MERGE (rt)-[:ASSIGNED_TO]->(wk)
  FOREACH (q IN t.requirements |
    MERGE (mr:MetricRequirement {key: q.key})
    SET mr += q.props
    MERGE (rt)-[:REQUIRES]->(mr)
    MERGE (m:Metric {metric_id: q.metric_id})
    MERGE (mr)-[:FOR_METRIC]->(m)
    FOREACH (_ IN CASE WHEN q.company_id IS NULL THEN [] ELSE [1] END |
      MERGE (c:Company {id: q.company_id}) ON CREATE SET c.name = q.entity
      MERGE (mr)-[:FOR_ENTITY]->(c))
    FOREACH (_ IN CASE WHEN q.market_id IS NULL THEN [] ELSE [1] END |
      MERGE (mk:Market {id: q.market_id}) ON CREATE SET mk.name = q.entity
      MERGE (mr)-[:FOR_ENTITY]->(mk))
    FOREACH (_ IN CASE WHEN q.product_id IS NULL THEN [] ELSE [1] END |
      MERGE (pd:Product {id: q.product_id}) ON CREATE SET pd.name = q.entity
      MERGE (mr)-[:FOR_ENTITY]->(pd))))
"""


def ingest_plan(conn: GraphConnection, plan: Any, *, request_id: str, index: EntityIndex) -> set[str]:
    """ResearchPlan -> plan, tasks, workers, metric requirements. Returns the requirement keys."""
    tasks, keys = [], set()
    for position, task in enumerate(get(plan, "tasks") or []):
        task_id = get(task, "task_id")
        requirements = []
        for req in get(task, "requirements") or []:
            metric_id, entity = get(req, "metric_id"), get(req, "entity")
            ref = index.resolve(entity)
            key = requirement_key(task_id, metric_id, entity)
            keys.add(key)
            requirements.append(
                {
                    "key": key,
                    "metric_id": metric_id,
                    "entity": entity,
                    "company_id": ref.id if ref and ref.label == COMPANY else None,
                    "market_id": ref.id if ref and ref.label == MARKET else None,
                    "product_id": ref.id if ref and ref.label == PRODUCT else None,
                    "props": primitive_props(
                        {
                            "metric_id": metric_id,
                            "entity": entity,
                            "required": get(req, "required"),
                            "definition": get(req, "definition"),
                            "check_rule": get(req, "check_rule"),
                            "preferred_source_types": list(get(req, "preferred_source_types") or []),
                            "query_hints": list(get(req, "query_hints") or []),
                        }
                    ),
                }
            )
        tasks.append(
            {
                "task_id": task_id,
                "worker": enum_value(get(task, "worker")),
                "position": position,
                "props": primitive_props(
                    {
                        "goal": get(task, "goal"),
                        "focus": get(task, "focus"),
                        "attempt": get(task, "attempt"),
                        "worker": get(task, "worker"),
                        "request_id": request_id,
                    }
                ),
                "requirements": requirements,
            }
        )
    conn.write(
        Q_PLAN,
        rid=request_id,
        plan_id=get(plan, "plan_id"),
        plan_props=primitive_props({"rationale": get(plan, "rationale"), "created_at": get(plan, "created_at")}),
        tasks=tasks,
    )
    return keys


# ============================================================================= entity catalog


def _catalog_query(label: str) -> str:
    if label not in CATALOG_LABELS:  # labels and relationship types cannot be parameters
        raise ValueError(f"not a catalog label: {label!r}")
    own = OWNER_RELATIONSHIPS.get(label)
    owner = (
        f"""FOREACH (cid IN row.owner_companies | MERGE (c:Company {{id: cid}}) MERGE (c)-[:{own}]->(x))
FOREACH (pid IN row.owner_products | MERGE (p:Product {{id: pid}}) MERGE (p)-[:{own}]->(x))"""
        if own
        else ""
    )
    return f"""
UNWIND $rows AS row
MERGE (x:{label} {{id: row.id}})
SET x.name = coalesce(x.name, row.name),
    x.description = coalesce(row.description, x.description),
    x.aliases = {_dedupe("x.aliases", "row.aliases")}
FOREACH (_ IN CASE WHEN $ws IS NULL THEN [] ELSE [1] END |
  MERGE (w:Workspace {{id: $ws}})
  MERGE (w)-[t:TRACKS]->(x) ON CREATE SET t.since = $now)
{owner}
FOREACH (mid IN row.markets | MERGE (m:Market {{id: mid}}) MERGE (x)-[:APPLIES_IN]->(m))
"""


def catalog_rows(entries: list[Any], index: EntityIndex) -> dict[str, list[dict[str, Any]]]:
    """Validate catalog entries against ``index`` and group them into write rows by label.

    Owners ("of") and markets ("applies_in") must be entities the index already knows; an unknown
    name raises ValueError instead of silently creating a node.
    """
    index.extend_catalog(entries)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries or []:
        label, name = get(entry, "label"), get(entry, "name")
        of = get(entry, "of")
        owners = [of] if isinstance(of, str) else list(of or [])
        if owners and label not in OWNER_RELATIONSHIPS:
            raise ValueError(f"{label} {name!r}: 'of' is only allowed for {sorted(OWNER_RELATIONSHIPS)}")
        companies, products = [], []
        for owner in owners:
            ref = index.resolve(owner)
            if ref is None or ref.label not in (COMPANY, PRODUCT):
                raise ValueError(f"{label} {name!r}: owner {owner!r} is not a known company or product")
            (companies if ref.label == COMPANY else products).append(ref.id)
        markets = []
        for market in get(entry, "applies_in") or []:
            ref = index.resolve(market)
            if ref is None or ref.label != MARKET:
                raise ValueError(f"{label} {name!r}: market {market!r} is not one of the request's markets")
            markets.append(ref.id)
        grouped.setdefault(label, []).append(
            {
                "id": get(entry, "id") or slugify(name),
                "name": name,
                "aliases": list(get(entry, "aliases") or []),
                "description": get(entry, "description"),
                "owner_companies": companies,
                "owner_products": products,
                "markets": markets,
            }
        )
    return grouped


def ingest_catalog(
    conn: GraphConnection, entries: list[Any], *, workspace_id: str | None, index: EntityIndex
) -> dict[str, int]:
    """Curated entities -> nodes, owner edges (OFFERS / HAS_FEATURE / HAS_PLAN / IN_INDUSTRY), APPLIES_IN markets, TRACKS.

    Also adds them to ``index`` so this run's findings and evidence link to them.
    """
    grouped = catalog_rows(entries, index)
    now = now_iso()
    for label, rows in grouped.items():
        conn.write_batches(_catalog_query(label), rows, ws=workspace_id, now=now)
    return {label: len(rows) for label, rows in grouped.items()}


Q_READ_CATALOG = """
MATCH (:Workspace {id: $ws})-[:TRACKS]->(x)
WHERE x:Product OR x:Feature OR x:PricingPlan OR x:Regulation OR x:Theme OR x:Platform OR x:Industry
RETURN labels(x)[0] AS label, x.id AS id, x.name AS name, coalesce(x.aliases, []) AS aliases
"""


def read_catalog(conn: GraphConnection, workspace_id: str | None) -> list[dict[str, Any]]:
    """The catalog a workspace already tracks, so later runs link to it without passing it again."""
    if not workspace_id:
        return []
    return conn.read(Q_READ_CATALOG, ws=workspace_id)


# ============================================================================= worker results

Q_RESULT = """
MERGE (rt:ResearchTask {task_id: $task_id})
MERGE (wr:WorkerResult {key: $key})
SET wr += $props
MERGE (rt)-[:RETURNED]->(wr)
MERGE (wk:Worker {type: $worker})
MERGE (wr)-[:PRODUCED_BY]->(wk)
"""

Q_EVIDENCE = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (e:Evidence {evidence_id: row.evidence_id})
SET e += row.props,
    e.workspace_ids = CASE
      WHEN $ws IS NULL OR $ws IN coalesce(e.workspace_ids, []) THEN coalesce(e.workspace_ids, [])
      ELSE coalesce(e.workspace_ids, []) + $ws END
MERGE (wr)-[:COLLECTED]->(e)
FOREACH (_ IN CASE WHEN row.host IS NULL THEN [] ELSE [1] END |
  MERGE (s:Source {host: row.host})
  MERGE (e)-[:PUBLISHED_BY]->(s))
FOREACH (_ IN CASE WHEN row.document_id IS NULL THEN [] ELSE [1] END |
  MERGE (d:Document {id: row.document_id})
  ON CREATE SET d.title = row.props.source_title
  MERGE (e)-[:FROM_DOCUMENT]->(d)
  FOREACH (__ IN CASE WHEN $ws IS NULL THEN [] ELSE [1] END |
    MERGE (w:Workspace {id: $ws})
    MERGE (d)-[:IN_WORKSPACE]->(w)))
FOREACH (da IN row.data_assets |
  MERGE (ds:DataSource {id: da.source_id})
  ON CREATE SET ds.name = da.source_name, ds.kind = da.channel, ds.workspace_id = $ws
  MERGE (a:DataAsset {id: da.id})
  ON CREATE SET a.name = da.name, a.kind = da.kind
  MERGE (a)-[:IN_SOURCE]->(ds)
  MERGE (e)-[rf:RETRIEVED_FROM]->(a)
  SET rf.query = da.query, rf.row_count = da.row_count
  FOREACH (__ IN CASE WHEN $ws IS NULL THEN [] ELSE [1] END |
    MERGE (w:Workspace {id: $ws})
    MERGE (w)-[:CONNECTS]->(ds)))
FOREACH (cid IN row.mentions |
  MERGE (c:Company {id: cid})
  MERGE (e)-[:MENTIONS]->(c))
FOREACH (pf IN row.platforms |
  MERGE (p:Platform {id: pf.id}) ON CREATE SET p.name = pf.name
  MERGE (e)-[:POSTED_ON]->(p))
FOREACH (tid IN row.themes |
  MERGE (t:Theme {id: tid})
  MERGE (e)-[x:EXPRESSES]->(t)
  SET x.stance = coalesce(row.stance, x.stance))
"""

Q_FINDINGS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (f:Finding {finding_id: row.finding_id})
SET f += row.props
MERGE (wr)-[:PRODUCED]->(f)
FOREACH (ref IN row.refs |
  MERGE (e:Evidence {evidence_id: ref.evidence_id})
  FOREACH (_ IN CASE WHEN ref.relation = 'supports' THEN [1] ELSE [] END |
    MERGE (f)-[:SUPPORTED_BY]->(e))
  FOREACH (_ IN CASE WHEN ref.relation = 'contradicts' THEN [1] ELSE [] END |
    MERGE (f)-[:CONTRADICTED_BY]->(e))
  FOREACH (_ IN CASE WHEN ref.relation = 'contextualizes' THEN [1] ELSE [] END |
    MERGE (f)-[:CONTEXTUALIZED_BY]->(e)))
"""


def about_query(label: str) -> str:
    if label not in ENTITY_LABELS:  # labels cannot be parameters; whitelist before formatting
        raise ValueError(f"not an entity label: {label!r}")
    return (
        "UNWIND $rows AS row "
        "MATCH (f:Finding {finding_id: row.finding_id}) "
        f"MATCH (x:{label} {{id: row.entity_id}}) "
        "MERGE (f)-[:ABOUT]->(x)"
    )


Q_CONFLICTS = """
UNWIND $rows AS row
MATCH (f:Finding {finding_id: row.finding_id})
MERGE (c:Conflict {key: row.key})
SET c.description = row.description
MERGE (c)-[:ON]->(f)
FOREACH (eid IN row.evidence_ids |
  MERGE (e:Evidence {evidence_id: eid})
  MERGE (c)-[:CITES]->(e))
"""

Q_GAPS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (g:InformationGap {id: row.id})
ON CREATE SET g.status = 'open', g.created_at = $now
SET g.subject = row.subject, g.worker = $worker, g.request_id = $rid, g.workspace_id = $ws
MERGE (wr)-[:REPORTED_GAP]->(g)
"""

Q_OBSERVATIONS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (o:MetricObservation {key: row.key})
SET o += row.props
MERGE (wr)-[:OBSERVED]->(o)
FOREACH (_ IN CASE WHEN row.metric_id IS NULL THEN [] ELSE [1] END |
  MERGE (m:Metric {metric_id: row.metric_id})
  MERGE (o)-[:MEASURES]->(m))
FOREACH (_ IN CASE WHEN row.company_id IS NULL THEN [] ELSE [1] END |
  MERGE (c:Company {id: row.company_id}) ON CREATE SET c.name = row.entity
  MERGE (o)-[:OF]->(c))
FOREACH (_ IN CASE WHEN row.market_id IS NULL THEN [] ELSE [1] END |
  MERGE (m:Market {id: row.market_id}) ON CREATE SET m.name = row.entity
  MERGE (o)-[:OF]->(m))
FOREACH (_ IN CASE WHEN row.req_key IS NULL THEN [] ELSE [1] END |
  MERGE (mr:MetricRequirement {key: row.req_key})
  MERGE (mr)-[:FULFILLED_BY]->(o))
MERGE (e:Evidence {evidence_id: row.evidence_id})
MERGE (o)-[x:EXTRACTED_FROM]->(e)
SET x.source_span = row.source_span
"""


def link_query(source_label: str, source_key: str, rel: str, target_label: str, *, edge_props: bool = False) -> str:
    """``UNWIND $rows`` -> (source)-[:rel]->(target). The target must already exist (MATCH, never MERGE):
    it is an entity the request or the catalog named. Labels and types are whitelisted before formatting."""
    if target_label not in ENTITY_LABELS:
        raise ValueError(f"not an entity label: {target_label!r}")
    if not rel.isidentifier() or not rel.isupper() or not source_label.isidentifier() or not source_key.isidentifier():
        raise ValueError(f"unsafe label, key or relationship: {source_label!r} {source_key!r} {rel!r}")
    return (
        "UNWIND $rows AS row "
        f"MATCH (s:{source_label} {{{source_key}: row.source}}) "
        f"MATCH (x:{target_label} {{id: row.entity_id}}) "
        f"MERGE (s)-[r:{rel}]->(x)"
        + (" SET r += row.props" if edge_props else "")
    )


def _write_links(
    conn: GraphConnection, rows_by_label: dict[str, list[dict[str, Any]]], source_label: str, source_key: str, rel: str,
    *, edge_props: bool = False,
) -> int:
    written = 0
    for label, rows in rows_by_label.items():
        if rows:
            conn.write_batches(link_query(source_label, source_key, rel, label, edge_props=edge_props), rows)
            written += len(rows)
    return written


def _quality(evidence: Any) -> str | None:
    """The verifier's own URL-based quality, when that module is importable."""
    try:
        from edrak.verification.quality import best_quality

        return enum_value(best_quality([evidence]))
    except Exception:  # noqa: BLE001 - quality is a nice-to-have on the node
        return None


def _data_asset(meta: dict[str, Any], workspace_id: str | None) -> dict[str, Any] | None:
    """The table or endpoint a piece of internal evidence was read from (tri-modal retriever metadata).

    Only ``channel`` decides: market evidence also carries an ``endpoint`` key, for a public API.
    """
    channel = meta.get("channel")
    kind = DATA_CHANNELS.get(channel)
    if kind is None:
        return None
    if channel == "sql_database":
        source_name, name = meta.get("db_name") or meta.get("db_id"), meta.get("table")
    else:
        source_name, name = meta.get("api_id"), meta.get("endpoint")
    if not source_name or not name:
        return None
    source_id = data_source_id(workspace_id, channel, source_name)
    row_count = meta.get("row_count")
    return {
        "source_id": source_id,
        "source_name": source_name,
        "channel": channel,
        "id": stable_id(source_id, kind, name),
        "name": name,
        "kind": kind,
        "query": meta.get("query") if isinstance(meta.get("query"), str) else None,
        "row_count": row_count if isinstance(row_count, int) else None,
    }


def ingest_worker_result(
    conn: GraphConnection,
    result: Any,
    *,
    request_id: str,
    workspace_id: str | None,
    index: EntityIndex,
    requirement_keys: set[str] | None = None,
) -> dict[str, int]:
    """WorkerResult -> result, evidence, sources, findings (+ABOUT), conflicts, gaps, metric observations."""
    task_id = get(result, "task_id")
    attempt = get(result, "attempt") or 1
    worker = enum_value(get(result, "worker"))
    key = result_key(task_id, attempt)
    now = now_iso()

    conn.write(
        Q_RESULT,
        task_id=task_id,
        key=key,
        worker=worker,
        props=primitive_props(
            {
                "task_id": task_id,
                "attempt": attempt,
                "worker": worker,
                "status": get(result, "status"),
                "confidence": get(result, "confidence"),
                "started_at": get(result, "started_at"),
                "completed_at": get(result, "completed_at"),
                "error": get(result, "error"),
                "gaps": list(get(result, "gaps") or []),
                "request_id": request_id,
                "workspace_id": workspace_id,
            }
        ),
    )

    # --- evidence
    synthetic: dict[str, bool] = {}
    retrieved: dict[str, str | None] = {}
    evidence_rows = []
    for ev in get(result, "evidence") or []:
        eid = get(ev, "evidence_id")
        source_type = enum_value(get(ev, "source_type"))
        url = get(ev, "source_url")
        meta = get(ev, "metadata") or {}
        synthetic[eid] = bool(get(ev, "is_synthetic"))
        retrieved[eid] = iso(get(ev, "retrieved_at"))
        props = primitive_props(
            {
                "source_type": source_type,
                "source_title": get(ev, "source_title"),
                "source_url": url,
                "publisher": get(ev, "publisher"),
                "extracted_fact": get(ev, "extracted_fact"),
                "excerpt": get(ev, "excerpt"),
                "retrieved_at": get(ev, "retrieved_at"),
                "is_synthetic": bool(get(ev, "is_synthetic")),
                "quality": _quality(ev),
            }
        )
        props.update(primitive_props(meta if isinstance(meta, dict) else {}, prefix="meta_"))
        if not isinstance(meta, dict):
            meta = {}
        matched = index.match(get(ev, "extracted_fact"), get(ev, "excerpt"), get(ev, "source_title"))
        platform = meta.get("platform")
        asset = _data_asset(meta, workspace_id)
        is_document = source_type in INTERNAL_SOURCE_TYPES and asset is None
        evidence_rows.append(
            {
                "evidence_id": eid,
                "props": props,
                "host": host_of(url),
                "document_id": (meta.get("source_uri") or url) if is_document else None,
                "data_assets": [asset] if asset else [],
                "mentions": [r.id for r in matched if r.label == COMPANY],
                "themes": [r.id for r in matched if r.label == THEME],
                "platforms": [{"id": slugify(platform), "name": platform}] if platform else [],
                "stance": meta.get("stance") or meta.get("sentiment"),
            }
        )
    conn.write_batches(Q_EVIDENCE, evidence_rows, key=key, ws=workspace_id)

    # --- findings and what they are about
    finding_rows, about = [], {label: [] for label in ENTITY_LABELS}
    for finding in get(result, "findings") or []:
        fid = get(finding, "finding_id")
        statement = get(finding, "statement")
        refs = [
            {"evidence_id": get(r, "evidence_id"), "relation": enum_value(get(r, "relation")) or "supports"}
            for r in get(finding, "evidence_refs") or []
        ]
        supporting = [r["evidence_id"] for r in refs if r["relation"] == "supports"]
        finding_rows.append(
            {
                "finding_id": fid,
                "refs": refs,
                "props": primitive_props(
                    {
                        "statement": statement,
                        "category": get(finding, "category"),
                        "confidence": get(finding, "confidence"),
                        "limitations": list(get(finding, "limitations") or []),
                        "worker": worker,
                        "task_id": task_id,
                        "attempt": attempt,
                        "request_id": request_id,
                        "workspace_id": workspace_id,
                        "synthetic_only": bool(supporting) and all(synthetic.get(x, False) for x in supporting),
                        "observed_at": max((retrieved.get(x) or "" for x in supporting), default="") or now,
                        "ingested_at": now,
                    }
                ),
            }
        )
        matched = index.match(statement)
        if worker == INTERNAL_WORKER and index.pilot and not any(r.label == COMPANY for r in matched):
            matched.append(index.pilot)  # internal findings are about our own company by definition
        for ref in matched:
            about[ref.label].append({"finding_id": fid, "entity_id": ref.id})
    conn.write_batches(Q_FINDINGS, finding_rows, key=key)
    for label, rows in about.items():
        if rows:
            conn.write_batches(about_query(label), rows)

    # --- conflicts
    conflict_rows = [
        {
            "key": stable_id(key, get(c, "finding_id"), i),
            "finding_id": get(c, "finding_id"),
            "description": get(c, "description"),
            "evidence_ids": list(get(c, "contradicting_evidence_ids") or []),
        }
        for i, c in enumerate(get(result, "conflicts") or [])
    ]
    if conflict_rows:
        conn.write_batches(Q_CONFLICTS, conflict_rows)

    # --- gaps
    gap_rows = [
        {"id": stable_id(request_id, worker, gap.strip().lower()), "subject": gap.strip()}
        for gap in get(result, "gaps") or []
        if isinstance(gap, str) and gap.strip()
    ]
    if gap_rows:
        conn.write_batches(Q_GAPS, gap_rows, key=key, now=now, worker=worker, rid=request_id, ws=workspace_id)

    # --- typed metric observations (WorkerResult.metadata["metrics"], see metrics/requirements.py)
    obs_rows: list[dict[str, Any]] = []
    obs_of: dict[str, list[dict[str, Any]]] = {}  # Product / PricingPlan / Feature / Segment targets
    obs_items: dict[str, list[dict[str, Any]]] = {}  # the plan or product named in MetricObservation.label
    for raw in (get(result, "metadata") or {}).get("metrics") or []:
        metric_id, entity, evidence_id = get(raw, "metric_id"), get(raw, "entity"), get(raw, "evidence_id")
        if not entity or not evidence_id:
            continue
        ref = index.resolve(entity)
        entity_id = ref.id if ref else slugify(entity)
        req_key = requirement_key(task_id, metric_id, entity) if metric_id else None
        if requirement_keys is None or req_key not in requirement_keys:
            req_key = None  # only link requirements the ingested plan really had
        obs_key = stable_id(evidence_id, metric_id, entity_id, get(raw, "label"), get(raw, "period"))
        if ref is not None and ref.label in MEASURABLE_LABELS and ref.label not in (COMPANY, MARKET):
            obs_of.setdefault(ref.label, []).append({"source": obs_key, "entity_id": ref.id})
        item = index.resolve(get(raw, "label"))
        if item is not None and item.label in (PRODUCT, PRICING_PLAN, FEATURE) and item.id != entity_id:
            obs_items.setdefault(item.label, []).append({"source": obs_key, "entity_id": item.id})
        obs_rows.append(
            {
                "key": obs_key,
                "metric_id": metric_id,
                "entity": entity,
                "company_id": entity_id if ref is None or ref.label == COMPANY else None,
                "market_id": entity_id if ref is not None and ref.label == MARKET else None,
                "req_key": req_key,
                "evidence_id": evidence_id,
                "source_span": get(raw, "source_span"),
                "props": primitive_props(
                    {
                        "metric_id": metric_id,
                        "entity": entity,
                        "entity_id": entity_id,
                        "value_text": get(raw, "value_text"),
                        "value": get(raw, "value"),
                        "unit": get(raw, "unit"),
                        "period": get(raw, "period"),
                        "label": get(raw, "label"),
                        "definition": get(raw, "definition"),
                        "source_span": get(raw, "source_span"),
                        "scope": get(raw, "scope"),
                        "sample_size": get(raw, "sample_size"),
                        "status": get(raw, "status"),
                        "derived": get(raw, "derived"),
                        "formula": get(raw, "formula"),
                        "worker": worker,
                        "request_id": request_id,
                        "workspace_id": workspace_id,
                        "observed_at": retrieved.get(evidence_id) or now,
                    }
                ),
            }
        )
    if obs_rows:
        conn.write_batches(Q_OBSERVATIONS, obs_rows, key=key)
        _write_links(conn, obs_of, "MetricObservation", "key", "OF")
        _write_links(conn, obs_items, "MetricObservation", "key", "OF_ITEM")

    counts = {
        "evidence": len(evidence_rows),
        "findings": len(finding_rows),
        "conflicts": len(conflict_rows),
        "gaps": len(gap_rows),
        "observations": len(obs_rows),
    }
    meta = get(result, "metadata") or {}
    if isinstance(meta, dict):
        seen_at = max((v for v in retrieved.values() if v), default=None) or now
        ctx = dict(conn=conn, key=key, request_id=request_id, workspace_id=workspace_id, index=index, seen_at=seen_at)
        if worker == COMPETITOR_WORKER:
            counts.update(ingest_competitor_metadata(meta, **ctx))
        elif worker == CUSTOMER_WORKER:
            counts.update(ingest_customer_metadata(meta, **ctx))
    return counts


# ============================================================================= worker metadata
# WorkerResult.metadata is a free dict. These read only the keys the competitor and customer
# workers document (agents/competitor_intelligence/nodes.py build_worker_result,
# agents/customer_trends/runner.py + schemas/findings.py to_worker_result) and skip anything malformed.

Q_COMPETITOR_META = """
UNWIND $rows AS row
MATCH (:BusinessRequest {request_id: $rid})-[rel:COMPARES_AGAINST]->(c:Company {id: row.id})
SET rel += row.props
FOREACH (h IN row.domains |
  MERGE (s:Source {host: h})
  MERGE (c)-[:OWNS_DOMAIN]->(s))
"""

Q_ASSESSMENTS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MATCH (c:Company {id: row.company_id})
MERGE (a:Assessment {key: row.key})
SET a += row.props
MERGE (wr)-[:ASSESSED]->(a)
MERGE (a)-[:OF]->(c)
MERGE (t:Topic {id: row.dimension_id}) ON CREATE SET t.name = row.dimension
MERGE (a)-[:ON_DIMENSION]->(t)
WITH a, row
OPTIONAL MATCH (p:Company {id: $pilot})
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (a)-[:RELATIVE_TO]->(p))
WITH a, row
UNWIND row.evidence_ids AS eid
MATCH (e:Evidence {evidence_id: eid})
MERGE (a)-[:CITES]->(e)
"""

Q_THEME_OBSERVATIONS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (o:ThemeObservation {key: row.key})
SET o += row.props
MERGE (wr)-[:OBSERVED_THEME]->(o)
FOREACH (tid IN row.themes |
  MERGE (t:Theme {id: tid})
  MERGE (o)-[:OF_THEME]->(t))
"""

Q_TRENDS = """
UNWIND $rows AS row
MATCH (wr:WorkerResult {key: $key})
MERGE (k:SearchTerm {id: row.term_id}) ON CREATE SET k.name = row.term
MERGE (ts:TrendSeries {key: row.key})
SET ts += row.props
MERGE (wr)-[:OBSERVED_TREND]->(ts)
MERGE (ts)-[:FOR_TERM]->(k)
"""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _run_props(request_id: str, workspace_id: str | None, worker: str, seen_at: str) -> dict[str, Any]:
    return {"request_id": request_id, "workspace_id": workspace_id, "worker": worker, "observed_at": seen_at}


def ingest_competitor_metadata(
    meta: dict[str, Any], *, conn: GraphConnection, key: str, request_id: str, workspace_id: str | None,
    index: EntityIndex, seen_at: str,
) -> dict[str, int]:
    """competitors[] -> discovery details on COMPARES_AGAINST and (Company)-[:OWNS_DOMAIN]->(Source);
    competitor_profiles -> the per-run overview on COMPARES_AGAINST; comparison_matrix -> Assessment nodes.

    Overviews and assessments stay on run-scoped edges and nodes, never on the shared Company node,
    so one workspace's research never shows up in another workspace's graph reads.
    """
    profiles = meta.get("competitor_profiles") if isinstance(meta.get("competitor_profiles"), dict) else {}
    competitor_rows: dict[str, dict[str, Any]] = {}
    for comp in meta.get("competitors") or []:
        ref = index.resolve(get(comp, "name"))
        if ref is None or ref.label != COMPANY or ref.role == "pilot":
            continue
        confidence = _number(get(comp, "discovery_confidence"))
        competitor_rows[ref.id] = {
            "id": ref.id,
            "domains": _domains(get(comp, "website"), *(get(comp, "official_domains") or [])),
            "props": primitive_props(
                {"discovery_reason": get(comp, "discovery_reason"), "discovery_confidence": confidence}
            ),
        }
    for name, profile in profiles.items():
        ref = index.resolve(name)
        overview = get(profile, "overview")
        if ref is None or ref.label != COMPANY or ref.role == "pilot" or not overview:
            continue
        if not get(profile, "verified_findings_count"):
            continue  # "Profile synthesis failed" / "No verified findings": nothing worth keeping
        row = competitor_rows.setdefault(ref.id, {"id": ref.id, "domains": [], "props": {}})
        row["props"].update(primitive_props({"overview": overview, "overview_caveats": list(get(profile, "caveats") or [])}))
    if competitor_rows:
        conn.write_batches(Q_COMPETITOR_META, list(competitor_rows.values()), rid=request_id)

    matrix = meta.get("comparison_matrix") if isinstance(meta.get("comparison_matrix"), dict) else {}
    pilot = index.pilot
    assessments = []
    for dim in matrix.get("dimensions") or []:
        dimension = (get(dim, "dimension") or "").strip()
        dimension_id = slugify(dimension)
        if not dimension_id:
            continue
        for cell in get(dim, "cells") or []:
            ref = index.resolve(get(cell, "competitor"))
            if ref is None or ref.label != COMPANY or (pilot and ref.id == pilot.id):
                continue
            assessments.append(
                {
                    "key": stable_id(key, dimension_id, ref.id),
                    "company_id": ref.id,
                    "dimension_id": dimension_id,
                    "dimension": dimension,
                    "evidence_ids": [e for e in get(cell, "evidence_ids") or [] if isinstance(e, str)],
                    "props": primitive_props(
                        {
                            "dimension": dimension,
                            "competitor": ref.name,
                            "relative_to_target": get(cell, "relative_to_target") or "not_established",
                            "summary": get(cell, "summary"),
                            "confidence": _number(get(cell, "confidence")),
                            "target_position": get(dim, "target_position"),
                            "takeaway": get(dim, "takeaway"),
                            **_run_props(request_id, workspace_id, COMPETITOR_WORKER, seen_at),
                        }
                    ),
                }
            )
    if assessments:
        conn.write_batches(Q_ASSESSMENTS, assessments, key=key, pilot=pilot.id if pilot else None)
    return {"competitor_details": len(competitor_rows), "assessments": len(assessments)}


def ingest_customer_metadata(
    meta: dict[str, Any], *, conn: GraphConnection, key: str, request_id: str, workspace_id: str | None,
    index: EntityIndex, seen_at: str,
) -> dict[str, int]:
    """themes -> ThemeObservation (OF_THEME a catalog Theme when the label matches one);
    trend_series -> SearchTerm + TrendSeries (SearchTerm REFERS_TO a known entity);
    stance_counts -> (WorkerResult)-[:MEASURED_STANCE {positive, neutral, negative, mixed, total}]->(entity).

    Theme labels are written by the analyst model, so they never become Theme nodes themselves.
    """
    run = _run_props(request_id, workspace_id, CUSTOMER_WORKER, seen_at)

    theme_rows = []
    for theme in meta.get("themes") or []:
        label = (get(theme, "theme_label") or "").strip()
        if not label:
            continue
        mix = get(theme, "sentiment_mix") or {}
        theme_rows.append(
            {
                "key": stable_id(key, slugify(label)),
                "themes": sorted({r.id for r in index.match(label) if r.label == THEME}),
                "props": primitive_props(
                    {
                        "label": label,
                        "count": get(theme, "count"),
                        "share": _number(get(theme, "share")),
                        "recent_growth": _number(get(theme, "recent_growth")),
                        **{f"sentiment_{s}": _number(mix.get(s)) for s in _STANCES if isinstance(mix, dict)},
                        **run,
                    }
                ),
            }
        )
    if theme_rows:
        conn.write_batches(Q_THEME_OBSERVATIONS, theme_rows, key=key)

    trend_rows, refers = [], {}
    for series in meta.get("trend_series") or []:
        term = (get(series, "keyword") or "").strip()
        term_id = slugify(term)
        if not term_id:
            continue
        dates, values = [], []
        for point in get(series, "points") or []:
            if isinstance(point, (list, tuple)) and len(point) == 2 and _number(point[1]) is not None:
                dates.append(str(point[0])[:10])
                values.append(_number(point[1]))  # floats only: Neo4j lists must be homogeneous
        if not values:
            continue
        geo, timeframe = get(series, "geo"), get(series, "timeframe")
        trend_rows.append(
            {
                "key": stable_id(key, term_id, geo, timeframe),
                "term_id": term_id,
                "term": term,
                "props": primitive_props(
                    {
                        "keyword": term,
                        "geo": geo,
                        "timeframe": timeframe,
                        "granularity": get(series, "granularity"),
                        "source": get(series, "source"),
                        "dates": dates,
                        "values": values,
                        "points": len(values),
                        "first_value": values[0],
                        "latest_value": values[-1],
                        "peak_value": max(values),
                        **run,
                    }
                ),
            }
        )
        ref = index.resolve(term)
        if ref is not None:
            refers.setdefault(ref.label, []).append({"source": term_id, "entity_id": ref.id})
    if trend_rows:
        conn.write_batches(Q_TRENDS, trend_rows, key=key)
        _write_links(conn, refers, "SearchTerm", "id", "REFERS_TO")

    stances: dict[str, list[dict[str, Any]]] = {}
    counts = meta.get("stance_counts") if isinstance(meta.get("stance_counts"), dict) else {}
    for entity, tally in counts.items():
        ref = index.resolve(entity)
        if ref is None or ref.label not in MEASURABLE_LABELS or not isinstance(tally, dict):
            continue
        total = tally.get("total")
        if not isinstance(total, int) or total <= 0:
            continue
        props = {s: int(tally.get(s) or 0) for s in _STANCES}
        stances.setdefault(ref.label, []).append(
            {"source": key, "entity_id": ref.id, "props": {**props, "total": total, "request_id": request_id}}
        )
    stance_count = _write_links(conn, stances, "WorkerResult", "key", "MEASURED_STANCE", edge_props=True)

    return {"theme_observations": len(theme_rows), "trend_series": len(trend_rows), "stance_tallies": stance_count}


# ============================================================================= verification

Q_VERIFICATION = """
MATCH (r:BusinessRequest {request_id: $rid})
MERGE (v:VerificationResult {id: $vid})
SET v += $props
MERGE (r)-[:VERIFIED_BY]->(v)
WITH v
UNWIND $verdicts AS row
MATCH (f:Finding {finding_id: row.finding_id})
MERGE (v)-[j:JUDGED]->(f)
SET j += row.props
FOREACH (_ IN CASE WHEN f.judged_at IS NULL OR f.judged_at <= $completed_at THEN [1] ELSE [] END |
  SET f.verification_status = row.props.status,
      f.evidence_quality = row.props.evidence_quality,
      f.verified_confidence = row.props.confidence,
      f.judged_at = $completed_at)
"""

Q_RETRIES = """
MATCH (v:VerificationResult {id: $vid})
UNWIND $rows AS row
MERGE (wk:Worker {type: row.worker})
MERGE (v)-[x:REQUESTED_RETRY]->(wk)
SET x.reason = row.reason
"""


def ingest_verification(conn: GraphConnection, verification: Any, *, request_id: str | None = None) -> int:
    """VerificationResult -> verification node + JUDGED edges. The latest verdict is copied onto each Finding."""
    rid = request_id or get(verification, "research_run_id")
    completed = iso(get(verification, "completed_at")) or now_iso()
    vid = f"{rid}|{completed}"
    decision = get(verification, "decision")
    control = get(verification, "control_summary")

    verdicts = [
        {
            "finding_id": get(v, "finding_id"),
            "props": primitive_props(
                {
                    "status": get(v, "verification_status"),
                    "evidence_quality": get(v, "evidence_quality"),
                    "confidence": get(v, "confidence"),
                    "worker": get(v, "worker"),
                    "contradictions": list(get(v, "contradictions") or []),
                    "missing_information": list(get(v, "missing_information") or []),
                }
            ),
        }
        for v in get(verification, "findings") or []
    ]
    conn.write(
        Q_VERIFICATION,
        rid=rid,
        vid=vid,
        completed_at=completed,
        verdicts=verdicts,
        props=primitive_props(
            {
                "request_id": rid,
                "status": get(decision, "status"),
                "summary": get(decision, "summary"),
                "completed_at": completed,
                "missing_information": list(get(control, "missing_information") or []),
                "conflicts": list(get(control, "conflicts") or []),
                "failures": list(get(control, "failures") or []),
                "next_research_targets": list(get(control, "next_research_targets") or []),
            }
        ),
    )
    retries = [
        {"worker": enum_value(get(a, "worker")), "reason": get(a, "reason")}
        for a in get(decision, "targeted_actions") or []
        if get(a, "worker")
    ]
    if retries:
        conn.write(Q_RETRIES, vid=vid, rows=retries)
    return len(verdicts)


# ============================================================================= cross-signal

Q_SIGNALS = """
MATCH (r:BusinessRequest {request_id: $rid})
UNWIND $rows AS row
MERGE (s:CrossSignal {signal_id: row.signal_id})
SET s += row.props, s.request_id = $rid, s.workspace_id = $ws
MERGE (r)-[:HAS_SIGNAL]->(s)
FOREACH (w IN row.workers |
  MERGE (wk:Worker {type: w})
  MERGE (s)-[:SPANS]->(wk))
WITH s, row
UNWIND row.support AS sup
MATCH (f:Finding {finding_id: sup.finding_id})
MERGE (s)-[b:BASED_ON]->(f)
SET b.relevance = sup.relevance
"""

_SIGNAL_FIELDS = (
    "signal_type", "title", "signal", "interpretation", "confidence", "evidence_quality",
    "strategic_relevance", "implications", "decision_relevance", "opportunity_relevance",
    "risk_relevance", "affected_business_areas", "decision_areas", "decision_question",
    "urgency", "dependencies", "evidence_gaps",
)


def _signals_of(output: Any) -> list[Any]:
    for name in ("signals", "cross_signals"):
        value = get(output, name)
        if value:
            return list(value)
    return []


def ingest_cross_signal(conn: GraphConnection, output: Any, *, request_id: str, workspace_id: str | None) -> int:
    """CrossSignalOutput (model or dict) -> CrossSignal nodes, BASED_ON findings, SPANS workers."""
    rows = []
    for sig in _signals_of(output):
        signal_id = get(sig, "signal_id")
        if not signal_id:
            continue
        rows.append(
            {
                "signal_id": signal_id,
                "props": primitive_props({name: get(sig, name) for name in _SIGNAL_FIELDS}),
                "workers": [enum_value(w) for w in get(sig, "domains_involved") or []],
                "support": [
                    {"finding_id": get(s, "finding_id"), "relevance": get(s, "relevance")}
                    for s in get(sig, "supporting_findings") or []
                ],
            }
        )
    if rows:
        conn.write(Q_SIGNALS, rid=request_id, ws=workspace_id, rows=rows)
    return len(rows)


# ============================================================================= decision analysis

Q_ANALYSIS = """
MATCH (r:BusinessRequest {request_id: $rid})
MERGE (a:DecisionAnalysis {analysis_id: $aid})
SET a += $props
MERGE (r)-[:HAS_ANALYSIS]->(a)
"""

_KINDS = {  # list field -> (label, key property, relationship from DecisionAnalysis)
    "opportunities": ("Opportunity", "opportunity_id", "IDENTIFIED"),
    "risks": ("Risk", "risk_id", "IDENTIFIED"),
    "recommended_actions": ("RecommendedAction", "action_id", "RECOMMENDS"),
    "options": ("DecisionOption", "key", "PRESENTS"),
    "question_recommendations": ("Recommendation", "key", "ANSWERS"),
}
_PRIORITY_TARGETS = {
    "opportunity": ("Opportunity", "opportunity_id"),
    "risk": ("Risk", "risk_id"),
    "action": ("RecommendedAction", "action_id"),
}


def _item_query(label: str, key_prop: str, rel: str) -> str:
    return (
        "UNWIND $rows AS row "
        "MATCH (a:DecisionAnalysis {analysis_id: $aid}) "
        f"MERGE (x:{label} {{{key_prop}: row.id}}) "
        "SET x += row.props, x.request_id = $rid, x.workspace_id = $ws "
        f"MERGE (a)-[:{rel}]->(x) "
        "WITH x, row UNWIND row.signal_ids AS sid "
        "MATCH (s:CrossSignal {signal_id: sid}) "
        "MERGE (x)-[:SUPPORTED_BY_SIGNAL]->(s)"
    )


def _texts(items: Any) -> list[str]:
    return [get(i, "text") if not isinstance(i, str) else i for i in items or [] if (get(i, "text") or isinstance(i, str))]


def _item_rows(field: str, items: list[Any], aid: str) -> list[dict[str, Any]]:
    rows = []
    for n, item in enumerate(items):
        if field == "options":
            row_id = f"{aid}|{get(item, 'key')}"
            props = primitive_props(
                {
                    "option_key": get(item, "key"),
                    "title": get(item, "title"),
                    "thesis": get(item, "thesis"),
                    "what_we_would_do": list(get(item, "what_we_would_do") or []),
                    "trade_offs": list(get(item, "trade_offs") or []),
                    "opportunities": _texts(get(item, "opportunities")),
                    "risks": _texts(get(item, "risks")),
                    "would_change_if": get(item, "would_change_if"),
                    "cost_of_waiting": get(item, "cost_of_waiting"),
                }
            )
            signal_ids = list(get(item, "supporting_signal_ids") or [])
        elif field == "question_recommendations":
            rec = get(item, "recommendation") or {}
            row_id = f"{aid}|{n}"
            props = primitive_props(
                {
                    "question": get(item, "question"),
                    "decision": get(rec, "decision"),
                    "rationale": get(rec, "rationale"),
                    "actions": list(get(rec, "actions") or []),
                    "opportunities": list(get(rec, "opportunities") or []),
                    "risks": list(get(rec, "risks") or []),
                    "success_criteria": list(get(rec, "success_criteria") or []),
                    "limitations": list(get(rec, "limitations") or []),
                    "confidence": get(rec, "confidence"),
                    "impact": get(rec, "impact"),
                    "feasibility": get(rec, "feasibility"),
                }
            )
            signal_ids = list(get(rec, "supporting_signal_ids") or [])
        else:
            _, key_prop, _ = _KINDS[field]
            row_id = get(item, key_prop)
            data = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            signal_ids = list(data.pop("supporting_signal_ids", None) or [])
            for drop in (key_prop, "related_opportunity_ids", "related_risk_ids"):
                data.pop(drop, None)
            props = primitive_props(data)
        if row_id:
            rows.append({"id": row_id, "props": props, "signal_ids": signal_ids})
    return rows


def ingest_decision(conn: GraphConnection, result: Any, *, request_id: str, workspace_id: str | None) -> int:
    """DecisionAnalysisResult (model or dict) -> analysis, opportunities, risks, actions, options, recommendations."""
    aid = get(result, "analysis_id") or stable_id(request_id, "analysis")
    conn.write(
        Q_ANALYSIS,
        rid=request_id,
        aid=aid,
        props=primitive_props(
            {
                "status": get(result, "status"),
                "business_goal": get(result, "business_goal"),
                "executive_summary": get(result, "executive_summary"),
                "decision_questions": list(get(result, "decision_questions") or []),
                "additional_information_needed": list(get(result, "additional_information_needed") or []),
                "limitations": list(get(result, "limitations") or []),
                "warnings": list(get(result, "warnings") or []),
                "created_at": get(result, "created_at"),
                "request_id": request_id,
                "workspace_id": workspace_id,
            }
        ),
    )
    written = 0
    for field, (label, key_prop, rel) in _KINDS.items():
        rows = _item_rows(field, list(get(result, field) or []), aid)
        if rows:
            conn.write_batches(_item_query(label, key_prop, rel), rows, aid=aid, rid=request_id, ws=workspace_id)
            written += len(rows)

    # action -> opportunity / risk links
    addresses, mitigates = [], []
    for action in get(result, "recommended_actions") or []:
        aid_ = get(action, "action_id")
        addresses += [{"action_id": aid_, "target": t} for t in get(action, "related_opportunity_ids") or []]
        mitigates += [{"action_id": aid_, "target": t} for t in get(action, "related_risk_ids") or []]
    if addresses:
        conn.write_batches(
            "UNWIND $rows AS row MATCH (x:RecommendedAction {action_id: row.action_id}) "
            "MATCH (o:Opportunity {opportunity_id: row.target}) MERGE (x)-[:ADDRESSES]->(o)",
            addresses,
        )
    if mitigates:
        conn.write_batches(
            "UNWIND $rows AS row MATCH (x:RecommendedAction {action_id: row.action_id}) "
            "MATCH (k:Risk {risk_id: row.target}) MERGE (x)-[:MITIGATES]->(k)",
            mitigates,
        )

    # option -> metric citations (MetricRef)
    cites = [
        {"key": f"{aid}|{get(opt, 'key')}", "metric_id": get(ref, "metric_id"), "entity": get(ref, "entity") or ""}
        for opt in get(result, "options") or []
        for ref in get(opt, "metric_refs") or []
        if get(ref, "metric_id")
    ]
    if cites:
        conn.write_batches(
            "UNWIND $rows AS row MATCH (x:DecisionOption {key: row.key}) "
            "MERGE (m:Metric {metric_id: row.metric_id}) MERGE (x)-[:CITES_METRIC {entity: row.entity}]->(m)",
            cites,
        )

    # priorities, grouped by target label
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in get(result, "priorities") or []:
        item_type = enum_value(get(p, "item_type"))
        if item_type not in _PRIORITY_TARGETS:
            continue
        grouped.setdefault(item_type, []).append(
            {
                "item_id": get(p, "item_id"),
                "props": primitive_props(
                    {
                        name: get(p, name)
                        for name in ("priority", "strategic_fit", "impact", "urgency", "feasibility", "rationale", "key_uncertainties")
                    }
                ),
            }
        )
    for item_type, rows in grouped.items():
        label, key_prop = _PRIORITY_TARGETS[item_type]
        conn.write_batches(
            "UNWIND $rows AS row MATCH (a:DecisionAnalysis {analysis_id: $aid}) "
            f"MATCH (x:{label} {{{key_prop}: row.item_id}}) "
            "MERGE (a)-[p:PRIORITIZED]->(x) SET p += row.props",
            rows,
            aid=aid,
        )
    return written


# ============================================================================= domain projection

Q_COMPETES = f"""
MATCH (r:BusinessRequest {{request_id: $rid}})-[:ABOUT_COMPANY]->(p:Company)
MATCH (r)-[:COMPARES_AGAINST]->(c:Company)
MATCH (f:Finding {{request_id: $rid}})-[:ABOUT]->(c)
WHERE f.verification_status = 'verified' AND c <> p
WITH p, c, collect(DISTINCT f.finding_id) AS fids
MERGE (p)-[e:COMPETES_WITH]->(c)
SET e.finding_ids = {_dedupe("e.finding_ids", "fids")},
    e.request_ids = {_dedupe("e.request_ids", "[$rid]")},
    e.first_seen = coalesce(e.first_seen, $now),
    e.last_seen = $now
RETURN count(e) AS n
"""

Q_OPERATES = f"""
MATCH (f:Finding {{request_id: $rid}})-[:ABOUT]->(c:Company)
WHERE f.verification_status = 'verified'
MATCH (f)-[:ABOUT]->(m:Market)
WITH c, m, collect(DISTINCT f.finding_id) AS fids
MERGE (c)-[e:OPERATES_IN]->(m)
SET e.finding_ids = {_dedupe("e.finding_ids", "fids")},
    e.request_ids = {_dedupe("e.request_ids", "[$rid]")},
    e.first_seen = coalesce(e.first_seen, $now),
    e.last_seen = $now
RETURN count(e) AS n
"""


def project_domain(conn: GraphConnection, *, request_id: str) -> dict[str, int]:
    """Write domain edges that verified findings support. Each edge lists the findings behind it."""
    now = now_iso()
    competes = conn.read_write(Q_COMPETES, rid=request_id, now=now)
    operates = conn.read_write(Q_OPERATES, rid=request_id, now=now)
    return {
        "competes_with": competes[0]["n"] if competes else 0,
        "operates_in": operates[0]["n"] if operates else 0,
    }


# ============================================================================= metric registry

Q_METRICS = """
UNWIND $rows AS row
MERGE (m:Metric {metric_id: row.metric_id})
SET m += row.props
FOREACH (_ IN CASE WHEN row.owner IS NULL THEN [] ELSE [1] END |
  MERGE (wk:Worker {type: row.owner})
  MERGE (m)-[:OWNED_BY]->(wk))
"""

_WORKERS = {"internal_intelligence", "competitor_intelligence", "market_intelligence", "customer_trends"}


def sync_metric_registry(conn: GraphConnection) -> int:
    """metrics/registry.yaml -> Metric nodes with definitions and owners."""
    try:
        from edrak.metrics.registry import get_registry
    except Exception as exc:  # noqa: BLE001
        logger.info("Metric registry not importable, skipping: %s", exc)
        return 0
    rows = []
    for m in get_registry().metrics.values():
        rows.append(
            {
                "metric_id": m.id,
                "owner": m.owner if m.owner in _WORKERS else None,
                "props": primitive_props(
                    {
                        "name": m.label("en"),
                        "aliases": list(m.aliases),
                        "definition": m.definition,
                        "unit_kind": m.unit_kind,
                        "period": m.period,
                        "comparator": m.comparator,
                        "visual": m.visual,
                    }
                ),
            }
        )
    return conn.write_batches(Q_METRICS, rows)
