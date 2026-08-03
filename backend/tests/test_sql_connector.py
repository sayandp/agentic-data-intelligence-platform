import pytest
from sqlalchemy import create_engine, text

from app.connectors.credentials import MissingCredentialError
from app.connectors.sql_connector import SQLConnector
from app.contract import SourceType


@pytest.fixture
def sqlite_orders(monkeypatch, tmp_path):
    db_path = tmp_path / "orders.db"
    env_var = "TEST_SQL_CONNECTOR_DB_URL"
    monkeypatch.setenv(env_var, f"sqlite:///{db_path.as_posix()}")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER, price NUMERIC(10,2), order_date DATE, notes TEXT)"))
        for i in range(20):
            conn.execute(
                text("INSERT INTO orders VALUES (:id, :price, :d, :n)"),
                {"id": i, "price": 10.0 + i, "d": "2024-01-01", "n": f"note-{i}"},
            )
        conn.commit()
    engine.dispose()
    return env_var


def test_fetch_reads_full_table(sqlite_orders):
    contract = SQLConnector(source_id="s1", connection_string_env=sqlite_orders, query="select * from orders").fetch()
    assert contract.source_type == SourceType.SQL
    assert contract.row_count == 20
    assert list(contract.data.columns) == ["id", "price", "order_date", "notes"]


def test_row_limit_applied_via_subquery_not_string_concat(sqlite_orders):
    contract = SQLConnector(
        source_id="s1", connection_string_env=sqlite_orders, query="select * from orders", row_limit=5
    ).fetch()
    assert contract.row_count == 5


def test_row_limit_cannot_inject_via_query_string(sqlite_orders):
    """row_limit is coerced through int() before ever reaching the query
    string - a crafted non-numeric value must fail loudly, not get
    concatenated into SQL."""
    connector = SQLConnector(
        source_id="s1", connection_string_env=sqlite_orders, query="select * from orders", row_limit="5; DROP TABLE orders;--"
    )
    with pytest.raises(ValueError):
        connector.fetch()

    # the table must still exist and be intact
    still_there = SQLConnector(
        source_id="s1", connection_string_env=sqlite_orders, query="select * from orders"
    ).fetch()
    assert still_there.row_count == 20


def test_missing_env_var_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("NOT_SET_DB_URL", raising=False)
    connector = SQLConnector(source_id="s1", connection_string_env="NOT_SET_DB_URL", query="select 1")
    with pytest.raises(MissingCredentialError) as exc_info:
        connector.fetch()
    assert "NOT_SET_DB_URL" in str(exc_info.value)


def test_declared_date_column_is_coerced_not_merely_flagged(sqlite_orders):
    """Phase 7.5 Part 2: SQL has authoritative declared types, unlike CSV
    (inferred) or Excel (metadata present but ignored by pandas). A declared
    DATE/TIMESTAMP is used without inference - coerced outright, not just
    surfaced as a warning for a human to notice and never act on."""
    import pandas as pd

    contract = SQLConnector(source_id="s1", connection_string_env=sqlite_orders, query="select * from orders").fetch()
    metadata = contract.metadata()

    assert metadata["declared_schema"]["order_date"] == "DATE"
    assert metadata["declared_schema"]["id"] == "INTEGER"

    assert pd.api.types.is_datetime64_any_dtype(contract.data["order_date"])
    coerced_columns = {c["column"] for c in metadata["datetime_coercions"]}
    assert "order_date" in coerced_columns
    coercion = next(c for c in metadata["datetime_coercions"] if c["column"] == "order_date")
    assert coercion["method"] == "declared_schema"
    assert coercion["parse_success_rate"] == 1.0

    # Resolved by coercion, not a dangling warning - see
    # app/connectors/sql_connector.py's fetch(): the dtype-mismatch
    # comparison runs AFTER coercion, so a temporal declared-vs-actual
    # disagreement never reaches dtype_mismatches/warnings at all.
    assert "order_date" not in {m["column"] for m in metadata.get("dtype_mismatches", [])}
    assert not any("order_date" in w for w in metadata.get("warnings", []))
    assert "id" not in {m["column"] for m in metadata.get("dtype_mismatches", [])}  # INTEGER declared, int64 actual - compatible, not flagged


def test_compatible_declared_and_actual_types_are_not_flagged(sqlite_orders):
    contract = SQLConnector(source_id="s1", connection_string_env=sqlite_orders, query="select * from orders").fetch()
    mismatch_columns = {m["column"] for m in contract.metadata().get("dtype_mismatches", [])}
    assert "price" not in mismatch_columns  # NUMERIC declared, numeric actual dtype either way


def test_declared_schema_skipped_for_multi_table_query(sqlite_orders, monkeypatch):
    """A query the connector can't safely attribute to one table (a join)
    just skips the declared-schema comparison rather than guessing."""
    env_var = sqlite_orders
    import os

    engine = create_engine(os.environ[env_var])
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE customers (id INTEGER, name TEXT)"))
        conn.commit()
    engine.dispose()

    contract = SQLConnector(
        source_id="s1",
        connection_string_env=env_var,
        query="select orders.id from orders JOIN customers ON orders.id = customers.id",
    ).fetch()
    assert "declared_schema" not in contract.metadata() or contract.metadata().get("declared_schema") == {}
    assert "dtype_mismatches" not in contract.metadata()
