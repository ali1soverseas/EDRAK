"""Workspace-scoped profile snapshots supplied by sample datasets."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .connection import GraphConnection
from .entities import slugify, stable_id


class ProfileMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    relation: Literal["serves", "considers"]


class ProfileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industries: list[str] = Field(default_factory=list)
    industry_other: str | None = None
    goals: list[str] = Field(default_factory=list)
    target_customers: str | None = None
    markets: list[ProfileMarket] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_markets(self) -> "ProfileSnapshot":
        ids = [slugify(market.name) for market in self.markets]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError("Profile markets must have distinct, non-empty identifiers")
        return self


def ingest_profile_snapshot(
    conn: GraphConnection, snapshot: ProfileSnapshot, workspace_id: str, request_id: str, company: str
) -> None:
    conn.write(
        """
        MATCH (w:Workspace {id: $ws}), (r:BusinessRequest {request_id: $request_id}),
              (c:Company {id: $company_id})
        MERGE (p:ProfileSnapshot {id: $id})
        SET p.workspace_id = $ws, p.request_id = $request_id, p.industries = $industries,
            p.industry_other = $industry_other, p.goals = $goals,
            p.target_customers = $target_customers
        MERGE (w)-[:HAS_PROFILE_SNAPSHOT]->(p)
        MERGE (r)-[:USES_PROFILE_SNAPSHOT]->(p)
        MERGE (p)-[:PROFILE_OF]->(c)
        WITH p
        OPTIONAL MATCH (p)-[old:PROFILE_MARKET]->()
        DELETE old
        WITH DISTINCT p
        UNWIND $markets AS market
        MERGE (m:Market {id: market.id})
        ON CREATE SET m.name = market.name
        MERGE (p)-[rel:PROFILE_MARKET]->(m)
        SET rel.relation = market.relation
        """,
        ws=workspace_id,
        request_id=request_id,
        company_id=slugify(company),
        id=stable_id(workspace_id, request_id, "profile_snapshot"),
        industries=snapshot.industries,
        industry_other=snapshot.industry_other,
        goals=snapshot.goals,
        target_customers=snapshot.target_customers,
        markets=[{"id": slugify(m.name), "name": m.name, "relation": m.relation} for m in snapshot.markets],
    )
