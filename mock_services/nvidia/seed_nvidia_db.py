"""Seeds a rich mock SQLite relational database for NVIDIA enterprise intelligence testing.

Tables included:
  1. gpu_products (with technical specs: bandwidth, NVLink, compute capability, FLOPS)
  2. enterprise_deals (14 enterprise deals across NA / EMEA / APAC / MENA / Sovereign AI)
  3. software_arr (ARR subscriptions, growth, pricing models)
  4. supply_chain_constraints (CoWoS, TSMC allocation, lead times, bottlenecks)
  5. competitor_benchmarks (H100/H200/B200 vs AMD MI300X/MI325X vs Google TPU)
  6. sales_pipeline_forecast (open pipeline, stage, expected close, probability)
  7. export_control_flags (controlled destinations, BIS regulations, license type)
  8. software_catalog (CUDA, cuDNN, TensorRT, TensorRT-LLM, Triton, NVAIE, NIM, NeMo)
  9. framework_compatibility (PyTorch, TensorFlow, JAX, ONNX Runtime versions & CUDA pairings)
 10. regional_cloud_availability (AWS, Azure, GCP, OCI instance types, pricing, and regional status)
"""

import os
import sqlite3
from pathlib import Path


def seed_database(db_path: str) -> None:
    """Creates tables and seeds rich mock NVIDIA internal enterprise data."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # 1. GPU Products
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE gpu_products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        architecture TEXT NOT NULL,
        segment TEXT NOT NULL,
        target_asp_usd REAL NOT NULL,
        target_gross_margin_pct REAL NOT NULL,
        release_quarter TEXT NOT NULL,
        tdp_watts INTEGER,
        hbm_capacity_gb INTEGER,
        memory_bandwidth_tbps REAL,
        nvlink_version TEXT,
        nvlink_bandwidth_gbps INTEGER,
        cuda_compute_capability TEXT,
        fp8_tflops REAL,
        bf16_tflops REAL
    );
    """)
    cur.executemany("""
    INSERT INTO gpu_products
        (product_name, architecture, segment, target_asp_usd, target_gross_margin_pct,
         release_quarter, tdp_watts, hbm_capacity_gb, memory_bandwidth_tbps,
         nvlink_version, nvlink_bandwidth_gbps, cuda_compute_capability, fp8_tflops, bf16_tflops)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("GB200 NVL72 rack",        "Blackwell",       "Hyperscaler",         2_900_000.0, 75.0, "Q2 FY2026", 120_000, 13_824, 576.0, "NVLink 5.0", 1800, "10.0", 1_440_000.0, 720_000.0),
        ("B200 GPU",                "Blackwell",       "Enterprise / Cloud",     32_500.0, 73.5, "Q2 FY2026",     700,    192,   8.0, "NVLink 5.0", 1800, "10.0",     9_000.0,   4_500.0),
        ("H200 GPU",                "Hopper",          "Enterprise / Cloud",     27_000.0, 71.0, "Q1 FY2025",     700,    141,   4.8, "NVLink 4.0",  900,  "9.0",     3_958.0,   1_979.0),
        ("H100 GPU (legacy)",       "Hopper",          "Price-sensitive",        19_500.0, 66.5, "Q3 FY2023",     700,     80,   3.35,"NVLink 4.0",  900,  "9.0",     3_958.0,   1_979.0),
        ("Spectrum-X switch bundle","Spectrum-X",      "Enterprise Ethernet",   250_000.0, 62.5, "Q4 FY2024",    None,   None,  None, "Spectrum-4", 51200, None,         None,      None),
        ("GB300 GPU (preview)",     "Blackwell Ultra", "Hyperscaler Preview",    45_000.0, 76.0, "Q3 FY2026",     800,    288,  10.0, "NVLink 5.0", 1800, "10.0",    12_000.0,   6_000.0),
        ("DGX H200 system",         "Hopper",          "On-Prem Enterprise",    300_000.0, 68.0, "Q2 FY2025",  10_200,  1_128,  38.4, "NVLink 4.0",  900,  "9.0",    31_664.0,  15_832.0),
        ("A100 GPU (legacy)",       "Ampere",          "Maintenance / Budget",   12_000.0, 58.0, "Q2 FY2020",     400,     80,   2.0, "NVLink 3.0",  600,  "8.0",        None,     312.0),
    ])

    # ------------------------------------------------------------------
    # 2. Enterprise Deals
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE enterprise_deals (
        deal_id TEXT PRIMARY KEY,
        customer_name TEXT NOT NULL,
        segment TEXT NOT NULL,
        region TEXT NOT NULL,
        product_name TEXT NOT NULL,
        units_ordered INTEGER NOT NULL,
        discount_pct REAL NOT NULL,
        total_deal_value_usd REAL NOT NULL,
        approval_status TEXT NOT NULL,
        deal_date TEXT NOT NULL,
        deal_desk_owner TEXT
    );
    """)
    cur.executemany("""
    INSERT INTO enterprise_deals
        (deal_id, customer_name, segment, region, product_name, units_ordered,
         discount_pct, total_deal_value_usd, approval_status, deal_date, deal_desk_owner)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("DEAL-2026-001", "Microsoft Azure",          "Hyperscaler",    "North America", "GB200 NVL72 rack",        120,   14.5, 297_540_000.0, "Approved (VP Deal Desk)",   "2026-01-15", "Sarah Chen"),
        ("DEAL-2026-002", "Amazon AWS",               "Hyperscaler",    "North America", "B200 GPU",              15_000,  15.0, 414_375_000.0, "Approved (VP Deal Desk)",   "2026-02-01", "Sarah Chen"),
        ("DEAL-2026-003", "CoreWeave",                "Tier 2 Cloud",   "North America", "B200 GPU",               6_000,  11.0, 173_550_000.0, "Approved (Sales Director)", "2026-02-18", "James Park"),
        ("DEAL-2026-004", "Sovereign AI France/Mistral","Sovereign AI", "EMEA",          "H200 GPU",               3_000,   9.5,  73_305_000.0, "Approved (Govt Desk)",      "2026-03-02", "Lena Müller"),
        ("DEAL-2026-005", "Saudi Sovereign AI (SDAIA)","Sovereign AI",  "MENA",          "GB200 NVL72 rack",         40,   12.0, 102_080_000.0, "Approved (Govt Desk)",      "2026-03-10", "Lena Müller"),
        ("DEAL-2026-006", "Meta Platforms",           "Hyperscaler",    "North America", "B200 GPU",              25_000,  15.0, 690_625_000.0, "Approved (CFO Review)",     "2026-03-22", "Sarah Chen"),
        ("DEAL-2026-007", "Lambda Labs",              "Tier 2 Cloud",   "North America", "H200 GPU",               2_500,   9.0,  61_425_000.0, "Approved (Sales Director)", "2026-03-29", "James Park"),
        ("DEAL-2026-008", "Google Cloud (GCP)",       "Hyperscaler",    "North America", "B200 GPU",              20_000,  15.0, 552_500_000.0, "Approved (CFO Review)",     "2026-04-05", "Sarah Chen"),
        ("DEAL-2026-009", "UAE G42",                  "Sovereign AI",   "MENA",          "H200 GPU",               5_000,  10.0, 121_500_000.0, "Approved (Govt Desk)",      "2026-04-12", "Lena Müller"),
        ("DEAL-2026-010", "Softbank Japan",           "Tier 1 Cloud",   "APAC",          "GB200 NVL72 rack",         30,   11.5,  77_085_000.0, "Approved (VP Deal Desk)",   "2026-04-18", "Kevin Tanaka"),
        ("DEAL-2026-011", "Oracle Cloud",             "Enterprise",     "North America", "DGX H200 system",          50,    8.0,  13_800_000.0, "Approved (Sales Director)", "2026-04-25", "James Park"),
        ("DEAL-2026-012", "Deutsche Telekom AI Lab",  "Sovereign AI",   "EMEA",          "H200 GPU",               1_200,   9.0,  29_484_000.0, "Approved (Govt Desk)",      "2026-05-03", "Lena Müller"),
        ("DEAL-2026-013", "Tencent Cloud",            "Hyperscaler",    "APAC",          "H200 GPU",               8_000,  12.0, 190_080_000.0, "Approved (VP Deal Desk)",   "2026-05-10", "Kevin Tanaka"),
        ("DEAL-2026-014", "NVIDIA DGX SuperPOD demo", "Internal",       "North America", "GB200 NVL72 rack",          4,    0.0,  11_600_000.0, "Internal Allocation",       "2026-05-15", "Engineering"),
    ])

    # ------------------------------------------------------------------
    # 3. Software Subscription ARR
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE software_arr (
        subscription_id TEXT PRIMARY KEY,
        offering_name TEXT NOT NULL,
        pricing_model TEXT NOT NULL,
        annual_list_price_usd REAL NOT NULL,
        active_licenses INTEGER NOT NULL,
        total_arr_usd REAL NOT NULL,
        fiscal_year INTEGER NOT NULL,
        yoy_growth_pct REAL
    );
    """)
    cur.executemany("""
    INSERT INTO software_arr
        (subscription_id, offering_name, pricing_model, annual_list_price_usd,
         active_licenses, total_arr_usd, fiscal_year, yoy_growth_pct)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("SUB-2026-01", "NVIDIA AI Enterprise",      "Per GPU / year",              4_500.0,  320_000, 1_440_000_000.0, 2026, 42.0),
        ("SUB-2026-02", "NIM microservices",          "Per GPU / hour (annualized)", 8_760.0,   95_000,   832_200_000.0, 2026, 68.0),
        ("SUB-2026-03", "DGX Cloud",                 "Monthly per node (ann.)",   444_000.0,    1_850,   821_400_000.0, 2026, 55.0),
        ("SUB-2026-04", "NVIDIA Omniverse Enterprise","Per seat / year",             3_000.0,   22_000,    66_000_000.0, 2026, 29.0),
        ("SUB-2026-05", "Base Command Platform",      "Per cluster / month (ann.)",600_000.0,      640,   384_000_000.0, 2026, 37.0),
    ])

    # ------------------------------------------------------------------
    # 4. Supply Chain Constraints
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE supply_chain_constraints (
        constraint_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        supplier TEXT NOT NULL,
        component TEXT NOT NULL,
        lead_time_weeks INTEGER NOT NULL,
        allocation_pct REAL NOT NULL,
        bottleneck_severity TEXT NOT NULL,
        as_of_quarter TEXT NOT NULL,
        notes TEXT
    );
    """)
    cur.executemany("""
    INSERT INTO supply_chain_constraints
        (product_name, supplier, component, lead_time_weeks,
         allocation_pct, bottleneck_severity, as_of_quarter, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("GB200 NVL72 rack", "TSMC",           "CoWoS-L packaging",       22, 88.5, "Critical",  "Q1 FY2026", "TSMC capacity fully allocated to NVIDIA through Q3 FY2026"),
        ("B200 GPU",         "TSMC",           "CoWoS-S packaging",       14, 85.0, "High",      "Q1 FY2026", "Constrained by TSMC N4P node ramp"),
        ("GB200 NVL72 rack", "Vertiv / Stulz", "Liquid cooling manifold", 18, 72.0, "High",      "Q1 FY2026", "CDU lead times extended due to copper shortage"),
        ("H200 GPU",         "TSMC",           "HBM3e (SK Hynix)",         6, 95.0, "Moderate",  "Q1 FY2026", "SK Hynix HBM3e qualification completed"),
        ("B200 GPU",         "SK Hynix",       "HBM3e stacking",          10, 80.0, "High",      "Q1 FY2026", "HBM3e yield ramp ongoing at SK Hynix"),
        ("Spectrum-X switch bundle","Foxconn",  "Passive optical cables",   8, 65.0, "Moderate",  "Q1 FY2026", "Optical transceiver allocation from II-VI / Coherent"),
        ("GB300 GPU (preview)","TSMC",          "N3E process / CoWoS-L",   28, 60.0, "Critical",  "Q2 FY2026", "Early engineering samples only; mass production Q3 FY2026"),
    ])

    # ------------------------------------------------------------------
    # 5. Competitor Benchmarks
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE competitor_benchmarks (
        benchmark_id INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_name TEXT NOT NULL,
        workload_type TEXT NOT NULL,
        nvidia_product TEXT NOT NULL,
        nvidia_score REAL NOT NULL,
        nvidia_score_unit TEXT NOT NULL,
        competitor_product TEXT NOT NULL,
        competitor_score REAL NOT NULL,
        nvidia_advantage_pct REAL NOT NULL,
        source TEXT NOT NULL,
        benchmark_date TEXT NOT NULL
    );
    """)
    cur.executemany("""
    INSERT INTO competitor_benchmarks
        (benchmark_name, workload_type, nvidia_product, nvidia_score, nvidia_score_unit,
         competitor_product, competitor_score, nvidia_advantage_pct, source, benchmark_date)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("MLPerf v4.0 LLM Training",  "LLM Training (GPT-3 175B)", "H100 SXM5 8-GPU", 4.68,  "samples/sec",  "AMD MI300X 8-GPU",       3.12,  50.0, "MLCommons MLPerf v4.0",        "2024-06-01"),
        ("MLPerf v4.0 Inference",     "LLM Inference (Llama2-70B)","H100 SXM5 8-GPU", 62500, "tokens/sec",   "AMD MI300X 8-GPU",      47800,  30.7, "MLCommons MLPerf v4.0",        "2024-06-01"),
        ("MLPerf v4.1 Training",      "LLM Training (GPT-3 175B)", "H200 SXM5 8-GPU", 6.10,  "samples/sec",  "AMD MI300X 8-GPU",       3.30,  84.8, "MLCommons MLPerf v4.1",        "2024-11-01"),
        ("MLPerf v4.1 Inference",     "LLM Inference (Llama2-70B)","H200 SXM5 8-GPU", 78000, "tokens/sec",   "Google TPUv5e (256 chip)",55000, 41.8, "MLCommons MLPerf v4.1",        "2024-11-01"),
        ("Blackwell B200 Training",   "LLM Training (GPT-4 scale)","B200 SXM5 8-GPU", 9.80,  "samples/sec",  "AMD MI325X 8-GPU",       5.10,  92.2, "NVIDIA Internal Benchmark",    "2025-03-15"),
        ("Blackwell B200 Inference",  "LLM Inference (Llama3-70B)","B200 SXM5 8-GPU",140000, "tokens/sec",   "AMD MI325X 8-GPU",      88000,  59.1, "NVIDIA Internal Benchmark",    "2025-03-15"),
        ("Memory Bandwidth",          "HBM Bandwidth (GB/s)",       "H200 SXM5",       4900,  "GB/s",         "AMD MI300X",             5200,  -5.8, "AnandTech",                    "2024-09-01"),
        ("TCO per TFLOP",             "Total Cost of Ownership",    "H100 SXM5 cluster",1.00, "normalized",   "AMD MI300X cluster",      1.08,   7.4, "Moor Insights TCO Report",     "2024-08-15"),
    ])

    # ------------------------------------------------------------------
    # 6. Sales Pipeline Forecast
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE sales_pipeline_forecast (
        opportunity_id TEXT PRIMARY KEY,
        account_name TEXT NOT NULL,
        segment TEXT NOT NULL,
        region TEXT NOT NULL,
        product_mix TEXT NOT NULL,
        estimated_units INTEGER NOT NULL,
        estimated_value_usd REAL NOT NULL,
        deal_stage TEXT NOT NULL,
        close_probability_pct REAL NOT NULL,
        expected_close_quarter TEXT NOT NULL,
        assigned_ae TEXT
    );
    """)
    cur.executemany("""
    INSERT INTO sales_pipeline_forecast
        (opportunity_id, account_name, segment, region, product_mix, estimated_units,
         estimated_value_usd, deal_stage, close_probability_pct, expected_close_quarter, assigned_ae)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("OPP-2026-Q2-001", "Apple (Private AI)",    "Enterprise",     "North America", "B200 GPU",              10_000,  325_000_000.0, "Final Negotiation",  85.0, "Q2 FY2026", "Alex Johnson"),
        ("OPP-2026-Q2-002", "Alibaba Cloud",          "Hyperscaler",    "APAC",          "H200 GPU",              12_000,  292_320_000.0, "Proposal Sent",      60.0, "Q2 FY2026", "Kevin Tanaka"),
        ("OPP-2026-Q2-003", "Saudi Aramco AI",        "Sovereign AI",   "MENA",          "GB200 NVL72 rack",          25,   71_575_000.0, "Qualification",      40.0, "Q3 FY2026", "Lena Müller"),
        ("OPP-2026-Q2-004", "Siemens Industrial AI",  "Enterprise",     "EMEA",          "DGX H200 system",           80,   22_080_000.0, "Proof of Concept",   55.0, "Q3 FY2026", "Lena Müller"),
        ("OPP-2026-Q2-005", "ByteDance (TikTok AI)",  "Hyperscaler",    "APAC",          "B200 GPU",               8_000,  260_000_000.0, "Compliance Hold",    30.0, "Q4 FY2026", "Kevin Tanaka"),
        ("OPP-2026-Q2-006", "JP Morgan AI Platform",  "Financial Svcs", "North America", "DGX H200 system",          120,   33_120_000.0, "Procurement Review", 70.0, "Q2 FY2026", "Alex Johnson"),
        ("OPP-2026-Q2-007", "Indian Govt AI Mission", "Sovereign AI",   "APAC",          "H200 GPU",               6_000,  146_880_000.0, "RFP Stage",          25.0, "Q4 FY2026", "Kevin Tanaka"),
        ("OPP-2026-Q2-008", "Cerebras Cloud",         "Tier 2 Cloud",   "North America", "H100 GPU (legacy)",      3_000,   52_650_000.0, "Closed Won",        100.0, "Q1 FY2026", "James Park"),
    ])

    # ------------------------------------------------------------------
    # 7. Export Control Flags
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE export_control_flags (
        flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
        country_or_entity TEXT NOT NULL,
        control_status TEXT NOT NULL,
        license_type TEXT NOT NULL,
        applies_to_products TEXT NOT NULL,
        restriction_basis TEXT NOT NULL,
        effective_date TEXT NOT NULL,
        notes TEXT
    );
    """)
    cur.executemany("""
    INSERT INTO export_control_flags
        (country_or_entity, control_status, license_type,
         applies_to_products, restriction_basis, effective_date, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """, [
        ("China (general)",       "Restricted",       "BIS License Required", "H100, H200, B200, GB200",   "US BIS EAR 742.6(a)(6)(ii)", "2023-10-17", "A100/H100 equivalents >4800 TOPS restricted per Oct 2023 BIS rule"),
        ("Russia",                "Prohibited",        "No Export Permitted",  "All NVIDIA GPUs",           "OFAC SDN + EAR Part 746",    "2022-03-01", "Comprehensive embargo; no licence available"),
        ("Iran",                  "Prohibited",        "No Export Permitted",  "All NVIDIA GPUs",           "OFAC Iran Regulations",      "2010-06-09", "Comprehensive sanctions"),
        ("North Korea",           "Prohibited",        "No Export Permitted",  "All NVIDIA GPUs",           "OFAC DPRK Regulations",      "2008-01-01", "Comprehensive sanctions"),
        ("ByteDance entities",    "Under Review",      "Compliance Hold",      "B200, GB200",               "US Entity List (proposed)",   "2026-04-01", "US DOC entity list investigation ongoing"),
        ("UAE G42",               "Conditional",       "End-User Cert. Reqd.", "B200, GB200",               "BIS VEU conditions",          "2024-06-01", "G42 signed binding commitment per US-UAE AI deal (May 2024)"),
        ("Saudi Arabia (SDAIA)",  "Conditional",       "Govt-to-Govt License", "GB200, H200",               "BIS Exceptional Approval",   "2024-09-01", "Approved under US-Saudi AI cooperation framework"),
        ("India",                 "General Permitted", "EAR99 / NLR",          "H100, H200, B200",          "India non-restricted country","2024-01-01", "India not on restricted country list; standard EAR controls apply"),
    ])

    # ------------------------------------------------------------------
    # 8. Software Catalog (NEW)
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE software_catalog (
        software_id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_name TEXT NOT NULL,
        category TEXT NOT NULL,
        license_type TEXT NOT NULL,
        purpose_description TEXT NOT NULL,
        supported_gpus TEXT NOT NULL,
        cuda_version_min TEXT NOT NULL,
        pricing_model TEXT NOT NULL
    );
    """)
    cur.executemany("""
    INSERT INTO software_catalog
        (component_name, category, license_type, purpose_description,
         supported_gpus, cuda_version_min, pricing_model)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """, [
        ("CUDA Toolkit",            "Platform SDK",         "Proprietary Free",  "Parallel computing platform and C/C++/Python compiler tools", "A100, H100, H200, B200, GB200", "11.0", "Free"),
        ("cuDNN",                   "Primitives Library",   "Proprietary Free",  "Deep neural network primitives (convolutions, flash-attention, pooling)", "A100, H100, H200, B200", "11.8", "Free"),
        ("TensorRT",                "Inference Engine",     "Proprietary Free",  "Deep learning inference optimizer and serialized engine runtime", "A100, H100, H200, B200", "12.0", "Free"),
        ("TensorRT-LLM",            "LLM Inference Library","Apache 2.0 Open",   "In-flight batching, paged KV-cache, and KV compression for LLMs", "H100, H200, B200", "12.2", "Free / Open Source"),
        ("NCCL",                    "Interconnect Comms",   "BSD-3 Open",        "Multi-GPU/multi-node collective communication primitives", "A100, H100, H200, B200, GB200", "11.0", "Free / Open Source"),
        ("Triton Inference Server", "Model Serving Platform","BSD-3 Open",       "Multi-framework (PyTorch, TRT, ONNX) concurrent model serving endpoint", "A100, H100, H200, B200", "11.8", "Free / Open Source"),
        ("NVIDIA AI Enterprise 5.x","Commercial Suite",     "Enterprise Commercial","Turnkey enterprise platform with SLA, 24/7 support, security patches", "A100, H100, H200, B200", "12.0", "$4,500/GPU/year or $1.00/GPU/hour"),
        ("NIM Microservices",       "Commercial Containers","Enterprise Commercial","Pre-built containerized inference endpoints for 100+ open foundation models", "H100, H200, B200", "12.2", "Included in NVAIE ($1.00/GPU/hr)"),
        ("NeMo Framework",          "Training & Guardrails","Enterprise Commercial","Distributed 3D parallelism training, alignment, curation, and guardrails", "H100, H200, B200", "12.2", "Included in NVAIE"),
        ("DGX Cloud Orchestrator",  "Managed SaaS",         "Enterprise Commercial","Fully managed cloud supercomputing infrastructure and scheduler", "H100, H200, B200", "12.0", "$36,900/node/month"),
    ])

    # ------------------------------------------------------------------
    # 9. Framework Compatibility Matrix (NEW)
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE framework_compatibility (
        compatibility_id INTEGER PRIMARY KEY AUTOINCREMENT,
        framework_name TEXT NOT NULL,
        framework_version TEXT NOT NULL,
        cuda_version_required TEXT NOT NULL,
        driver_version_min TEXT NOT NULL,
        supported_gpus TEXT NOT NULL,
        native_fp8_support TEXT NOT NULL,
        native_fp4_support TEXT NOT NULL,
        status TEXT NOT NULL
    );
    """)
    cur.executemany("""
    INSERT INTO framework_compatibility
        (framework_name, framework_version, cuda_version_required, driver_version_min,
         supported_gpus, native_fp8_support, native_fp4_support, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, [
        ("PyTorch",       "2.4.x",  "CUDA 12.4", "550.90.07", "A100, H100, H200, B200", "Yes (torch.float8_e4m3fn)", "Yes (via TorchAO)", "Generally Available"),
        ("PyTorch",       "2.3.x",  "CUDA 12.1", "535.154.05", "A100, H100, H200",       "Yes (torch.float8_e4m3fn)", "No",                 "Generally Available"),
        ("TensorFlow",    "2.17.x", "CUDA 12.4", "550.90.07", "A100, H100, H200, B200", "Yes (XLA GEMM)",           "Experimental",       "Generally Available"),
        ("TensorFlow",    "2.16.x", "CUDA 12.3", "535.154.05", "A100, H100, H200",       "Yes (XLA GEMM)",           "No",                 "Maintenance"),
        ("JAX",           "0.4.31", "CUDA 12.4", "550.90.07", "A100, H100, H200, B200", "Yes (Pallas kernels)",     "Experimental",       "Generally Available"),
        ("ONNX Runtime",  "1.19.x", "CUDA 12.4", "550.90.07", "A100, H100, H200, B200", "Yes (TensorRT 10.3 EP)",   "Yes",                "Generally Available"),
    ])

    # ------------------------------------------------------------------
    # 10. Regional Cloud Availability (NEW)
    # ------------------------------------------------------------------
    cur.execute("""
    CREATE TABLE regional_cloud_availability (
        availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
        cloud_provider TEXT NOT NULL,
        region_name TEXT NOT NULL,
        gpu_product TEXT NOT NULL,
        instance_type TEXT NOT NULL,
        hourly_price_usd REAL NOT NULL,
        availability_status TEXT NOT NULL,
        launch_date TEXT NOT NULL
    );
    """)
    cur.executemany("""
    INSERT INTO regional_cloud_availability
        (cloud_provider, region_name, gpu_product, instance_type,
         hourly_price_usd, availability_status, launch_date)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """, [
        ("AWS",          "us-east-1 (N. Virginia)", "8x H100 SXM5", "p5.48xlarge",    98.32,  "Generally Available", "2023-08-01"),
        ("AWS",          "us-west-2 (Oregon)",      "8x H200 SXM5", "p5en.48xlarge",  124.50, "Generally Available", "2024-10-15"),
        ("AWS",          "eu-central-1 (Frankfurt)","8x H100 SXM5", "p5.48xlarge",   108.15,  "Generally Available", "2024-02-01"),
        ("Azure",        "East US",                 "8x H100 SXM5", "ND96isr H100 v5", 96.80, "Generally Available", "2023-09-01"),
        ("Azure",        "West Europe (NL)",        "8x H200 SXM5", "ND96isr H200 v5",129.40, "Generally Available", "2024-11-01"),
        ("Azure",        "Central US",              "GB200 NVL72",  "NDv6 Blackwell",  950.00, "Preview / Tier-1",    "2025-06-01"),
        ("Google Cloud", "us-central1 (Iowa)",      "8x H100 SXM5", "a3-highgpu-8g",   97.20,  "Generally Available", "2023-11-01"),
        ("Google Cloud", "europe-west4 (Eemshaven)","8x H200 SXM5", "a3-megagpu-8g", 126.80,  "Generally Available", "2025-01-15"),
        ("Oracle OCI",   "us-ashburn-1",            "8x H100 SXM5", "BM.GPU.H100.8",   80.00,  "Generally Available", "2023-07-01"),
        ("Oracle OCI",   "eu-frankfurt-1",          "8x H200 SXM5", "BM.GPU.H200.8",  105.00,  "Generally Available", "2024-12-01"),
        ("CoreWeave",    "US-East (New Jersey)",    "8x H100 SXM5", "HGX H100",        63.84,  "Generally Available", "2023-06-01"),
    ])

    conn.commit()
    conn.close()
    print(f"Successfully seeded expanded NVIDIA SQLite Database at: {db_path}")
    print("Tables created (10): gpu_products, enterprise_deals, software_arr, supply_chain_constraints, "
          "competitor_benchmarks, sales_pipeline_forecast, export_control_flags, software_catalog, "
          "framework_compatibility, regional_cloud_availability")


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(__file__), "nvidia_enterprise.db")
    seed_database(default_path)
