"""Part 2, SQL path: sqlglot-based static validation - the gate generated
SQL must survive before anything runs. Every check here is deterministic
and independent of anything the model reported about its own output
(confidence, columns_referenced, assumptions - none of it is trusted here).
A validation failure is NEVER repaired by re-prompting the model with the
error - that would turn this validator into a hint channel for getting
past itself (Part 2). The caller escalates instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DEFAULT_ROW_LIMIT = 10_000
DEFAULT_DIALECT = "postgres"

# Reject on sight, anywhere in the tree - defense in depth on top of the
# "must be exactly one bare SELECT" check below, in case a construct is
# reachable from inside a SELECT in some dialect this project doesn't
# anticipate (a nested CALL, a vendor extension, etc). None of these can
# legitimately appear inside a read-only SELECT.
_FORBIDDEN_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Grant,
    exp.Merge,
    exp.Command,
    exp.Use,
    exp.Set,
    exp.Pragma,
    exp.Attach,
    exp.Copy,
    exp.Cache,
)


@dataclass
class SQLValidationResult:
    valid: bool
    # The original SELECT, re-serialized with an enforced LIMIT - what
    # actually gets executed. None when valid=False; there is nothing safe
    # to run.
    limited_sql: str | None = None
    tables_referenced: list[str] = field(default_factory=list)
    columns_referenced: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_sql(
    sql: str,
    allowed_tables: set[str],
    allowed_columns: set[str],
    row_limit: int = DEFAULT_ROW_LIMIT,
    dialect: str = DEFAULT_DIALECT,
) -> SQLValidationResult:
    """allowed_tables/allowed_columns come from the registered source's own
    schema (app/query/pipeline.py derives them from the live DataContract) -
    never from anything the model claims to have referenced."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except ParseError as exc:
        return SQLValidationResult(valid=False, errors=[f"could not parse SQL: {exc}"])

    if len(statements) != 1:
        return SQLValidationResult(
            valid=False, errors=[f"exactly one SQL statement is permitted; found {len(statements)} (multi-statement bodies are rejected)"]
        )

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        return SQLValidationResult(valid=False, errors=[f"only a single SELECT statement is permitted; got '{tree.key}'"])

    errors: list[str] = []
    forbidden = tree.find_all(*_FORBIDDEN_NODE_TYPES)
    forbidden_keys = sorted({node.key for node in forbidden})
    if forbidden_keys:
        errors.append(f"forbidden construct(s) found in the query: {forbidden_keys}")

    # CTE aliases (WITH t AS (...)) are not real tables and must never be
    # checked against the registered schema - they're names the query
    # itself defines, not references to persisted data.
    cte_aliases = {cte.alias for cte in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)} - cte_aliases
    columns = {c.name for c in tree.find_all(exp.Column) if c.name}

    unknown_tables = tables - allowed_tables
    if unknown_tables:
        errors.append(f"references table(s) not in the registered source's schema: {sorted(unknown_tables)}")

    unknown_columns = columns - allowed_columns
    if unknown_columns:
        errors.append(f"references column(s) not in the live schema: {sorted(unknown_columns)}")

    if errors:
        return SQLValidationResult(valid=False, tables_referenced=sorted(tables), columns_referenced=sorted(columns), errors=errors)

    limited_tree = _apply_row_limit(tree, row_limit)
    return SQLValidationResult(
        valid=True,
        limited_sql=limited_tree.sql(dialect=dialect),
        tables_referenced=sorted(tables),
        columns_referenced=sorted(columns),
    )


def _apply_row_limit(tree: exp.Select, row_limit: int) -> exp.Select:
    """The smaller of (row_limit, any LIMIT the query already specifies) -
    a genuinely tighter caller-specified limit is respected; anything
    missing or larger is forced down to the cap. Always wraps: a query
    with no LIMIT at all is exactly the case this exists to catch."""
    effective = row_limit
    existing = tree.args.get("limit")
    if existing is not None:
        try:
            effective = min(int(existing.expression.this), row_limit)
        except (AttributeError, TypeError, ValueError):
            pass  # a non-literal LIMIT (bind parameter, expression) - fall through to the cap
    return tree.copy().limit(effective)
