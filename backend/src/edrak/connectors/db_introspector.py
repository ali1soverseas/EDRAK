"""Dynamic Two-Tier Schema Introspector for Relational Databases."""

import logging
from typing import Any, Dict, List, Optional
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from edrak.connectors.sql_guardrail import validate_and_sanitize_sql

logger = logging.getLogger(__name__)


class DatabaseSchemaIntrospector:
    """Introspects arbitrary SQL databases dynamically without hardcoded schemas."""

    def __init__(self, connection_uri: str, allowed_tables: Optional[List[str]] = None):
        self.connection_uri = connection_uri
        self.allowed_tables = set(allowed_tables) if allowed_tables else None
        # Connect with pre-ping to ensure active connection
        self.engine: Engine = sa.create_engine(connection_uri, pool_pre_ping=True)
        self.inspector = sa.inspect(self.engine)

    def list_table_catalog(self) -> List[Dict[str, Any]]:
        """Tier 1: Fast table catalog to avoid context window explosion."""
        all_table_names = self.inspector.get_table_names()
        catalog = []
        with self.engine.connect() as conn:
            for name in all_table_names:
                if self.allowed_tables and name not in self.allowed_tables:
                    continue
                # Get approximate row count safely
                row_count = 0
                try:
                    res = conn.execute(sa.text(f"SELECT COUNT(*) FROM {name}"))
                    row_count = res.scalar() or 0
                except Exception as e:
                    logger.debug("Could not count rows for table %s: %s", name, e)

                catalog.append({
                    "table_name": name,
                    "estimated_row_count": row_count,
                })
        return catalog

    def introspect_selected_tables(self, table_names: List[str]) -> Dict[str, Any]:
        """Tier 2: Detailed DDL, column types, foreign keys, and 2 sample rows for chosen tables."""
        schema_details: Dict[str, Any] = {}
        with self.engine.connect() as conn:
            for table in table_names:
                if self.allowed_tables and table not in self.allowed_tables:
                    continue
                try:
                    columns = self.inspector.get_columns(table)
                    pk = self.inspector.get_pk_constraint(table)
                    fks = self.inspector.get_foreign_keys(table)

                    # Fetch 2 sample rows to show formatting of actual values
                    sample_rows = []
                    try:
                        result = conn.execute(sa.text(f"SELECT * FROM {table} LIMIT 2"))
                        sample_rows = [dict(row._mapping) for row in result]
                    except Exception as e:
                        logger.warning("Error fetching sample rows for %s: %s", table, e)

                    schema_details[table] = {
                        "table_name": table,
                        "columns": [
                            {
                                "name": col["name"],
                                "type": str(col["type"]),
                                "nullable": col.get("nullable", True),
                            }
                            for col in columns
                        ],
                        "primary_key": pk.get("constrained_columns", []) if pk else [],
                        "foreign_keys": [
                            {
                                "constrained": fk.get("constrained_columns"),
                                "referred_table": fk.get("referred_table"),
                            }
                            for fk in fks
                        ],
                        "sample_records": sample_rows,
                    }
                except Exception as e:
                    logger.error("Failed to introspect table %s: %s", table, e)
        return schema_details

    def execute_query(self, sql_query: str, default_limit: int = 100, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """Validates query against AST guardrails and executes safely with row limits."""
        sanitized_sql = validate_and_sanitize_sql(sql_query, default_limit=default_limit)
        records: List[Dict[str, Any]] = []

        with self.engine.connect() as conn:
            # Set statement execution options if supported
            conn = conn.execution_options(timeout=timeout)
            result = conn.execute(sa.text(sanitized_sql))
            for row in result:
                records.append(dict(row._mapping))

        return records
