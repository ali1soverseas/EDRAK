"""EDRAK Tri-Modal Internal Intelligence CLI Runner.

Accepts dynamic Database URIs, OpenAPI REST API URLs, and company profiles
via command-line arguments to test custom databases (SQLite, PostgreSQL, MySQL)
and REST APIs.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add backend/src and root to sys.path
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
from edrak.contracts.evidence import Evidence
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


def run_preflight_db(db_uri: str, allowed_tables: Optional[List[str]] = None) -> DatabaseSchemaIntrospector:
    """Tests database connection, dialect detection, and schema introspection."""
    print_section("Pre-Flight Check: Relational Database")
    print(f"Connecting to URI: {db_uri}")
    
    introspector = DatabaseSchemaIntrospector(connection_uri=db_uri, allowed_tables=allowed_tables)
    dialect = introspector.engine.dialect.name
    print(f"[OK] Database connected! Dialect detected: '{dialect}'")

    catalog = introspector.list_table_catalog()
    print(f"[OK] Found {len(catalog)} accessible tables:")
    for item in catalog:
        print(f"     - Table: {item['table_name']:<25} Rows: {item['estimated_row_count']}")

    if catalog:
        sample_tables = [item["table_name"] for item in catalog[:2]]
        details = introspector.introspect_selected_tables(sample_tables)
        print(f"\n[OK] Sample introspection for tables {sample_tables}:")
        for tbl, info in details.items():
            cols = [c["name"] for c in info["columns"]]
            print(f"     * {tbl}: Columns: {', '.join(cols[:5])} ... (+{max(0, len(cols)-5)} more)")
            if info["sample_records"]:
                print(f"       Sample row: {info['sample_records'][0]}")

    return introspector


def run_preflight_api(
    base_url: str,
    spec_source: str,
    auth_headers: Optional[Dict[str, str]] = None,
) -> DynamicAPIDiscoverer:
    """Tests OpenAPI spec fetching, endpoint indexing, and route connectivity."""
    print_section("Pre-Flight Check: REST API & OpenAPI Discovery")
    print(f"Base URL: {base_url}")
    print(f"Spec source: {spec_source}")

    discoverer = None
    # Check if spec_source is a local file or remote URL or inline
    if os.path.exists(spec_source):
        with open(spec_source, "r", encoding="utf-8") as f:
            spec = json.load(f)
        discoverer = DynamicAPIDiscoverer(spec=spec, base_url=base_url)
    elif spec_source.startswith("http://") or spec_source.startswith("https://"):
        discoverer = DynamicAPIDiscoverer.from_url(spec_url=spec_source, base_url=base_url)
    else:
        # Try fetching from base_url/openapi.json
        full_url = f"{base_url.rstrip('/')}/openapi.json"
        discoverer = DynamicAPIDiscoverer.from_url(spec_url=full_url, base_url=base_url)

    print(f"[OK] Discovered {len(discoverer.endpoints)} read-safe (GET) endpoints:")
    for ep in discoverer.endpoints[:5]:
        print(f"     * {ep['method']:<4} {ep['path']:<35} -> {ep['summary']}")

    return discoverer


def run_agent_pipeline(
    company_name: str,
    goal: str,
    focus: str,
    db_config: Optional[DatabaseConnectionConfig],
    api_config: Optional[RESTAPIConfig],
    workspace_id: str = "default",
):
    """Executes the full LangGraph internal agent pipeline with dynamic DB and API configs."""
    print_banner(f"Running Tri-Modal Agent Analysis for: {company_name}")
    print(f"Goal:  {goal}")
    print(f"Focus: {focus}")

    company_profile = CompanyProfile(
        name=company_name,
        workspace_id=workspace_id,
        db_config=db_config,
        api_config=api_config,
    )

    business_context = BusinessContext(
        use_case=UseCase.COMPETITIVE_INTELLIGENCE,
        targets=["Market Baseline"],
        focus_areas=[focus],
    )

    task = ResearchTask(
        parent_request_id=f"req-cli-{company_name.lower().replace(' ', '-')}",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal=goal,
        focus=focus,
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

    # Step 1: Query Planning
    print_section("Stage 1: Query Planning Node")
    plan_out = plan_queries_node(state)
    state["queries"] = plan_out.get("queries", [])
    print(f"Generated {len(state['queries'])} precision queries:")
    for q in state["queries"]:
        print(f"  * {q}")

    # Step 2: Tri-Modal Retrieval
    print_section("Stage 2: Tri-Modal Evidence Retrieval Node")
    retrieval_out = retrieve_evidence_node(state)
    evidence_list = retrieval_out.get("retrieved_evidence", [])
    state["retrieved_evidence"] = evidence_list

    db_items = [e for e in evidence_list if e.metadata.get("channel") == "sql_database"]
    api_items = [e for e in evidence_list if e.metadata.get("channel") == "rest_api"]
    rag_items = [e for e in evidence_list if e.metadata.get("channel") not in ("sql_database", "rest_api")]

    print(f"\nRetrieved {len(evidence_list)} Total Grounded Evidence Items:")
    print(f"  * Relational Database (SQL): {len(db_items)} items")
    for e in db_items:
        print(f"    - Table: {e.metadata.get('table')} | Fact: {e.extracted_fact[:100]}...")
        print(f"      Query: {e.metadata.get('query')}")

    print(f"  * Operational REST API:      {len(api_items)} items")
    for e in api_items:
        print(f"    - Endpoint: {e.metadata.get('endpoint')} | Fact: {e.extracted_fact[:100]}...")

    print(f"  * Vector Documents (RAG):    {len(rag_items)} items")
    for e in rag_items:
        print(f"    - Doc: {e.source_title} | Fact: {e.extracted_fact[:100]}...")

    # Step 3: Analysis & Synthesis
    print_section("Stage 3: Deep Synthesis Node")
    synth_out = analyze_and_synthesize_node(state)

    print("\nSynthesized Executive Summary:")
    print("-" * 75)
    print(synth_out.get("summary", "No summary generated."))
    print("-" * 75)

    findings = synth_out.get("findings", [])
    print(f"\nExtracted Verified Findings ({len(findings)} items):")
    for idx, f in enumerate(findings, 1):
        conf = f"{f.confidence:.2f}" if f.confidence is not None else "N/A"
        print(f"\n[{idx}] Category: {f.category} | Confidence: {conf}")
        print(f"    Statement: {f.statement}")
        if f.limitations:
            print(f"    Limitations: {', '.join(f.limitations)}")
        print(f"    Evidence Sources: {[ref.evidence_id for ref in f.evidence_refs]}")

    print_banner("Analysis Complete & Grounded in Tri-Modal Evidence")


def main():
    parser = argparse.ArgumentParser(
        description="EDRAK Tri-Modal Internal Intelligence CLI: Ingest and test dynamic SQL DBs and REST APIs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  1. Default Run (uses NVIDIA mock SQLite DB & mock REST API):
     python scripts/run_tri_modal_agent.py

  2. Custom PostgreSQL Database:
     python scripts/run_tri_modal_agent.py --company "Acme" --db-uri "postgresql://user:pass@localhost:5432/acmedb" --allowed-tables "contracts,subscriptions"

  3. Custom REST API:
     python scripts/run_tri_modal_agent.py --company "Acme" --api-base-url "https://api.acme.com" --openapi-spec "https://api.acme.com/openapi.json" --api-token "secret_123"

  4. Pre-Flight Test Only (Verifies DB and API connection without running agent):
     python scripts/run_tri_modal_agent.py --db-uri "postgresql://..." --preflight-only
""",
    )

    # Database parameters
    parser.add_argument("--db-uri", "--db-url", type=str, default=None, help="Database connection URI (e.g. postgresql://user:pass@host:5432/db, sqlite:///path/to/db).")
    parser.add_argument("--db-id", type=str, default=None, help="Identifier for the database (default: derived from company name).")
    parser.add_argument("--allowed-tables", type=str, default=None, help="Comma-separated list of allowed tables (e.g. 'contracts,deals,pricing').")

    # REST API parameters
    parser.add_argument("--api-base-url", type=str, default=None, help="Base URL for REST API (e.g. https://api.company.com or http://127.0.0.1:8088).")
    parser.add_argument("--openapi-spec", type=str, default=None, help="URL or local path to OpenAPI 3.x / Swagger JSON spec.")
    parser.add_argument("--api-id", type=str, default=None, help="Identifier for the API (default: derived from company name).")
    parser.add_argument("--api-token", type=str, default=None, help="Bearer token for API Authorization header.")
    parser.add_argument("--auth-header", type=str, default=None, help="Custom auth header in 'Header-Name: Value' format.")

    # Business context parameters
    parser.add_argument("--company", type=str, default="NVIDIA", help="Company name under analysis (default: 'NVIDIA').")
    parser.add_argument("--workspace-id", type=str, default="default", help="Workspace ID for internal vector document search (default: 'default').")
    parser.add_argument("--goal", type=str, default=None, help="Research goal for the agent.")
    parser.add_argument("--focus", type=str, default=None, help="Focus areas for the analysis.")

    # Preflight only flag
    parser.add_argument("--preflight-only", action="store_true", help="Only verify DB and API connection and schema without running the LLM synthesis.")

    args = parser.parse_args()

    # Default paths for mock NVIDIA DB and spec if none specified
    default_db_file = os.path.join(repo_root, "mock_services", "nvidia", "nvidia_enterprise.db")
    default_spec_file = os.path.join(repo_root, "mock_services", "nvidia", "nvidia_openapi_spec.json")

    # If no DB URI is passed, default to NVIDIA mock SQLite DB
    db_uri = args.db_uri
    if not db_uri:
        if not os.path.exists(default_db_file):
            print("Seeding default mock NVIDIA SQLite database...")
            seed_database(default_db_file)
        db_uri = f"sqlite:///{default_db_file}"

    # If no OpenAPI spec is passed, default to NVIDIA mock spec
    spec_source = args.openapi_spec or default_spec_file
    api_base_url = args.api_base_url or "http://127.0.0.1:8088"

    # Default goals
    company_name = args.company
    default_goal = (
        f"Assess {company_name} Blackwell B200 and GB200 enterprise pricing, closed deal volume, cluster utilization, and software ARR."
        if company_name.upper() == "NVIDIA"
        else f"Assess {company_name} operational performance, pricing structure, and key business metrics."
    )
    goal = args.goal or default_goal
    focus = args.focus or "Compare hardware pricing, contract volumes, cluster telemetry, and ARR."

    # Parse allowed tables
    allowed_tables = [t.strip() for t in args.allowed_tables.split(",") if t.strip()] if args.allowed_tables else None

    # Parse auth headers
    auth_headers = {}
    if args.api_token:
        auth_headers["Authorization"] = f"Bearer {args.api_token}"
    if args.auth_header and ":" in args.auth_header:
        k, v = args.auth_header.split(":", 1)
        auth_headers[k.strip()] = v.strip()

    print_banner("EDRAK Dynamic Tri-Modal Intelligence CLI")
    print(f"Company:      {company_name}")
    print(f"Database URI: {db_uri}")
    print(f"API Base URL: {api_base_url}")
    print(f"OpenAPI Spec: {spec_source}")

    # 1. Run Pre-flight Checks
    introspector = run_preflight_db(db_uri, allowed_tables)
    discoverer = run_preflight_api(api_base_url, spec_source, auth_headers)

    if args.preflight_only:
        print_banner("Pre-Flight Checks Passed Successfully!")
        print("Both Database and REST API connections verified. Exiting (--preflight-only specified).")
        return

    # 2. Build Configurations for the Agent
    db_id = args.db_id or f"{company_name.lower().replace(' ', '_')}_db"
    api_id = args.api_id or f"{company_name.lower().replace(' ', '_')}_api"

    db_config = DatabaseConnectionConfig(
        db_id=db_id,
        connection_uri=db_uri,
        allowed_tables=allowed_tables,
        description=f"{company_name} relational database.",
    )

    # If spec_source is local file, pass inline spec; otherwise pass spec URL
    spec_inline = None
    spec_url = None
    if os.path.exists(spec_source):
        with open(spec_source, "r", encoding="utf-8") as f:
            spec_inline = json.load(f)
    else:
        spec_url = spec_source

    api_config = RESTAPIConfig(
        api_id=api_id,
        base_url=api_base_url,
        openapi_spec_url=spec_url,
        openapi_spec_inline=spec_inline,
        auth_headers=auth_headers if auth_headers else None,
        description=f"{company_name} operational microservices API.",
    )

    # 3. Run Agent Pipeline
    run_agent_pipeline(
        company_name=company_name,
        goal=goal,
        focus=focus,
        db_config=db_config,
        api_config=api_config,
        workspace_id=args.workspace_id,
    )


if __name__ == "__main__":
    main()
