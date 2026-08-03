"""SQLAlchemy-based connector for PostgreSQL, MySQL, and MSSQL.

Dialect support comes entirely from the connection string prefix
(postgresql+psycopg2://, mysql+pymysql://, mssql+pyodbc://, ...) and
SQLAlchemy's own dialect plugins - this module contains no dialect-specific
branching. The driver package itself (psycopg2, pymysql, pyodbc, ...) is the
user's dependency to install for whichever dialect they connect to; tests in
this repo use sqlite (bundled with Python) as a stand-in, since SQLAlchemy's
inspector and pd.read_sql_query behave the same way regardless of dialect.

AUTHORITATIVE DTYPES: SQL is the only source in this platform with a real,
queryable declared schema (CSV has none - type is inferred; Excel has
cell-level type metadata but pandas.read_excel ignores it - see the Phase 2
README notes). That's surfaced here as connector_metadata["declared_schema"],
compared against the dtypes pandas actually produced. Disagreements are
recorded as a warning, not corrected - see _dtype_mismatches below.
"""

from __future__ import annotations

import re

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text

from app.connectors.base import BaseConnector
from app.connectors.credentials import resolve_env_var
from app.contract import DataContract, SourceType
from app.datetime_coercion import normalize_datetime_columns

_SINGLE_TABLE_QUERY_RE = re.compile(r"\bFROM\s+\"?([A-Za-z_][\w.]*)\"?", re.IGNORECASE)

# Coarse type families used to compare a declared SQL type against the dtype
# pandas actually produced. Deliberately coarse: NUMERIC(10,2) vs float64 is
# "numeric" on both sides and should never be flagged just for not matching
# character-for-character.
_NUMERIC_MARKERS = ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "SERIAL")
_TEMPORAL_MARKERS = ("DATE", "TIME")
_BOOLEAN_MARKERS = ("BOOL",)
_TEXT_MARKERS = ("CHAR", "TEXT", "CLOB", "STR", "OBJECT")


class SQLConnectorConfig(BaseModel):
    connection_string_env: str
    query: str
    row_limit: int | None = None
    # Optional explicit table name for declared-schema lookup, for queries
    # (joins, CTEs, subqueries) _extract_single_table can't safely infer one
    # from. Leave unset for a simple "select ... from <table>" query.
    table: str | None = None


class SQLConnector(BaseConnector):
    source_immutable = False  # the table can change between fetches

    def __init__(
        self,
        source_id: str,
        connection_string_env: str,
        query: str,
        row_limit: int | None = None,
        table: str | None = None,
    ):
        self.source_id = source_id
        self.connection_string_env = connection_string_env
        self.query = query
        self.row_limit = row_limit
        self.table = table

    def fetch(self) -> DataContract:
        connection_string = resolve_env_var(self.connection_string_env)
        engine = create_engine(connection_string)
        try:
            df = self._run_query(engine)
            declared_schema = self._declared_schema(engine)
        finally:
            engine.dispose()

        connector_metadata: dict = {}
        if declared_schema:
            connector_metadata["declared_schema"] = declared_schema

        # A declared DATE/TIMESTAMP column is coerced HERE, authoritatively,
        # before the dtype-mismatch comparison below ever runs - so a
        # declared-temporal-vs-actual-text disagreement becomes a resolved
        # coercion, not a dangling warning. Any mismatch that survives past
        # this point is a genuine, still-unresolved disagreement (numeric,
        # boolean, or text category) and is reported as before.
        normalization = normalize_datetime_columns(df, declared_schema=declared_schema)
        df = normalization.data
        if normalization.coercions:
            connector_metadata["datetime_coercions"] = normalization.coercions
        if normalization.parse_attempts:
            connector_metadata["datetime_parse_attempts"] = normalization.parse_attempts

        if declared_schema:
            actual_dtypes = {c: str(dt) for c, dt in df.dtypes.items()}
            mismatches = _dtype_mismatches(declared_schema, actual_dtypes)
            if mismatches:
                connector_metadata["dtype_mismatches"] = mismatches
                connector_metadata["warnings"] = [
                    f"column '{m['column']}': declared SQL type {m['declared_type']!r} does not match "
                    f"pandas-inferred dtype {m['actual_dtype']!r} - dtype confidence is a property of "
                    "the source format, not the data"
                    for m in mismatches
                ]

        return DataContract(
            data=df, source_type=SourceType.SQL, source_id=self.source_id, connector_metadata=connector_metadata
        )

    def _run_query(self, engine) -> pd.DataFrame:
        query = self.query
        if self.row_limit is not None:
            # Wrapped as a subquery rather than string-concatenating a LIMIT
            # clause onto the caller's SQL - the caller's query may already
            # have its own LIMIT/ORDER BY, may not be a bare SELECT, or could
            # otherwise not tolerate a clause appended to its tail. The row
            # limit itself is coerced through int() so a crafted non-numeric
            # value can never reach the query string at all.
            limit = int(self.row_limit)
            query = f"SELECT * FROM ({self.query}) AS _row_limit_subquery LIMIT {limit}"

        with engine.connect() as conn:
            return pd.read_sql_query(text(query), conn)

    def _declared_schema(self, engine) -> dict[str, str]:
        table_name = self.table or _extract_single_table(self.query)
        if not table_name:
            return {}
        try:
            columns = inspect(engine).get_columns(table_name)
        except Exception:  # noqa: BLE001 - reflection failure just means no declared-schema comparison
            return {}
        return {col["name"]: str(col["type"]) for col in columns}


def _extract_single_table(query: str) -> str | None:
    """Best-effort single-table extraction from a simple `SELECT ... FROM
    <table>` query - not a SQL parser. Only returns a table name when the
    query has exactly one FROM and no JOIN, since anything more complex
    can't be safely attributed to one table's declared schema. Returns None
    otherwise; declared-schema comparison is then skipped rather than guessed."""
    if len(_SINGLE_TABLE_QUERY_RE.findall(query)) != 1 or re.search(r"\bJOIN\b", query, re.IGNORECASE):
        return None
    match = _SINGLE_TABLE_QUERY_RE.search(query)
    return match.group(1) if match else None


def _type_category(type_str: str) -> str:
    upper = type_str.upper()
    if any(marker in upper for marker in _NUMERIC_MARKERS):
        return "numeric"
    if any(marker in upper for marker in _TEMPORAL_MARKERS):
        return "temporal"
    if any(marker in upper for marker in _BOOLEAN_MARKERS):
        return "boolean"
    if any(marker in upper for marker in _TEXT_MARKERS):
        return "text"
    return "other"


def _dtype_mismatches(declared_schema: dict[str, str], actual_dtypes: dict[str, str]) -> list[dict]:
    mismatches = []
    for column, declared_type in declared_schema.items():
        if column not in actual_dtypes:
            continue
        actual_dtype = actual_dtypes[column]
        declared_category = _type_category(declared_type)
        actual_category = _type_category(actual_dtype)
        if declared_category == "other" or actual_category == "other":
            continue  # can't confidently judge either side; don't false-positive
        if declared_category != actual_category:
            mismatches.append({"column": column, "declared_type": declared_type, "actual_dtype": actual_dtype})
    return mismatches
