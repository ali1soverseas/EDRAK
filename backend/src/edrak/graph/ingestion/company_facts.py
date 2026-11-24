"""Approved company research facts as graph nodes and typed links.

Company research (``edrak.company_research``) collects facts about the user's own company and a person
approves them. This module writes the approved ones to the graph and removes them again when they are
rejected or changed. It knows nothing about SQLite or the research agent: the caller hands it plain
``CompanyFactRecord`` objects, which keeps the dependency pointing from company research to the graph.

* A fact is always written as a ``CompanyFact`` node attached to its ``Company`` with ``HAS_FACT``.
* A fact whose key names a person, an organisation or a place also gets a typed link to that entity
  (``LED_BY`` a ``Person``, ``PARTNERS_WITH`` a ``Company``, ``LOCATED_IN`` a ``Location``). The value is
  free text, so the link is keyed by the slug of the name and no entity resolution is attempted.
* Every link carries ``fact_id`` and ``workspace_id``, so retracting a fact removes exactly its own links.
* Every write is a ``MERGE`` on a stable id, so publishing the same fact twice changes nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..connection import GraphConnection
from ..entities import COMPANY, now_iso, slugify, stable_id

SOURCE = "company_research"
PERSON = "Person"
LOCATION = "Location"

# A name longer than this is a sentence, not an entity: the fact is still written, but no link is made.
MAX_NAME_CHARS = 80


@dataclass(frozen=True)
class LinkRule:
    """How one (category, key) becomes a link from the company: ``(Company)-[rel]->(target_label)``."""

    relationship: str
    target_label: str
    role: str | None = None  # LED_BY carries the person's role
    kind_from_key: bool = False  # LOCATED_IN carries the key as its kind


_LEADERS = {
    "ceo": "CEO",
    "cfo": "CFO",
    "cto": "CTO",
    "coo": "COO",
    "chairman": "Chairman",
    "managing_director": "Managing Director",
    "board_member": "Board member",
    "executive_team_member": "Executive",
}

LINK_RULES: dict[tuple[str, str], LinkRule] = {
    **{("leadership", key): LinkRule("LED_BY", PERSON, role=role) for key, role in _LEADERS.items()},
    ("leadership", "founder"): LinkRule("FOUNDED_BY", PERSON),
    ("ownership_and_funding", "shareholder"): LinkRule("HAS_SHAREHOLDER", COMPANY),
    ("ownership_and_funding", "investor"): LinkRule("FUNDED_BY", COMPANY),
    ("ownership_and_funding", "subsidiary"): LinkRule("HAS_SUBSIDIARY", COMPANY),
    ("ownership_and_funding", "parent_company"): LinkRule("SUBSIDIARY_OF", COMPANY),
    ("partners", "partner"): LinkRule("PARTNERS_WITH", COMPANY),
    ("partners", "supplier"): LinkRule("SUPPLIED_BY", COMPANY),
    ("partners", "distributor"): LinkRule("DISTRIBUTED_BY", COMPANY),
    ("customers", "customer_name"): LinkRule("HAS_CUSTOMER", COMPANY),
    **{
        ("locations", key): LinkRule("LOCATED_IN", LOCATION, kind_from_key=True)
        for key in ("headquarters", "branch", "factory", "warehouse", "country_presence")
    },
}

# Every relationship type this module writes. Retraction deletes only these, so it can never touch an edge
# that another part of the graph created.
LINK_TYPES = sorted({rule.relationship for rule in LINK_RULES.values()})


class CompanyFactRecord(BaseModel):
    """One approved fact, as the graph needs it."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)  # the SQLite id; the graph node id is derived from it
    category: str
    key: str
    label: str | None = None
    value: str  # text, or a JSON array of strings when value_type is "list"
    value_type: str = "text"
    quote: str
    source_url: str
    pdf_page: int | None = None
    source_tier: str
    confidence: str
    edited_by_user: bool = False
    conflict_group: str | None = None
    observed_at: str | None = None

    def items(self) -> list[str]:
        """The value as a list: a ``list`` fact fans out to its items, anything else is one item."""
        if self.value_type == "list":
            try:
                parsed = json.loads(self.value)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [self.value.strip()] if self.value.strip() else []


@dataclass
class PublishResult:
    facts: int = 0
    links: int = 0
    skipped_links: int = 0  # values that look like a sentence rather than a name, or point back at the company


def fact_node_id(workspace_id: str, fact_id: str) -> str:
    return stable_id(workspace_id, "company_fact", fact_id)


# ----------------------------------------------------------------------------- rows


def fact_props(workspace_id: str, fact: CompanyFactRecord) -> dict[str, Any]:
    """The properties of a CompanyFact node. ``SET f += map`` removes a property whose value is ``None``,
    so an edit that clears the PDF page or the conflict group never leaves the old one behind."""
    return {
        "id": fact_node_id(workspace_id, fact.fact_id),
        "fact_id": fact.fact_id,
        "workspace_id": workspace_id,
        "category": fact.category,
        "key": fact.key,
        "label": fact.label,
        "value": fact.value,
        "value_type": fact.value_type,
        "quote": fact.quote,
        "source_url": fact.source_url,
        "pdf_page": fact.pdf_page,
        "source_tier": fact.source_tier,
        "confidence": fact.confidence,
        "edited_by_user": fact.edited_by_user,
        "conflict_group": fact.conflict_group,
        "observed_at": fact.observed_at,
        "source": SOURCE,
    }


