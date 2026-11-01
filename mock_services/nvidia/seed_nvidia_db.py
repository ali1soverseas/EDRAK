"""Seeds a realistic mock SQLite relational database for NVIDIA enterprise data."""

import os
import sqlite3
from pathlib import Path


def seed_database(db_path: str) -> None:
    """Creates tables and seeds mock NVIDIA internal enterprise data."""
    # Ensure directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Remove existing db if present to ensure clean seed
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. GPU Products Table
    cursor.execute("""
    CREATE TABLE gpu_products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        architecture TEXT NOT NULL,
        segment TEXT NOT NULL,
        target_asp_usd REAL NOT NULL,
        target_gross_margin_pct REAL NOT NULL,
        release_quarter TEXT NOT NULL
    );
    """)

    gpu_products = [
        ("GB200 NVL72 rack", "Blackwell", "Hyperscaler", 2900000.0, 75.0, "Q2 FY2026"),
        ("B200 GPU", "Blackwell", "Enterprise / Cloud", 32500.0, 73.5, "Q2 FY2026"),
        ("H200 GPU", "Hopper", "Enterprise / Cloud", 27000.0, 71.0, "Q1 FY2025"),
        ("H100 GPU (legacy)", "Hopper", "Price-sensitive", 19500.0, 66.5, "Q3 FY2023"),
        ("Spectrum-X switch bundle", "Spectrum-X", "Enterprise Ethernet", 250000.0, 62.5, "Q4 FY2024"),
    ]
    cursor.executemany("""
    INSERT INTO gpu_products (product_name, architecture, segment, target_asp_usd, target_gross_margin_pct, release_quarter)
    VALUES (?, ?, ?, ?, ?, ?);
    """, gpu_products)

    # 2. Enterprise Closed Deals Table
    cursor.execute("""
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
        deal_date TEXT NOT NULL
    );
    """)

    enterprise_deals = [
        ("DEAL-2026-001", "Microsoft Azure", "Hyperscaler", "North America", "GB200 NVL72 rack", 120, 14.5, 297540000.0, "Approved (VP Deal Desk)", "2026-01-15"),
        ("DEAL-2026-002", "Amazon AWS", "Hyperscaler", "North America", "B200 GPU", 15000, 15.0, 414375000.0, "Approved (VP Deal Desk)", "2026-02-01"),
        ("DEAL-2026-003", "CoreWeave", "Tier 2 Cloud", "North America", "B200 GPU", 6000, 11.0, 173550000.0, "Approved (Sales Director)", "2026-02-18"),
        ("DEAL-2026-004", "Sovereign AI France / Mistral", "Sovereign AI", "EMEA", "H200 GPU", 3000, 9.5, 73305000.0, "Approved (Govt Desk)", "2026-03-02"),
        ("DEAL-2026-005", "Saudi Sovereign AI (SDAIA)", "Sovereign AI", "MENA", "GB200 NVL72 rack", 40, 12.0, 102080000.0, "Approved (Govt Desk)", "2026-03-10"),
        ("DEAL-2026-006", "Meta Platforms", "Hyperscaler", "North America", "B200 GPU", 25000, 15.0, 690625000.0, "Approved (CFO Review)", "2026-03-22"),
        ("DEAL-2026-007", "Lambda Labs", "Tier 2 Cloud", "North America", "H200 GPU", 2500, 9.0, 61425000.0, "Approved (Sales Director)", "2026-03-29"),
    ]
    cursor.executemany("""
    INSERT INTO enterprise_deals (deal_id, customer_name, segment, region, product_name, units_ordered, discount_pct, total_deal_value_usd, approval_status, deal_date)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, enterprise_deals)

    # 3. Software Subscription ARR Table
    cursor.execute("""
    CREATE TABLE software_arr (
        subscription_id TEXT PRIMARY KEY,
        offering_name TEXT NOT NULL,
        pricing_model TEXT NOT NULL,
        annual_list_price_usd REAL NOT NULL,
        active_licenses INTEGER NOT NULL,
        total_arr_usd REAL NOT NULL,
        fiscal_year INTEGER NOT NULL
    );
    """)

    software_arr = [
        ("SUB-2026-01", "NVIDIA AI Enterprise", "Per GPU / year", 4500.0, 320000, 1440000000.0, 2026),
        ("SUB-2026-02", "NIM microservices", "Per GPU / hour (annualized)", 8760.0, 95000, 832200000.0, 2026),
        ("SUB-2026-03", "DGX Cloud", "Monthly per node (annualized)", 444000.0, 1850, 821400000.0, 2026),
    ]
    cursor.executemany("""
    INSERT INTO software_arr (subscription_id, offering_name, pricing_model, annual_list_price_usd, active_licenses, total_arr_usd, fiscal_year)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """, software_arr)

    conn.commit()
    conn.close()
    print(f"Successfully seeded NVIDIA SQLite Database at: {db_path}")


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(__file__), "nvidia_enterprise.db")
    seed_database(default_path)
