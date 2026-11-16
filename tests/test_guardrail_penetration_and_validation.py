"""
EDRAK Guardrail Penetration & Validation Test Suite
====================================================
Tests cover:
  A. SQL Guardrail — Penetration Attacks
  B. SQL Guardrail — Meaningfulness Validation
  C. SQL Guardrail — Valid Business Queries
  D. API Method Enforcement — Write Operations Blocked
  E. API Discoverer — Only GET Indexed
  F. API Endpoint Penetration (path traversal, injections)
  G. DB Introspector — Table Access Control & Execution Safety
  H. End-to-End Evidence Validation (Tri-Modal Integrity)
  I. Rich DB Data Assertions (new tables)
  J. API Rich Data Assertions (new endpoints)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from edrak.connectors.api_discoverer import DynamicAPIDiscoverer
from edrak.connectors.api_executor import SecureAPIExecutor
from edrak.connectors.db_introspector import DatabaseSchemaIntrospector
from edrak.connectors.sql_guardrail import (
    SQLGuardrailViolation,
    SQLMeaninglessQuery,
    assess_query_usefulness,
    validate_and_sanitize_sql,
)
from edrak.connectors.tri_modal_retriever import (
    api_response_to_evidence,
    run_dynamic_api_pipeline,
    run_dynamic_sql_pipeline,
    sql_result_to_evidence,
)
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.request import DatabaseConnectionConfig, RESTAPIConfig
from mock_services.nvidia.mock_nvidia_api import app as mock_api_app
from mock_services.nvidia.seed_nvidia_db import seed_database


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def nvidia_db_path(tmp_path_factory):
    """Creates a temporary seeded NVIDIA SQLite database."""
    tmp_dir = tmp_path_factory.mktemp("nvidia_data")
    db_file = str(tmp_dir / "nvidia_test.db")
    seed_database(db_file)
    return db_file


@pytest.fixture(scope="module")
def db_introspector(nvidia_db_path):
    uri = f"sqlite:///{nvidia_db_path}"
    return DatabaseSchemaIntrospector(connection_uri=uri)


@pytest.fixture(scope="module")
def nvidia_openapi_spec():
    """Returns the mock NVIDIA OpenAPI specification dict from the running app."""
    return mock_api_app.openapi()


@pytest.fixture(scope="module")
def api_client():
    """TestClient wrapping the mock API app."""
    return TestClient(mock_api_app)


@pytest.fixture(scope="module")
def discoverer(nvidia_openapi_spec):
    return DynamicAPIDiscoverer(spec=nvidia_openapi_spec, base_url="http://testserver")


@pytest.fixture(scope="module")
def executor():
    return SecureAPIExecutor(base_url="http://testserver", app=mock_api_app)


# ─────────────────────────────────────────────────────────────────────────────
# A. SQL Guardrail — Penetration Attacks
# ─────────────────────────────────────────────────────────────────────────────


class TestSQLGuardrailPenetrationAttacks:
    """Tries to break the guardrail with known SQL injection patterns."""

    def test_block_delete_all_rows(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("DELETE FROM gpu_products WHERE 1=1")

    def test_block_drop_table(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("DROP TABLE enterprise_deals")

    def test_block_truncate(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("TRUNCATE TABLE software_arr")

    def test_block_insert(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("INSERT INTO gpu_products VALUES (99, 'FAKE', 'A', 'B', 0, 0, 'Q1')")

    def test_block_update(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("UPDATE gpu_products SET target_asp_usd = 1 WHERE product_id = 1")

    def test_block_create_table(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("CREATE TABLE exfil_data AS SELECT * FROM enterprise_deals")

    def test_block_alter_table(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("ALTER TABLE gpu_products ADD COLUMN backdoor TEXT")

    def test_block_query_stacking_select_then_drop(self):
        """Classic stacked query injection: SELECT; DROP TABLE."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT * FROM gpu_products; DROP TABLE gpu_products")

    def test_block_query_stacking_select_then_insert(self):
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql(
                "SELECT * FROM gpu_products; INSERT INTO gpu_products VALUES (99,'x','x','x',0,0,'Q1',0,0)"
            )

    def test_block_union_based_drop(self):
        """UNION injection attempting to stack a DROP after SELECT."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql(
                "SELECT product_name FROM gpu_products; DROP TABLE gpu_products"
            )

    def test_block_time_based_sleep(self):
        """Time-based blind injection attempt using SLEEP."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT * FROM gpu_products WHERE product_id = 1 AND SLEEP(5)")

    def test_block_xp_cmdshell(self):
        """MSSQL shell execution injection."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT * FROM gpu_products; EXEC xp_cmdshell('whoami')")

    def test_block_version_disclosure(self):
        """DB version fingerprinting via VERSION() function."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT VERSION()")

    def test_block_current_user_disclosure(self):
        """CURRENT_USER() disclosure attempt."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT CURRENT_USER()")

    def test_block_or_1_equals_1_tautology(self):
        """Classic OR 1=1 authentication bypass."""
        with pytest.raises(SQLGuardrailViolation, match="tautology"):
            validate_and_sanitize_sql(
                "SELECT * FROM enterprise_deals WHERE customer_name = 'CORP' OR 1=1"
            )

    def test_block_or_quoted_tautology(self):
        """OR '1'='1' variant."""
        with pytest.raises(SQLGuardrailViolation, match="tautology"):
            validate_and_sanitize_sql(
                "SELECT * FROM enterprise_deals WHERE customer_name = '' OR '1'='1'"
            )

    def test_block_merge_upsert(self):
        """MERGE INTO (UPSERT) operation attempt."""
        # sqlglot may parse this differently per dialect; we just check it doesn't pass clean
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql(
                "MERGE INTO gpu_products USING (SELECT 99 AS product_id) src "
                "ON gpu_products.product_id = src.product_id "
                "WHEN NOT MATCHED THEN INSERT VALUES (99,'hack','x','x',0,0,'Q1',0,0)"
            )

    def test_block_load_file_function(self):
        """MySQL LOAD_FILE exfiltration attempt."""
        with pytest.raises(SQLGuardrailViolation):
            validate_and_sanitize_sql("SELECT LOAD_FILE('/etc/passwd')")

    def test_limit_enforced_when_too_large(self):
        """Ensures LIMIT 99999 is capped to default."""
        sql = validate_and_sanitize_sql("SELECT * FROM gpu_products LIMIT 99999", default_limit=50)
        assert re.search(r"LIMIT\s+50", sql, re.IGNORECASE), f"Expected LIMIT 50, got: {sql}"

    def test_limit_injected_when_absent(self):
        """Ensures LIMIT is added when absent."""
        sql = validate_and_sanitize_sql("SELECT * FROM gpu_products", default_limit=25)
        assert re.search(r"LIMIT\s+25", sql, re.IGNORECASE), f"Expected LIMIT 25, got: {sql}"

    def test_semicolon_stripped_and_passes(self):
        """Trailing semicolons should be stripped, not treated as stacking."""
        sql = validate_and_sanitize_sql("SELECT product_name FROM gpu_products;")
        assert "product_name" in sql.lower()


# ─────────────────────────────────────────────────────────────────────────────
# B. SQL Guardrail — Meaningfulness Validation
# ─────────────────────────────────────────────────────────────────────────────


class TestSQLMeaningfulnessValidation:
    """Validates that the AI-usefulness layer rejects queries that return no business data."""

    def test_reject_select_constant_only(self):
        """SELECT 1 has no table context; useless for business intelligence."""
        useful, reason = assess_query_usefulness("SELECT 1")
        assert not useful
        assert "FROM clause" in reason or "Meaningless" in reason

    def test_reject_select_null_only(self):
        useful, reason = assess_query_usefulness("SELECT NULL")
        assert not useful

    def test_reject_limit_zero(self):
        """LIMIT 0 guarantees empty result set."""
        useful, reason = assess_query_usefulness("SELECT * FROM gpu_products LIMIT 0")
        assert not useful
        assert "LIMIT 0" in reason or "Meaningless" in reason

    def test_reject_always_false_where_1_eq_0(self):
        """WHERE 1=0 will always be empty — possible blind SQLi probe."""
        useful, reason = assess_query_usefulness("SELECT * FROM gpu_products WHERE 1=0")
        assert not useful

    def test_reject_null_predicate_where(self):
        """WHERE NULL=NULL is always false."""
        useful, reason = assess_query_usefulness("SELECT * FROM gpu_products WHERE NULL=NULL")
        assert not useful

    def test_accept_valid_business_query(self):
        """SELECT with real columns and table is useful."""
        useful, reason = assess_query_usefulness(
            "SELECT product_name, target_asp_usd FROM gpu_products WHERE architecture='Blackwell'"
        )
        assert useful, f"Expected useful query but got: {reason}"

    def test_accept_aggregate_query(self):
        useful, reason = assess_query_usefulness(
            "SELECT segment, SUM(total_deal_value_usd) AS total FROM enterprise_deals GROUP BY segment"
        )
        assert useful, f"Expected useful query but got: {reason}"

    def test_accept_join_query(self):
        useful, reason = assess_query_usefulness(
            "SELECT d.customer_name, g.target_gross_margin_pct "
            "FROM enterprise_deals d JOIN gpu_products g ON d.product_name = g.product_name "
            "WHERE d.region = 'EMEA'"
        )
        assert useful, f"Expected useful JOIN query but got: {reason}"

    def test_accept_supply_chain_query(self):
        useful, reason = assess_query_usefulness(
            "SELECT product_name, lead_time_weeks, bottleneck_severity "
            "FROM supply_chain_constraints WHERE bottleneck_severity = 'Critical'"
        )
        assert useful, f"Expected useful query but got: {reason}"

    def test_meaningfulness_check_can_be_disabled(self):
        """When check_meaningfulness=False, SELECT 1 passes security (no table required)."""
        # This should not raise
        sql = validate_and_sanitize_sql("SELECT * FROM gpu_products LIMIT 0", check_meaningfulness=False)
        assert "LIMIT" in sql


# ─────────────────────────────────────────────────────────────────────────────
# C. SQL Guardrail — Valid Business Queries
# ─────────────────────────────────────────────────────────────────────────────


class TestSQLValidBusinessQueries:
    """Ensures legitimately useful queries are NOT rejected by the guardrail."""

    def test_select_star_with_where(self):
        sql = validate_and_sanitize_sql("SELECT * FROM gpu_products WHERE architecture = 'Blackwell'")
        assert "gpu_products" in sql

    def test_select_with_aggregate(self):
        sql = validate_and_sanitize_sql(
            "SELECT segment, COUNT(*) as deal_count, SUM(total_deal_value_usd) as total "
            "FROM enterprise_deals GROUP BY segment ORDER BY total DESC"
        )
        assert "enterprise_deals" in sql

    def test_select_with_subquery(self):
        sql = validate_and_sanitize_sql(
            "SELECT product_name, target_asp_usd FROM gpu_products "
            "WHERE target_asp_usd > (SELECT AVG(target_asp_usd) FROM gpu_products)"
        )
        assert "gpu_products" in sql

    def test_select_from_competitor_benchmarks(self):
        sql = validate_and_sanitize_sql(
            "SELECT benchmark_name, nvidia_product, competitor_product, nvidia_advantage_pct "
            "FROM competitor_benchmarks ORDER BY nvidia_advantage_pct DESC LIMIT 5"
        )
        assert "competitor_benchmarks" in sql

    def test_select_from_export_control_flags(self):
        sql = validate_and_sanitize_sql(
            "SELECT country_or_entity, control_status, applies_to_products "
            "FROM export_control_flags WHERE control_status = 'Prohibited'"
        )
        assert "export_control_flags" in sql

    def test_select_from_sales_pipeline(self):
        sql = validate_and_sanitize_sql(
            "SELECT account_name, estimated_value_usd, close_probability_pct "
            "FROM sales_pipeline_forecast WHERE expected_close_quarter = 'Q2 FY2026'"
        )
        assert "sales_pipeline_forecast" in sql


# ─────────────────────────────────────────────────────────────────────────────
# D. API Method Enforcement — Write Operations Blocked
# ─────────────────────────────────────────────────────────────────────────────


class TestAPIMethodEnforcement:
    """Verifies the SecureAPIExecutor blocks every non-GET HTTP method."""

    def test_block_post_deal_create(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/deals/create", method="POST")

    def test_block_post_emergency_order(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/supply-chain/emergency-order", method="POST")

    def test_block_delete_opportunity(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/pipeline/OPP-2026-Q2-001", method="DELETE")

    def test_block_patch_pricing_override(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/pricing/override", method="PATCH")

    def test_block_put(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/products", method="PUT")

    def test_head_method_is_allowed(self, executor):
        """HEAD is a safe read-only method and should be permitted by the guardrail."""
        url, headers = executor._prepare_request("/v1/products", "HEAD")
        assert "/v1/products" in url

    def test_block_options_method(self, executor):
        """OPTIONS reveals server capabilities — treat as a non-allowed method."""
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/products", method="OPTIONS")

    def test_block_case_insensitive_post(self, executor):
        """Method check must be case-insensitive."""
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/deals/create", method="post")

    def test_block_case_insensitive_delete(self, executor):
        with pytest.raises(PermissionError, match="prohibited"):
            executor.execute_endpoint_sync(path="/v1/pipeline/OPP-001", method="Delete")


# ─────────────────────────────────────────────────────────────────────────────
# E. API Discoverer — Only GET Operations Indexed
# ─────────────────────────────────────────────────────────────────────────────


class TestAPIDiscovererGetOnlyIndexing:
    """Verifies the discoverer never indexes POST/PATCH/DELETE from the OpenAPI spec."""

    def test_all_indexed_methods_are_get_or_head(self, discoverer):
        for ep in discoverer.endpoints:
            assert ep["method"].upper() in ("GET", "HEAD"), (
                f"Non-GET/HEAD method '{ep['method']}' indexed: {ep['path']}"
            )

    def test_write_paths_not_indexed(self, discoverer):
        write_paths = {"/v1/deals/create", "/v1/supply-chain/emergency-order",
                       "/v1/pricing/override"}
        indexed_paths = {ep["path"] for ep in discoverer.endpoints}
        overlap = write_paths & indexed_paths
        assert not overlap, (
            f"Write endpoint(s) leaked into discoverer index: {overlap}"
        )

    def test_delete_pipeline_not_indexed(self, discoverer):
        indexed_paths = {ep["path"] for ep in discoverer.endpoints}
        # /v1/pipeline/{opportunity_id} is a DELETE-only route
        for p in indexed_paths:
            if "pipeline" in p:
                # it must not have a DELETE method associated
                for ep in discoverer.endpoints:
                    if ep["path"] == p:
                        assert ep["method"].upper() != "DELETE"

    def test_read_endpoints_are_indexed(self, discoverer):
        expected = {
            "/v1/telemetry/gpu-clusters",
            "/v1/pricing/discount-matrix",
            "/v1/supply-chain/lead-times",
            "/v1/products",
            "/v1/competitive/benchmark-summary",
            "/v1/finance/arr-summary",
        }
        indexed_paths = {ep["path"] for ep in discoverer.endpoints}
        for path in expected:
            assert path in indexed_paths, f"Expected read endpoint not indexed: {path}"

    def test_endpoint_count_matches_get_routes(self, discoverer):
        # We have 8 GET routes in the expanded API; discoverer count must be >= 8
        assert len(discoverer.endpoints) >= 8

    def test_semantic_match_telemetry(self, discoverer):
        matches = discoverer.match_endpoints_for_task("fleet utilization cluster gpu telemetry", top_k=1)
        assert matches, "No match returned for telemetry query"
        assert matches[0]["path"] == "/v1/telemetry/gpu-clusters"

    def test_semantic_match_competitive(self, discoverer):
        matches = discoverer.match_endpoints_for_task("benchmark comparison AMD competitor", top_k=1)
        assert matches, "No match returned for competitive query"
        assert "benchmark" in matches[0]["path"]

    def test_semantic_match_arr(self, discoverer):
        matches = discoverer.match_endpoints_for_task("software subscription ARR revenue", top_k=1)
        assert matches, "No match for ARR query"
        assert "arr" in matches[0]["path"] or "finance" in matches[0]["path"]


# ─────────────────────────────────────────────────────────────────────────────
# F. API Endpoint Penetration (path traversal, injections in path params)
# ─────────────────────────────────────────────────────────────────────────────


class TestAPIEndpointPenetration:
    """Tries path-traversal and injection attacks via the API executor path argument."""

    def test_path_traversal_attempt_blocked_by_404(self, api_client):
        """../../etc/passwd style traversal should 404, not succeed."""
        resp = api_client.get("/v1/products/../../../etc/passwd")
        assert resp.status_code in (404, 422), f"Unexpected status: {resp.status_code}"

    def test_sql_injection_in_path_param_not_executed(self, api_client):
        """SQLi in path param for /v1/export-control/{country} must not hit DB."""
        malicious = "' OR 1=1 --"
        resp = api_client.get(f"/v1/export-control/{malicious}")
        # Should be 404 (unknown country) or 422 (validation), NOT 200 with DB results
        assert resp.status_code in (404, 422), (
            f"Injection in path param returned unexpected {resp.status_code}: {resp.text}"
        )

    def test_script_injection_in_path_param(self, api_client):
        resp = api_client.get("/v1/export-control/<script>alert(1)</script>")
        assert resp.status_code in (404, 422)

    def test_null_byte_in_path(self, api_client):
        resp = api_client.get("/v1/products/%00")
        assert resp.status_code in (404, 422, 400)

    def test_excessively_long_path_rejected(self, api_client):
        long_param = "A" * 2048
        resp = api_client.get(f"/v1/export-control/{long_param}")
        assert resp.status_code in (404, 422, 400, 414)

    def test_write_endpoint_post_not_routed_as_get(self, api_client):
        """Sending a GET to a POST-only endpoint must 405, not succeed."""
        resp = api_client.get("/v1/deals/create")
        assert resp.status_code == 405, (
            f"GET to POST-only route should return 405, got {resp.status_code}"
        )

    def test_agent_cannot_reach_write_endpoint_via_executor(self, executor):
        """The executor must block POST before the request even reaches the server."""
        with pytest.raises(PermissionError):
            executor.execute_endpoint_sync(path="/v1/deals/create", method="POST",
                                           params={"customer_name": "evil", "product_name": "B200",
                                                   "units_ordered": 1, "discount_pct": 99.0})


# ─────────────────────────────────────────────────────────────────────────────
# G. DB Introspector — Table Access Control & Execution Safety
# ─────────────────────────────────────────────────────────────────────────────


class TestDBIntrospectorSafety:

    def test_allowed_tables_filter_blocks_unlisted_table(self, nvidia_db_path):
        uri = f"sqlite:///{nvidia_db_path}"
        restricted = DatabaseSchemaIntrospector(
            connection_uri=uri,
            allowed_tables=["gpu_products"],
        )
        catalog = restricted.list_table_catalog()
        names = [t["table_name"] for t in catalog]
        assert "gpu_products" in names
        assert "enterprise_deals" not in names, (
            "enterprise_deals should be blocked by allowed_tables filter"
        )

    def test_execute_query_requires_select_statement(self, db_introspector):
        with pytest.raises(Exception):
            db_introspector.execute_query("DELETE FROM gpu_products WHERE 1=1")

    def test_execute_query_enforces_limit(self, db_introspector):
        records = db_introspector.execute_query("SELECT * FROM gpu_products", default_limit=3)
        assert len(records) <= 3

    def test_catalog_lists_all_seven_tables(self, db_introspector):
        catalog = db_introspector.list_table_catalog()
        names = {t["table_name"] for t in catalog}
        expected = {
            "gpu_products", "enterprise_deals", "software_arr",
            "supply_chain_constraints", "competitor_benchmarks",
            "sales_pipeline_forecast", "export_control_flags",
        }
        assert expected == names, f"Missing tables: {expected - names}"

    def test_sample_rows_available_in_tier2(self, db_introspector):
        details = db_introspector.introspect_selected_tables(["competitor_benchmarks"])
        assert "competitor_benchmarks" in details
        assert len(details["competitor_benchmarks"]["sample_records"]) > 0

    def test_execute_real_aggregate_query(self, db_introspector):
        records = db_introspector.execute_query(
            "SELECT region, COUNT(*) as deal_count, SUM(total_deal_value_usd) as total_usd "
            "FROM enterprise_deals GROUP BY region ORDER BY total_usd DESC"
        )
        assert len(records) > 0
        assert "region" in records[0]
        assert "total_usd" in records[0]

    def test_execute_join_query_safe(self, db_introspector):
        records = db_introspector.execute_query(
            "SELECT e.customer_name, g.architecture, e.total_deal_value_usd "
            "FROM enterprise_deals e "
            "JOIN gpu_products g ON e.product_name = g.product_name "
            "WHERE e.region = 'North America' LIMIT 5"
        )
        assert len(records) > 0
        assert "customer_name" in records[0]


# ─────────────────────────────────────────────────────────────────────────────
# H. End-to-End Evidence Validation (Tri-Modal Integrity)
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEndEvidenceValidity:
    """Ensures all three modalities produce valid EDRAK Evidence objects."""

    def test_sql_pipeline_returns_evidence(self, nvidia_db_path, nvidia_openapi_spec):
        db_config = DatabaseConnectionConfig(
            db_id="nvidia_test_db",
            connection_uri=f"sqlite:///{nvidia_db_path}",
        )
        ev_list = run_dynamic_sql_pipeline(
            db_config, ["GPU pricing ARR software"], "Assess NVIDIA software ARR and GPU margins"
        )
        assert len(ev_list) > 0
        for ev in ev_list:
            assert isinstance(ev, Evidence)
            assert ev.extracted_fact
            assert ev.metadata.get("channel") == "sql_database"
            assert ev.metadata.get("row_count", 0) > 0

    def test_api_pipeline_returns_evidence(self, nvidia_openapi_spec):
        api_config = RESTAPIConfig(
            api_id="nvidia_test_api",
            base_url="http://testserver",
            openapi_spec_inline=nvidia_openapi_spec,
        )
        ev_list = run_dynamic_api_pipeline(
            api_config, ["cluster utilization telemetry fleet"], "GPU cluster health",
            asgi_app=mock_api_app,
        )
        assert len(ev_list) > 0
        for ev in ev_list:
            assert isinstance(ev, Evidence)
            assert ev.metadata.get("channel") == "rest_api"
            assert ev.excerpt

    def test_api_pipeline_never_calls_write_endpoints(self, nvidia_openapi_spec):
        """Run pipeline and confirm no evidence came from a write endpoint."""
        api_config = RESTAPIConfig(
            api_id="nvidia_test_api",
            base_url="http://testserver",
            openapi_spec_inline=nvidia_openapi_spec,
        )
        ev_list = run_dynamic_api_pipeline(
            api_config, ["pricing discount supply chain"], "Full operational status",
            asgi_app=mock_api_app,
        )
        write_paths = {"/v1/deals/create", "/v1/supply-chain/emergency-order",
                       "/v1/pricing/override"}
        for ev in ev_list:
            ep = ev.metadata.get("endpoint", "")
            assert ep not in write_paths, (
                f"Agent called write endpoint: {ep}"
            )

    def test_sql_evidence_source_type(self, nvidia_db_path):
        db_config = DatabaseConnectionConfig(
            db_id="nvidia_test_db",
            connection_uri=f"sqlite:///{nvidia_db_path}",
        )
        ev_list = run_dynamic_sql_pipeline(
            db_config, ["supply chain constraints lead time"], "Supply chain risk assessment"
        )
        assert len(ev_list) > 0
        for ev in ev_list:
            assert ev.source_type in (SourceType.SYNTHETIC_INTERNAL, SourceType.INTERNAL_DOCUMENT)

    def test_evidence_contract_fields_complete(self, nvidia_db_path):
        db_config = DatabaseConnectionConfig(
            db_id="nvidia_test_db",
            connection_uri=f"sqlite:///{nvidia_db_path}",
        )
        ev_list = run_dynamic_sql_pipeline(
            db_config, ["competitor benchmark AMD"], "Competitive benchmark analysis"
        )
        assert len(ev_list) > 0
        ev = ev_list[0]
        assert ev.evidence_id
        assert ev.source_title
        assert ev.source_url
        assert ev.extracted_fact
        assert ev.retrieved_at


# ─────────────────────────────────────────────────────────────────────────────
# I. Rich DB Data Assertions (new tables)
# ─────────────────────────────────────────────────────────────────────────────


class TestRichDatabaseData:
    """Validates the expanded seed data is correct and queryable."""

    def test_gpu_products_count(self, db_introspector):
        recs = db_introspector.execute_query("SELECT COUNT(*) AS cnt FROM gpu_products")
        assert recs[0]["cnt"] >= 7

    def test_enterprise_deals_count(self, db_introspector):
        recs = db_introspector.execute_query("SELECT COUNT(*) AS cnt FROM enterprise_deals")
        assert recs[0]["cnt"] >= 14

    def test_software_arr_count(self, db_introspector):
        recs = db_introspector.execute_query("SELECT COUNT(*) AS cnt FROM software_arr")
        assert recs[0]["cnt"] >= 5

    def test_supply_chain_constraints_exist(self, db_introspector):
        recs = db_introspector.execute_query(
            "SELECT product_name, bottleneck_severity FROM supply_chain_constraints "
            "WHERE bottleneck_severity = 'Critical'"
        )
        assert len(recs) >= 2

    def test_competitor_benchmarks_nvidia_advantage(self, db_introspector):
        recs = db_introspector.execute_query(
            "SELECT benchmark_name, nvidia_advantage_pct FROM competitor_benchmarks "
            "ORDER BY nvidia_advantage_pct DESC LIMIT 3"
        )
        assert len(recs) == 3
        # Blackwell B200 should have the highest advantage
        assert recs[0]["nvidia_advantage_pct"] > 80.0

    def test_sales_pipeline_weighted_value(self, db_introspector):
        """Weighted pipeline value = estimated_value * probability."""
        recs = db_introspector.execute_query(
            "SELECT SUM(estimated_value_usd * close_probability_pct / 100.0) AS weighted_pipeline "
            "FROM sales_pipeline_forecast"
        )
        assert recs[0]["weighted_pipeline"] > 100_000_000, "Weighted pipeline should exceed $100M"

    def test_export_control_prohibited_countries(self, db_introspector):
        recs = db_introspector.execute_query(
            "SELECT country_or_entity FROM export_control_flags WHERE control_status = 'Prohibited'"
        )
        prohibited = {r["country_or_entity"] for r in recs}
        assert "Russia" in prohibited
        assert "Iran" in prohibited

    def test_total_arr_calculation(self, db_introspector):
        recs = db_introspector.execute_query(
            "SELECT SUM(total_arr_usd) AS total FROM software_arr WHERE fiscal_year = 2026"
        )
        # Sum of 5 subscriptions should exceed $3B
        assert recs[0]["total"] > 3_000_000_000


# ─────────────────────────────────────────────────────────────────────────────
# J. API Rich Data Assertions (new endpoints)
# ─────────────────────────────────────────────────────────────────────────────


class TestRichAPIData:
    """Validates the expanded API endpoints return correct data."""

    def test_get_products_returns_all(self, api_client):
        resp = api_client.get("/v1/products")
        assert resp.status_code == 200
        products = resp.json()
        assert len(products) >= 7
        names = [p["product_name"] for p in products]
        assert "B200 GPU" in names
        assert "GB300 GPU (preview)" in names

    def test_get_product_margin_b200(self, api_client):
        resp = api_client.get("/v1/products/B200 GPU/margin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_gross_margin_pct"] == 73.5
        assert data["margin_floor_pct"] == 70.0
        assert data["headroom_pct"] == pytest.approx(3.5, abs=0.1)

    def test_get_product_margin_not_found(self, api_client):
        resp = api_client.get("/v1/products/FakeProduct9999/margin")
        assert resp.status_code == 404

    def test_get_competitive_benchmarks(self, api_client):
        resp = api_client.get("/v1/competitive/benchmark-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["average_nvidia_advantage_pct"] > 50.0
        assert len(data["benchmarks"]) >= 4

    def test_get_arr_summary(self, api_client):
        resp = api_client.get("/v1/finance/arr-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fiscal_year"] == 2026
        assert data["total_software_arr_usd"] > 3_000_000_000

    def test_get_export_control_china(self, api_client):
        resp = api_client.get("/v1/export-control/china")
        assert resp.status_code == 200
        data = resp.json()
        assert data["control_status"] == "Restricted"
        assert "B200" in data["applies_to_products"]

    def test_get_export_control_russia_prohibited(self, api_client):
        resp = api_client.get("/v1/export-control/russia")
        assert resp.status_code == 200
        data = resp.json()
        assert data["control_status"] == "Prohibited"

    def test_get_export_control_unknown_returns_404(self, api_client):
        resp = api_client.get("/v1/export-control/atlantis")
        assert resp.status_code == 404

    def test_write_endpoint_post_deal_returns_405_on_get(self, api_client):
        """GET request to a POST-only route must be rejected."""
        resp = api_client.get("/v1/deals/create")
        assert resp.status_code == 405

    def test_telemetry_timestamp_is_iso(self, api_client):
        resp = api_client.get("/v1/telemetry/gpu-clusters")
        assert resp.status_code == 200
        ts = resp.json().get("telemetry_timestamp", "")
        # Should parse as ISO 8601
        from datetime import datetime
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Telemetry timestamp is not valid ISO 8601: {ts}")

    def test_discount_matrix_has_five_tiers(self, api_client):
        resp = api_client.get("/v1/pricing/discount-matrix")
        assert resp.status_code == 200
        rules = resp.json()["rules"]
        assert len(rules) == 5

    def test_supply_chain_includes_gb300(self, api_client):
        resp = api_client.get("/v1/supply-chain/lead-times")
        assert resp.status_code == 200
        lead_times = resp.json()["lead_times_weeks"]
        assert "GB300 GPU (preview)" in lead_times
        assert lead_times["GB300 GPU (preview)"] == 28

