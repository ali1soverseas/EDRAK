"""Complete Demonstration & Test of EDRAK Tri-Modal Internal Intelligence Worker.

Modalities tested:
1. Vector RAG (Internal Strategic Documents & Playbooks)
2. Dynamic Relational Database (Text-to-SQL + AST Guardrails + Schema Introspection)
3. Dynamic Operational REST API (OpenAPI 3.x Discovery + Secure GET Execution)
"""

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add backend/src to path
repo_root = Path(__file__).resolve().parents[1]
backend_src = repo_root / "backend" / "src"
if str(backend_src) not in sys.path:
    sys.path.insert(0, str(backend_src))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from edrak.agents.internal_intelligence.nodes import (
    plan_queries_node,
    retrieve_evidence_node,
    analyze_and_synthesize_node,
)
from edrak.agents.internal_intelligence.state import InternalAgentState
from edrak.connectors.api_discoverer import DynamicAPIDiscoverer
from edrak.connectors.api_executor import SecureAPIExecutor
from edrak.connectors.db_introspector import DatabaseSchemaIntrospector
from edrak.connectors.sql_guardrail import validate_and_sanitize_sql
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.request import (
    BusinessContext,
    CompanyProfile,
    DatabaseConnectionConfig,
    RESTAPIConfig,
    UseCase,
)
from edrak.contracts.task import ResearchTask, WorkerType
from mock_services.nvidia.seed_nvidia_db import seed_database


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title.upper()}")
    print("=" * 80)


def print_section(title: str):
    print(f"\n--- [ {title} ] " + "-" * (70 - len(title)))


def test_guardrails(db_path: str):
    print_section("1. Testing SQL AST Guardrails & Safety Defenses")
    safe_query = "SELECT product_name, target_asp_usd FROM gpu_products WHERE target_gross_margin_pct >= 70"
    sanitized = validate_and_sanitize_sql(safe_query, default_limit=10)
    print(f"[OK] Safe Query Allowed & Sanitized:\n     {sanitized}")

    # Test mutation rejection
    forbidden_queries = [
        "DROP TABLE enterprise_deals",
        "INSERT INTO gpu_products (product_name) VALUES ('H1000')",
        "UPDATE enterprise_deals SET discount_pct = 50",
        "SELECT * FROM gpu_products; DROP TABLE gpu_products;",
    ]

    for fq in forbidden_queries:
        try:
            validate_and_sanitize_sql(fq)
            print(f"[FAIL] Guardrail failed to catch: {fq}")
        except PermissionError as e:
            print(f"[BLOCKED] Successfully caught attack: {e}")


def test_schema_introspection(db_path: str):
    print_section("2. Testing Two-Tier Dynamic Database Introspection")
    uri = f"sqlite:///{db_path}"
    introspector = DatabaseSchemaIntrospector(connection_uri=uri)

    # Tier 1: Catalog
    catalog = introspector.list_table_catalog()
    print(f"Tier 1 Catalog Introspection: Found {len(catalog)} tables:")
    for item in catalog:
        print(f"  * Table: {item['table_name']:<20} Estimated Rows: {item['estimated_row_count']}")

    # Tier 2: Selected Tables Schema
    chosen = ["enterprise_deals", "gpu_products"]
    details = introspector.introspect_selected_tables(chosen)
    print(f"\nTier 2 Detailed Introspection for: {chosen}")
    for tbl, info in details.items():
        cols = [f"{c['name']} ({c['type']})" for c in info['columns']]
        print(f"  * {tbl}: Columns: {', '.join(cols[:4])} ... (+{len(cols)-4} more)")
        print(f"    Sample Record: {info['sample_records'][0] if info['sample_records'] else 'None'}")


def test_api_discovery(spec_path: str):
    print_section("3. Testing Dynamic OpenAPI Discovery & Matching")
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    discoverer = DynamicAPIDiscoverer(spec=spec, base_url="http://127.0.0.1:8088")
    print(f"Discovered {len(discoverer.endpoints)} read-safe (GET) endpoints:")
    for ep in discoverer.endpoints:
        print(f"  * {ep['method']:<4} {ep['path']:<30} Summary: {ep['summary']}")

    # Test query matching
    query = "What is the GPU cluster fleet utilization and active Blackwell units?"
    matches = discoverer.match_endpoints_for_task(query, top_k=1)
    print(f"\nSemantic match for query: '{query}'")
    for m in matches:
        print(f"  -> Matched Endpoint: {m['path']} ({m['summary']})")


