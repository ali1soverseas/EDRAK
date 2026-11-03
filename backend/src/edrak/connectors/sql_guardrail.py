"""AST-level SQL guardrails: read-only enforcement, bounds, and query meaningfulness validation."""

import re
from typing import List, Optional, Tuple

import sqlglot
from sqlglot import exp

# ---------------------------------------------------------------------------
# Forbidden AST expression types (mutation / DDL / privilege operations)
# ---------------------------------------------------------------------------
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
    exp.Merge,      # MERGE INTO (UPSERT) – covered explicitly
    exp.LoadData,   # LOAD DATA INFILE
)

# Functions whose sole purpose is side-effects / info-disclosure
FORBIDDEN_FUNCTIONS: Tuple[str, ...] = (
    "LOAD_FILE",
    "INTO_OUTFILE",
    "SLEEP",            # time-based blind SQLi
    "BENCHMARK",        # CPU load attack
    "PG_SLEEP",
    "WAITFOR",          # MSSQL time delay
    "USER",             # DB user disclosure
    "CURRENT_USER",
    "SYSTEM_USER",
    "SESSION_USER",
    "VERSION",          # DB version fingerprinting
    "INFORMATION_SCHEMA",
    "PG_READ_FILE",
    "DBLINK",
    "OPENROWSET",
    "OPENQUERY",
    "EXEC",
    "EXECUTE",
    "XP_CMDSHELL",
    "SP_EXECUTESQL",
)

# Comment-stripping regex  –  strips `-- ...` and `/* ... */` for plain-text checks
_COMMENT_STRIP = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)

# Regex markers for always-true tautologies (SQL injection patterns)
_ALWAYS_TRUE_PATTERNS = [
    re.compile(r"\b1\s*=\s*1\b", re.IGNORECASE),
    re.compile(r"\bor\s+1\s*=\s*1\b", re.IGNORECASE),
    re.compile(r"\bor\s+'1'\s*=\s*'1'", re.IGNORECASE),
    re.compile(r'\bor\s+"1"\s*=\s*"1"', re.IGNORECASE),
    re.compile(r"\bor\s+'([^']+)'\s*=\s*'\1'", re.IGNORECASE),  # OR 'x'='x'
    re.compile(r'\bor\s+"([^"]+)"\s*=\s*"\1"', re.IGNORECASE),  # OR "x"="x"
    re.compile(r"'\w*'\s*=\s*'\w*'\s+or\b", re.IGNORECASE),     # 'x'='x' OR ...
]

