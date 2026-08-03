from datetime import datetime, timezone

import pandas as pd

from app.contract import DataContract, SourceType


def test_metadata_correctness():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    contract = DataContract(
        data=df,
        source_type=SourceType.FILE,
        source_id="src-1",
        ingestion_timestamp=ts,
    )

    expected_column_types = {col: str(dtype) for col, dtype in df.dtypes.items()}

    assert contract.row_count == 3
    assert contract.column_types == expected_column_types

    meta = contract.metadata()

    assert meta == {
        "source_type": "file",
        "source_id": "src-1",
        "ingestion_timestamp": ts.isoformat(),
        "row_count": 3,
        "column_types": expected_column_types,
        "detected_encoding": None,
        "encoding_confidence": None,
    }
    assert "data" not in meta


def test_metadata_default_timestamp_is_set():
    df = pd.DataFrame({"a": [1]})
    contract = DataContract(data=df, source_type=SourceType.SQL, source_id="src-2")

    assert contract.ingestion_timestamp is not None
    assert contract.metadata()["source_type"] == "sql"
