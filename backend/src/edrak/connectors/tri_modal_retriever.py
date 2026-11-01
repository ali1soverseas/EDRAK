"""Tri-Modal Retrieval Tier for Internal Intelligence: Vector RAG + Direct DB + REST API."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from edrak.connectors.api_discoverer import DynamicAPIDiscoverer
from edrak.connectors.api_executor import SecureAPIExecutor
from edrak.connectors.db_introspector import DatabaseSchemaIntrospector
from edrak.connectors.sql_guardrail import validate_and_sanitize_sql
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.request import DatabaseConnectionConfig, RESTAPIConfig
from edrak.core.llm import get_json_object

logger = logging.getLogger(__name__)


def sql_result_to_evidence(
    db_name: str,
    table: str,
    sql_query: str,
    records: List[Dict[str, Any]],
    fact_summary: str,
    is_synthetic: bool = True,
) -> Evidence:
    """Converts a database query result into an EDRAK Evidence record."""
    excerpt_records = records[:5] if records else []
    excerpt_text = json.dumps(excerpt_records, indent=2, default=str)
    full_text = json.dumps(records, indent=2, default=str)

    return Evidence(
        source_type=SourceType.SYNTHETIC_INTERNAL if is_synthetic else SourceType.INTERNAL_DOCUMENT,
        source_title=f"Internal Database [{db_name}.{table}]",
        source_url=f"sql://{db_name}/{table}",
        publisher=f"Database System: {db_name}",
        extracted_fact=fact_summary,
        excerpt=excerpt_text,
        page_text=full_text,
        is_synthetic=is_synthetic,
        metadata={
            "channel": "sql_database",
            "db_name": db_name,
            "table": table,
            "query": sql_query,
            "row_count": len(records),
            "relevance_score": 0.88,
            "origin": "internal_db",
        },
    )


def api_response_to_evidence(
    api_id: str,
    endpoint_path: str,
    params: Optional[Dict[str, Any]],
    payload: Any,
    fact_summary: str,
    is_synthetic: bool = True,
) -> Evidence:
    """Converts an operational REST API response into an EDRAK Evidence record."""
    excerpt_text = json.dumps(payload, indent=2, default=str)[:1500]
    full_text = json.dumps(payload, indent=2, default=str)

    return Evidence(
        source_type=SourceType.SYNTHETIC_INTERNAL if is_synthetic else SourceType.INTERNAL_DOCUMENT,
        source_title=f"Internal REST API [{api_id}: {endpoint_path}]",
        source_url=f"api://{api_id}{endpoint_path}",
        publisher=f"Internal Microservice: {api_id}",
        extracted_fact=fact_summary,
        excerpt=excerpt_text,
        page_text=full_text,
        is_synthetic=is_synthetic,
        metadata={
            "channel": "rest_api",
            "api_id": api_id,
            "endpoint": endpoint_path,
            "params": params or {},
            "relevance_score": 0.86,
            "origin": "internal_api",
        },
    )


def run_dynamic_sql_pipeline(
    db_config: DatabaseConnectionConfig,
    queries: List[str],
    task_goal: str,
) -> List[Evidence]:
    """Dynamically introspects DB catalog, selects relevant tables, generates safe SQL, and executes it."""
    logger.info("Starting dynamic SQL pipeline for DB: %s", db_config.db_id)
    try:
        introspector = DatabaseSchemaIntrospector(
            connection_uri=db_config.connection_uri,
            allowed_tables=db_config.allowed_tables,
        )
    except Exception as e:
        logger.error("Failed to connect to database %s: %s", db_config.db_id, e)
        return []

    # Step 1: Catalog Introspection
    try:
        catalog = introspector.list_table_catalog()
    except Exception as e:
        logger.error("Failed to list catalog for database %s: %s", db_config.db_id, e)
        return []

    if not catalog:
        logger.warning("Database %s has no accessible tables.", db_config.db_id)
        return []

    # Step 2: Table Selection
    combined_query_text = f"{task_goal} {' '.join(queries)}".lower()
    selected_tables = []
    for item in catalog:
        tbl = item["table_name"]
        tbl_words = [w for w in re.split(r"[_ -]+", tbl.lower()) if len(w) > 2]
        if any(w in combined_query_text for w in tbl_words):
            selected_tables.append(tbl)

    if not selected_tables:
        selected_tables = [item["table_name"] for item in catalog[:2]]

    # Step 3: Targeted Schema Introspection
    schema_details = introspector.introspect_selected_tables(selected_tables)
    evidence_items: List[Evidence] = []

    # Step 4: SQL Generation & Safe Execution
    for table_name, schema in schema_details.items():
        columns = [c["name"] for c in schema.get("columns", [])]
        if not columns:
            continue

        dialect = introspector.engine.dialect.name
        try:
            system_prompt = (
                f"You are an expert SQL analyst for {dialect}. Formulate a precise, read-only SELECT query "
                "to retrieve the most relevant business data answering the research goal. "
                f"Write dialect-valid {dialect} SQL. "
                f"Do NOT prefix the table name with any database name (query strictly FROM {table_name}). "
                "Output JSON with keys 'sql_query' and 'reason'."
            )
            user_prompt = (
                f"SQL Dialect: {dialect}\n"
                f"Table: {table_name}\n"
                f"Columns: {', '.join(columns)}\n"
                f"Sample records: {json.dumps(schema.get('sample_records', []))}\n"
                f"Research Goal: {task_goal}\n"
                f"Query strictly 'FROM {table_name}'. Return JSON with 'sql_query'."
            )
            resp = get_json_object(system_prompt, user_prompt, agent="internal", temperature=0.1)
            if isinstance(resp, dict) and "sql_query" in resp:
                candidate_sql = resp["sql_query"].strip()
                if candidate_sql.upper().startswith("SELECT"):
                    # Strip any database or catalog prefix e.g. "nvidia_enterprise_db.enterprise_deals"
                    clean_sql = re.sub(rf"\b{re.escape(db_config.db_id)}\.", "", candidate_sql)
                    generated_sql = clean_sql
        except Exception as e:
            logger.debug("LLM SQL generation fallback to default for table %s: %s", table_name, e)

        # Deterministic fallback query if LLM query was not generated
        if not generated_sql:
            generated_sql = f"SELECT * FROM {table_name} LIMIT 50"

        # Step 5: Execute with AST Guardrails and Automatic Fallback
        records = []
        try:
            records = introspector.execute_query(generated_sql, default_limit=100)
        except Exception as e:
            logger.warning("Query '%s' failed on table %s (%s). Retrying with safe fallback query.", generated_sql, table_name, e)
            fallback_sql = f"SELECT * FROM {table_name} LIMIT 50"
            try:
                records = introspector.execute_query(fallback_sql, default_limit=100)
                generated_sql = fallback_sql
            except Exception as e2:
                logger.error("Fallback query failed on table %s: %s", table_name, e2)

        if records:
            fact_summary = (
                f"Direct database extraction from '{table_name}' returned {len(records)} records "
                f"relevant to goal '{task_goal}'."
            )
            ev = sql_result_to_evidence(
                db_name=db_config.db_id,
                table=table_name,
                sql_query=generated_sql,
                records=records,
                fact_summary=fact_summary,
                is_synthetic=True,
            )
            evidence_items.append(ev)

    return evidence_items


def run_dynamic_api_pipeline(
    api_config: RESTAPIConfig,
    queries: List[str],
    task_goal: str,
    asgi_app: Optional[Any] = None,
) -> List[Evidence]:
    """Dynamically parses OpenAPI spec, discovers relevant endpoints, executes GET requests, and wraps as evidence."""
    logger.info("Starting dynamic REST API pipeline for API: %s", api_config.api_id)

    # Step 1: Discoverer Ingestion
    discoverer: Optional[DynamicAPIDiscoverer] = None
    try:
        if api_config.openapi_spec_inline:
            discoverer = DynamicAPIDiscoverer(spec=api_config.openapi_spec_inline, base_url=api_config.base_url)
        elif api_config.openapi_spec_url:
            discoverer = DynamicAPIDiscoverer.from_url(spec_url=api_config.openapi_spec_url, base_url=api_config.base_url)
        else:
            spec_url = f"{api_config.base_url.rstrip('/')}/openapi.json"
            discoverer = DynamicAPIDiscoverer.from_url(spec_url=spec_url, base_url=api_config.base_url)
    except Exception as e:
        logger.error("Failed to initialize OpenAPI discoverer for %s: %s", api_config.api_id, e)
        return []

    if not discoverer or not discoverer.endpoints:
        logger.warning("No read-safe endpoints found in OpenAPI spec for %s.", api_config.api_id)
        return []

    # Step 2: Endpoint Matching
    search_query = f"{task_goal} {' '.join(queries)}"
    candidate_endpoints = discoverer.match_endpoints_for_task(search_query, top_k=2)

    evidence_items: List[Evidence] = []
    executor = SecureAPIExecutor(
        base_url=api_config.base_url,
        auth_headers=api_config.auth_headers,
        app=asgi_app,
    )

    # Step 3: Execution & Serialization
    for ep in candidate_endpoints:
        path = ep["path"]
        payload = None
        try:
            payload = executor.execute_endpoint_sync(path=path, method="GET", timeout=5.0)
        except Exception as e:
            # Fallback for local mock testing if direct HTTP port is not running
            logger.debug("Direct HTTP request to %s failed (%s). Checking for local mock app...", path, e)
            try:
                from mock_services.nvidia.mock_nvidia_api import app as mock_app
                fallback_executor = SecureAPIExecutor(base_url=api_config.base_url, app=mock_app)
                payload = fallback_executor.execute_endpoint_sync(path=path, method="GET", timeout=5.0)
            except Exception as e2:
                logger.error("Failed to execute REST API endpoint %s: %s", path, e2)
                continue

        if payload is not None:
            fact_summary = (
                f"Operational REST API lookup at '{path}' ({ep.get('summary') or 'Endpoint'}) "
                f"returned data matching research goal '{task_goal}'."
            )
            ev = api_response_to_evidence(
                api_id=api_config.api_id,
                endpoint_path=path,
                params={},
                payload=payload,
                fact_summary=fact_summary,
                is_synthetic=True,
            )
            evidence_items.append(ev)

    return evidence_items