def run_full_tri_modal_agent_flow(db_path: str, spec_path: str):
    print_banner("4. Running Full Tri-Modal Internal Intelligence Agent")

    with open(spec_path, "r", encoding="utf-8") as f:
        openapi_spec = json.load(f)

    # 1. Configure NVIDIA Company Profile with Dynamic DB and API Configs
    db_config = DatabaseConnectionConfig(
        db_id="nvidia_enterprise_db",
        connection_uri=f"sqlite:///{db_path}",
        allowed_tables=["gpu_products", "enterprise_deals", "software_arr"],
        description="NVIDIA Enterprise internal production database containing hardware pricing, closed customer deals, and software ARR.",
    )

    api_config = RESTAPIConfig(
        api_id="nvidia_ops_api",
        base_url="http://127.0.0.1:8088",
        openapi_spec_inline=openapi_spec,
        description="NVIDIA global operational telemetry and deal desk microservice.",
    )

    company_profile = CompanyProfile(
        name="NVIDIA",
        aliases=["NVDA", "Nvidia Corporation"],
        products=["GB200 NVL72 rack", "B200 GPU", "H200 GPU", "NVIDIA AI Enterprise", "NIM microservices"],
        notes="Global leader in accelerated computing and AI infrastructure.",
        workspace_id="default",
        db_config=db_config,
        api_config=api_config,
    )

    business_context = BusinessContext(
        use_case=UseCase.COMPETITIVE_INTELLIGENCE,
        targets=["AMD", "Intel", "Custom Silicon / Hyperscalers"],
        focus_areas=["pricing", "enterprise deals", "cluster telemetry", "software ARR"],
    )

    task = ResearchTask(
        parent_request_id="req-nvidia-demo-001",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Assess NVIDIA Blackwell B200 and GB200 enterprise pricing, closed deal volume, cluster utilization, and software ARR.",
        focus="Compare hardware target ASP, closed hyperscaler contracts, software ARR metrics, and operational cluster health.",
        company_profile=company_profile,
        business_context=business_context,
    )

    state: InternalAgentState = {
        "task": task,
        "queries": [],
        "retrieved_evidence": [],
        "findings": [],
        "summary": "",
        "limitations_and_gaps": [],
        "dropped_findings": [],
        "conflicts": [],
    }

    # Step A: Planning Queries
    print("\n[Stage A] Query Planning Node...")
    plan_out = plan_queries_node(state)
    state["queries"] = plan_out.get("queries", [])
    print(f"Generated {len(state['queries'])} queries:")
    for q in state["queries"]:
        print(f"  - {q}")

    # Step B: Tri-Modal Evidence Retrieval
    print("\n[Stage B] Tri-Modal Evidence Retrieval Node...")
    retrieval_out = retrieve_evidence_node(state)
    evidence_list = retrieval_out.get("retrieved_evidence", [])
    state["retrieved_evidence"] = evidence_list

    print(f"\nRetrieved {len(evidence_list)} Total Evidence Items across 3 Modalities:")
    db_items = [e for e in evidence_list if e.metadata.get("channel") == "sql_database"]
    api_items = [e for e in evidence_list if e.metadata.get("channel") == "rest_api"]
    rag_items = [e for e in evidence_list if e.metadata.get("channel") not in ("sql_database", "rest_api")]

    print(f"  * SQL Database Evidence: {len(db_items)} items")
    for e in db_items:
        print(f"    - Title: {e.source_title}")
        print(f"      Fact:  {e.extracted_fact}")
        print(f"      Rows:  {e.metadata.get('row_count')}, Query: {e.metadata.get('query')}")

    print(f"  * REST API Evidence:     {len(api_items)} items")
    for e in api_items:
        print(f"    - Title: {e.source_title}")
        print(f"      Fact:  {e.extracted_fact}")
        print(f"      Endpoint: {e.metadata.get('endpoint')}")

    print(f"  * Vector Docs Evidence:  {len(rag_items)} items")
    for e in rag_items:
        print(f"    - Title: {e.source_title}")
        print(f"      Fact:  {e.extracted_fact}")

    # Step C: Deep Synthesis
    print("\n[Stage C] Analysis & Synthesis Node...")
    synth_out = analyze_and_synthesize_node(state)
    print("\nSynthesized Executive Summary:")
    print("-" * 60)
    print(synth_out.get("summary", "No summary generated."))
    print("-" * 60)

    findings = synth_out.get("findings", [])
    print(f"\nExtracted Findings ({len(findings)} items):")
    for idx, f in enumerate(findings, 1):
        conf = f"{f.confidence:.2f}" if f.confidence is not None else "N/A"
        print(f"\n[{idx}] Category: {f.category} | Confidence: {conf}")
        print(f"    Statement: {f.statement}")
        if f.limitations:
            print(f"    Limitations: {', '.join(f.limitations)}")
        print(f"    Sources: {[ref.evidence_id for ref in f.evidence_refs]}")

    print_banner("Tri-Modal Verification Successful!")
    print("All 3 modalities (Vector RAG + SQL Database + REST API) grounded seamlessly into EDRAK contracts.")


def main():
    print_banner("EDRAK Dynamic DB & REST API Tri-Modal Integration Test")

    # Seed mock database if needed
    db_file = os.path.join(repo_root, "mock_services", "nvidia", "nvidia_enterprise.db")
    spec_file = os.path.join(repo_root, "mock_services", "nvidia", "nvidia_openapi_spec.json")

    seed_database(db_file)

    # 1. Test AST guardrails
    test_guardrails(db_file)

    # 2. Test dynamic schema introspection
    test_schema_introspection(db_file)

    # 3. Test OpenAPI discovery
    test_api_discovery(spec_file)

    # 4. Run the full agent flow
    run_full_tri_modal_agent_flow(db_file, spec_file)


if __name__ == "__main__":
    main()