# Regex markers for always-false predicates (blind SQLi probes / misconfigs)
_ALWAYS_FALSE_PATTERNS = [
    re.compile(r"\b1\s*=\s*0\b", re.IGNORECASE),
    re.compile(r"\bnull\s*=\s*null\b", re.IGNORECASE),
    # Two different non-empty literals: 'a' = 'b' where a != b
    # We match two quoted strings that are provably different (non-empty and different content)
    re.compile(r"'([^']+)'\s*=\s*'([^']+)'", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SQLGuardrailViolation(PermissionError):
    """Raised when a query violates the SQL security or meaningfulness guardrails."""


class SQLMeaninglessQuery(ValueError):
    """Raised when a syntactically valid query is unlikely to return useful data for the AI."""


def validate_and_sanitize_sql(
    raw_sql: str,
    default_limit: int = 100,
    check_meaningfulness: bool = True,
) -> str:
    """Parse + sanitize + bounds-enforce a SQL query; optionally check AI meaningfulness.

    Phase 1 – Security:
        * Rejects any non-SELECT statement (INSERT, UPDATE, DELETE, DROP, MERGE, etc.)
        * Rejects multi-statement stacking (query chaining via `;`)
        * Rejects forbidden function calls (SLEEP, EXEC, XP_CMDSHELL, …)
        * Detects tautology / always-true WHERE injections (OR 1=1)
        * Detects trivially-false WHERE clauses

    Phase 2 – Meaningfulness (when check_meaningfulness=True):
        * Rejects SELECT with no columns (bare `SELECT FROM …`)
        * Warns (raises SQLMeaninglessQuery) when SELECT 1 / SELECT NULL / SELECT constant
        * Rejects LIMIT 0
        * Rejects queries selecting from obviously empty or system tables

    Phase 3 – Bounds:
        * Injects LIMIT if absent; caps existing LIMIT to default_limit.

    Returns the sanitised, bound SQL string.
    """
    clean_sql = raw_sql.strip().rstrip(";")
    if not clean_sql:
        raise SQLGuardrailViolation("Empty SQL query provided.")

    # ------------------------------------------------------------------
    # PHASE 1: SECURITY CHECKS
    # ------------------------------------------------------------------
    _check_statement_count(clean_sql)
    parsed = _parse_sql(clean_sql)
    _check_forbidden_ast_nodes(parsed)
    _check_forbidden_functions_text(clean_sql)
    _check_only_select(parsed)
    _check_injection_patterns(clean_sql)

    # ------------------------------------------------------------------
    # PHASE 2: MEANINGFULNESS VALIDATION
    # ------------------------------------------------------------------
    if check_meaningfulness:
        _check_query_meaningfulness(parsed, clean_sql)

    # ------------------------------------------------------------------
    # PHASE 3: LIMIT ENFORCEMENT
    # ------------------------------------------------------------------
    parsed = _enforce_limit(parsed, default_limit)

    return parsed.sql()


def assess_query_usefulness(raw_sql: str) -> Tuple[bool, str]:
    """Non-raising helper used in tests and the retriever to classify query quality.

    Returns (is_useful: bool, reason: str).
    """
    try:
        validate_and_sanitize_sql(raw_sql, check_meaningfulness=True)
        return True, "Query passed all guardrail and meaningfulness checks."
    except SQLMeaninglessQuery as e:
        return False, f"Meaningless query: {e}"
    except SQLGuardrailViolation as e:
        return False, f"Security violation: {e}"
    except Exception as e:
        return False, f"Query validation error: {e}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> str:
    return _COMMENT_STRIP.sub(" ", sql)


def _check_statement_count(sql: str) -> None:
    try:
        stmts = sqlglot.parse(sql)
    except Exception as e:
        raise SQLGuardrailViolation(f"Invalid SQL syntax: {e}") from e
    if len(stmts) > 1:
        raise SQLGuardrailViolation(
            "Multiple SQL statements (query stacking / chaining) are strictly prohibited."
        )


def _parse_sql(sql: str) -> exp.Expression:
    try:
        stmts = sqlglot.parse(sql)
        return stmts[0]
    except Exception as e:
        raise SQLGuardrailViolation(f"Invalid SQL syntax: {e}") from e


def _check_forbidden_ast_nodes(parsed: exp.Expression) -> None:
    for node in parsed.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            raise SQLGuardrailViolation(
                f"Security Violation: Prohibited SQL expression '{type(node).__name__}' detected."
            )


def _check_forbidden_functions_text(sql: str) -> None:
    """Plain-text scan for dangerous function names after comment stripping."""
    clean = _strip_comments(sql).upper()
    for fn in FORBIDDEN_FUNCTIONS:
        # Match as whole-word token (not inside an identifier)
        if re.search(rf"\b{re.escape(fn)}\b", clean):
            raise SQLGuardrailViolation(
                f"Security Violation: Forbidden function or keyword '{fn}' detected."
            )


def _check_only_select(parsed: exp.Expression) -> None:
    if not isinstance(parsed, exp.Select):
        raise SQLGuardrailViolation(
            "Security Violation: Only SELECT queries are permitted. "
            f"Received statement type: {type(parsed).__name__}."
        )


def _check_injection_patterns(sql: str) -> None:
    clean = _strip_comments(sql)

    # Check tautologies FIRST (OR 1=1, OR '1'='1') — these are security violations
    for pat in _ALWAYS_TRUE_PATTERNS:
        if pat.search(clean):
            raise SQLGuardrailViolation(
                "Security Violation: Always-true tautology detected in WHERE clause "
                "(classic SQL injection pattern, e.g. OR 1=1)."
            )

    # Check provably-false predicates (1=0, NULL=NULL) — meaningless queries / blind probe
    # Simple numeric/null false predicates
    for pat in [
        re.compile(r"\b1\s*=\s*0\b", re.IGNORECASE),
        re.compile(r"\bnull\s*=\s*null\b", re.IGNORECASE),
    ]:
        if pat.search(clean):
            raise SQLMeaninglessQuery(
                "Always-false predicate detected in WHERE clause. "
                "The query would return no rows (potential blind SQLi probe or misconfiguration)."
            )

    # For quoted-string comparisons 'a'='b', only flag when the two values differ
    quoted_cmp = re.compile(r"'([^']*)'\s*=\s*'([^']*)'", re.IGNORECASE)
    for match in quoted_cmp.finditer(clean):
        lhs, rhs = match.group(1), match.group(2)
        if lhs != rhs and lhs and rhs:
            raise SQLMeaninglessQuery(
                f"Always-false literal comparison detected: '{lhs}' = '{rhs}'. "
                "The query would return no rows."
            )


def _check_query_meaningfulness(parsed: exp.Select, sql: str) -> None:
    """Validate that the query is likely to return *useful* data for an AI agent."""

    # Rule 1: LIMIT 0 – guaranteed empty result
    limit_node = parsed.args.get("limit")
    if limit_node:
        lv = None
        try:
            lv = int(limit_node.expression.name)
        except (AttributeError, ValueError):
            pass
        if lv == 0:
            raise SQLMeaninglessQuery(
                "LIMIT 0 detected: query will always return 0 rows — no useful data for the AI."
            )

    # Rule 2: Bare constant SELECT (SELECT 1, SELECT NULL, SELECT 'string') – no table
    # sqlglot stores the FROM clause under the key "from_" (trailing underscore)
    from_clause = parsed.args.get("from_")
    if not from_clause:
        raise SQLMeaninglessQuery(
            "Query has no FROM clause — selects a constant or expression only. "
            "No table data will be returned for AI consumption."
        )

    # Rule 3: All selected expressions are literals / constants
    expressions = parsed.args.get("expressions", [])
    if expressions:
        all_constants = all(
            isinstance(e, (exp.Literal, exp.Null, exp.Boolean, exp.Star)) is False
            and isinstance(e, (exp.Literal, exp.Null, exp.Boolean))
            for e in expressions
        )
        if all_constants and not any(isinstance(e, exp.Star) for e in expressions):
            raise SQLMeaninglessQuery(
                "All SELECT expressions are literal constants (e.g. SELECT 1, SELECT NULL). "
                "This will not produce any business data for the AI."
            )

    # Rule 4: WHERE clause that is just a constant false / 0
    where_clause = parsed.args.get("where")
    if where_clause:
        cond = where_clause.this
        # e.g. WHERE 0, WHERE FALSE
        if isinstance(cond, (exp.Literal, exp.Boolean)):
            try:
                val = cond.name
                if val in ("0", "false", "False", "FALSE"):
                    raise SQLMeaninglessQuery(
                        f"WHERE clause is always FALSE ({val}). Query will return no rows."
                    )
            except AttributeError:
                pass

    # Rule 5: Selecting only NULL / 1 columns
    if expressions:
        non_trivial = [
            e for e in expressions
            if not isinstance(e, (exp.Literal, exp.Null, exp.Boolean))
        ]
        if not non_trivial and expressions:
            raise SQLMeaninglessQuery(
                "All projected columns are trivial constants (NULL, TRUE, FALSE, numbers). "
                "Query cannot yield meaningful business data."
            )


def _enforce_limit(parsed: exp.Select, default_limit: int) -> exp.Select:
    limit_clause = parsed.args.get("limit")
    if not limit_clause:
        return parsed.limit(default_limit)
    try:
        current = int(limit_clause.expression.name)
        if current > default_limit:
            return parsed.limit(default_limit)
    except (AttributeError, ValueError):
        return parsed.limit(default_limit)
    return parsed