def link_rows(workspace_id: str, company_id: str, facts: Iterable[CompanyFactRecord]) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], int]:
    """Group the typed links by (relationship, target label). Returns the groups and how many values were skipped."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    skipped = 0
    for fact in facts:
        rule = LINK_RULES.get((fact.category, fact.key))
        if rule is None:
            continue
        for name in fact.items():
            target_id = slugify(name)
            if not target_id or len(name) > MAX_NAME_CHARS or target_id == company_id:
                skipped += 1
                continue
            groups.setdefault((rule.relationship, rule.target_label), []).append(
                {
                    "fact_id": fact.fact_id,
                    "target_id": target_id,
                    "name": name,
                    "role": rule.role,
                    "kind": fact.key if rule.kind_from_key else None,
                }
            )
    return groups, skipped


# ----------------------------------------------------------------------------- queries

Q_FACTS = """
UNWIND $rows AS row
MATCH (c:Company {id: $company_id}), (w:Workspace {id: $ws})
MERGE (f:CompanyFact {id: row.id})
SET f += row.props
MERGE (f)-[:IN_WORKSPACE]->(w)
MERGE (c)-[:HAS_FACT]->(f)
"""

Q_COMPANY = """
MERGE (w:Workspace {id: $ws})
MERGE (c:Company {id: $company_id})
  ON CREATE SET c.name = $company_name
"""

Q_DROP_LINKS = """
MATCH (:Company)-[r]->()
WHERE r.fact_id IN $fact_ids AND r.workspace_id = $ws AND type(r) IN $types
DELETE r
"""

Q_DROP_FACTS = """
MATCH (f:CompanyFact)
WHERE f.id IN $node_ids AND f.workspace_id = $ws
DETACH DELETE f
"""

# Only entities this module created, and only once nothing points at them any more. A shared Company node
# is never removed: it may belong to competitor analyses.
Q_SWEEP = """
MATCH (t)
WHERE (t:Person OR t:Location) AND t.source = $source AND NOT (t)--()
DELETE t
"""


def _link_query(relationship: str, target_label: str) -> str:
    # Relationship types and labels cannot be parameters in Cypher, so they are formatted in from the
    # fixed LINK_RULES table above (never from a fact), and the rest stay parameters.
    on_create = "t.name = row.name" if target_label == COMPANY else "t.name = row.name, t.source = $source"
    return f"""
UNWIND $rows AS row
MATCH (c:Company {{id: $company_id}})
MERGE (t:{target_label} {{id: row.target_id}})
  ON CREATE SET {on_create}
MERGE (c)-[r:{relationship} {{fact_id: row.fact_id}}]->(t)
SET r.workspace_id = $ws, r.role = row.role, r.kind = row.kind, r.updated_at = $now
"""


# ----------------------------------------------------------------------------- writers


def publish_facts(
    conn: GraphConnection,
    workspace_id: str,
    company_name: str,
    facts: Sequence[CompanyFactRecord],
) -> PublishResult:
    """Write approved facts and their links. Safe to repeat: it replaces a fact's links before writing them."""
    result = PublishResult()
    if not facts:
        return result
    company_id = slugify(company_name)
    if not company_id:
        raise ValueError("company_name must contain at least one letter or digit")

    conn.write(Q_COMPANY, ws=workspace_id, company_id=company_id, company_name=company_name)

    rows = [{"id": fact_node_id(workspace_id, f.fact_id), "props": fact_props(workspace_id, f)} for f in facts]
    conn.write_batches(Q_FACTS, rows, ws=workspace_id, company_id=company_id)
    result.facts = len(rows)

    # An edited fact may point at different entities than before, so its old links go first.
    conn.write(Q_DROP_LINKS, fact_ids=[f.fact_id for f in facts], ws=workspace_id, types=LINK_TYPES)

    groups, result.skipped_links = link_rows(workspace_id, company_id, facts)
    now = now_iso()
    for (relationship, target_label), rows_ in groups.items():
        conn.write_batches(
            _link_query(relationship, target_label), rows_, ws=workspace_id, company_id=company_id, source=SOURCE, now=now
        )
        result.links += len(rows_)

    conn.write(Q_SWEEP, source=SOURCE)
    return result


def retract_facts(conn: GraphConnection, workspace_id: str, fact_ids: Sequence[str]) -> int:
    """Remove facts and their links from the graph. Returns how many fact ids were asked for."""
    ids = [fact_id for fact_id in fact_ids if fact_id]
    if not ids:
        return 0
    conn.write(Q_DROP_LINKS, fact_ids=ids, ws=workspace_id, types=LINK_TYPES)
    conn.write(Q_DROP_FACTS, node_ids=[fact_node_id(workspace_id, i) for i in ids], ws=workspace_id)
    conn.write(Q_SWEEP, source=SOURCE)
    return len(ids)
