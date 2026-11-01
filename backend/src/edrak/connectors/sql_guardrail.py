"""AST-level SQL guardrails to enforce strict read-only queries and bounded execution."""

from typing import Tuple
import sqlglot
from sqlglot import exp

FORBIDDEN_EXPRESSIONS: Tuple[type, ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Grant,
    exp.Revoke,
    exp.Create,
)


def validate_and_sanitize_sql(raw_sql: str, default_limit: int = 100) -> str:
    """Parses SQL AST to verify read-only safety and enforce row bounds.
    
    1. Prohibits any non-SELECT statements (INSERT, UPDATE, DELETE, DROP, etc.)
    2. Prohibits query stacking / multiple statements.
    3. Injects or caps LIMIT clause to prevent Cartesian product blowups.
    """
    clean_sql = raw_sql.strip().rstrip(";")
    if not clean_sql:
        raise ValueError("Empty SQL query provided.")

    try:
        # Check if multiple statements are passed
        parsed_statements = sqlglot.parse(clean_sql)
        if len(parsed_statements) > 1:
            raise PermissionError("Multiple SQL statements (query stacking) are strictly prohibited.")
        parsed = parsed_statements[0]
    except Exception as e:
        if isinstance(e, PermissionError):
            raise
        raise ValueError(f"Invalid SQL syntax: {e}")

    # Walk AST and search for prohibited mutations
    for expr in parsed.walk():
        if isinstance(expr, FORBIDDEN_EXPRESSIONS):
            raise PermissionError(f"Security Violation: Prohibited SQL expression '{type(expr).__name__}' detected.")

    if not isinstance(parsed, exp.Select):
        raise PermissionError("Security Violation: Only SELECT queries are permitted.")

    # Enforce LIMIT bounds
    limit_clause = parsed.args.get("limit")
    if not limit_clause:
        parsed = parsed.limit(default_limit)
    else:
        try:
            current_limit = int(limit_clause.expression.name)
            if current_limit > default_limit:
                parsed = parsed.limit(default_limit)
        except (AttributeError, ValueError):
            parsed = parsed.limit(default_limit)

    return parsed.sql()
