"""Integration test suite for Tri-Modal Internal Intelligence: RAG + Dynamic DB + REST API."""

import json
import os
from pathlib import Path
import pytest

from edrak.connectors.api_discoverer import DynamicAPIDiscoverer
from edrak.connectors.api_executor import SecureAPIExecutor
from edrak.connectors.db_introspector import DatabaseSchemaIntrospector
from edrak.connectors.sql_guardrail import validate_and_sanitize_sql
from edrak.connectors.tri_modal_retriever import (
    run_dynamic_sql_pipeline,
    run_dynamic_api_pipeline,
    sql_result_to_evidence,
    api_response_to_evidence,
)
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.request import DatabaseConnectionConfig, RESTAPIConfig
from mock_services.nvidia.seed_nvidia_db import seed_database
from mock_services.nvidia.mock_nvidia_api import app as mock_api_app


@pytest.fixture(scope="module")
def nvidia_db_path(tmp_path_factory):
    """Creates a temporary seeded NVIDIA SQLite database."""
    tmp_dir = tmp_path_factory.mktemp("nvidia_data")
    db_file = str(tmp_dir / "nvidia_test.db")
    seed_database(db_file)
    return db_file


@pytest.fixture(scope="module")
def nvidia_openapi_spec():
    """Returns the mock NVIDIA OpenAPI specification dict."""
    spec_path = Path(__file__).resolve().parents[1] / "mock_services" / "nvidia" / "nvidia_openapi_spec.json"
    with open(spec_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_sql_guardrail_safety():
    """Verifies that mutation queries are strictly rejected and SELECT queries are bound by LIMIT."""
    # Read query must pass and have LIMIT
    valid = validate_and_sanitize_sql("SELECT * FROM gpu_products", default_limit=25)
    assert "LIMIT 25" in valid

    # Mutations must raise PermissionError
    with pytest.raises(PermissionError):
        validate_and_sanitize_sql("DELETE FROM gpu_products WHERE 1=1")

    with pytest.raises(PermissionError):
        validate_and_sanitize_sql("DROP TABLE enterprise_deals")

    with pytest.raises(PermissionError):
        validate_and_sanitize_sql("INSERT INTO software_arr VALUES ('test')")

    with pytest.raises(PermissionError):
        validate_and_sanitize_sql("SELECT * FROM gpu_products; DROP TABLE gpu_products;")


def test_database_introspector(nvidia_db_path):
    """Verifies Tier 1 catalog listing and Tier 2 detailed schema introspection."""
    uri = f"sqlite:///{nvidia_db_path}"
    introspector = DatabaseSchemaIntrospector(connection_uri=uri)

    # Tier 1
    catalog = introspector.list_table_catalog()
    table_names = [c["table_name"] for c in catalog]
    assert "gpu_products" in table_names
    assert "enterprise_deals" in table_names
    assert "software_arr" in table_names

    # Tier 2
    details = introspector.introspect_selected_tables(["gpu_products"])
    assert "gpu_products" in details
    col_names = [c["name"] for c in details["gpu_products"]["columns"]]
    assert "product_name" in col_names
    assert "target_asp_usd" in col_names
    assert len(details["gpu_products"]["sample_records"]) > 0

    # Execute safe query
    records = introspector.execute_query("SELECT product_name, target_asp_usd FROM gpu_products LIMIT 2")
    assert len(records) == 2
    assert "product_name" in records[0]


def test_openapi_discoverer(nvidia_openapi_spec):
    """Verifies endpoint discovery and semantic matching."""
    discoverer = DynamicAPIDiscoverer(spec=nvidia_openapi_spec, base_url="http://127.0.0.1:8088")
    assert len(discoverer.endpoints) >= 3

    paths = [ep["path"] for ep in discoverer.endpoints]
    assert "/v1/telemetry/gpu-clusters" in paths
    assert "/v1/pricing/discount-matrix" in paths

    # Semantic query matching
    matches = discoverer.match_endpoints_for_task("gpu cluster fleet telemetry utilization", top_k=1)
    assert len(matches) == 1
    assert matches[0]["path"] == "/v1/telemetry/gpu-clusters"


def test_secure_api_executor_with_mock_app():
    """Verifies in-memory execution of endpoints via FastAPI mock app."""
    executor = SecureAPIExecutor(base_url="http://testserver", app=mock_api_app)
    data = executor.execute_endpoint_sync(path="/v1/telemetry/gpu-clusters", method="GET")

    assert isinstance(data, dict)
    assert data["fleet_total_gpus"] == 680000
    assert data["cluster_status"] == "operational"


def test_sql_and_api_evidence_mapping(nvidia_db_path, nvidia_openapi_spec):
    """Verifies that database rows and API responses map seamlessly to EDRAK Evidence contracts."""
    db_config = DatabaseConnectionConfig(
        db_id="nvidia_test_db",
        connection_uri=f"sqlite:///{nvidia_db_path}",
    )
    api_config = RESTAPIConfig(
        api_id="nvidia_test_api",
        base_url="http://testserver",
        openapi_spec_inline=nvidia_openapi_spec,
    )

    # 1. SQL Pipeline
    sql_ev = run_dynamic_sql_pipeline(db_config, ["B200", "pricing"], "Assess B200 hardware pricing")
    assert len(sql_ev) > 0
    first_sql = sql_ev[0]
    assert isinstance(first_sql, Evidence)
    assert first_sql.metadata["channel"] == "sql_database"
    assert first_sql.is_synthetic is True

    # 2. REST API Pipeline
    api_ev = run_dynamic_api_pipeline(api_config, ["telemetry", "fleet"], "Cluster telemetry", asgi_app=mock_api_app)
    assert len(api_ev) > 0
    first_api = api_ev[0]
    assert isinstance(first_api, Evidence)
    assert first_api.metadata["channel"] == "rest_api"
    assert first_api.is_synthetic is True
