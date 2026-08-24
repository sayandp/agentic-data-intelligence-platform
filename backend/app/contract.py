"""The canonical data contract.

This is the only interface between connectors (SQL/file/API) and every
downstream agent. Downstream code reads a DataContract and must never need
to know what kind of source produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd

from app.security.config import MAX_INGEST_COLUMNS, MAX_INGEST_ROWS


class FrameTooLargeError(ValueError):
    """Raised when a frame exceeds a configured ingest limit.

    Its own type, not a bare ValueError, so the ingest path can turn it
    into a clear refusal instead of a stack trace that reads like a
    parsing bug."""


class SourceType(str, Enum):
    SQL = "sql"
    FILE = "file"
    API = "api"


@dataclass
class DataContract:
    data: pd.DataFrame
    source_type: SourceType
    source_id: str
    ingestion_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connector_metadata: dict[str, Any] = field(default_factory=dict)
    detected_encoding: str | None = None
    encoding_confidence: float | None = None

    def __post_init__(self) -> None:
        """The size limits, enforced where a frame ENTERS the system.

        Here rather than in each connector, because this is the one type every
        connector must produce and every agent must consume: a frame that
        exceeds the limits cannot be represented, rather than being caught by
        three checks that must each be remembered.

        It REFUSES; it never truncates. Analysing the first two million rows
        of a larger file and reporting the result as the dataset's profile
        would be a silent fallback producing confidently wrong output - a
        baseline, a null rate and a Pareto band computed over a fraction of
        the data, with nothing in the report saying so.
        """
        rows = len(self.data)
        if rows > MAX_INGEST_ROWS:
            raise FrameTooLargeError(
                f"this source has {rows:,} rows, above the {MAX_INGEST_ROWS:,}-row ingest limit. "
                "Nothing was analysed: a partial read would produce a profile of part of the data "
                "and report it as the whole. Raise MAX_INGEST_ROWS to accept it, or narrow the "
                "source (a SQL row_limit, a smaller export)."
            )
        columns = len(self.data.columns)
        if columns > MAX_INGEST_COLUMNS:
            raise FrameTooLargeError(
                f"this source has {columns:,} columns, above the {MAX_INGEST_COLUMNS:,}-column ingest limit. "
                "A frame this wide is usually a parse gone wrong - a mis-detected delimiter, or a header "
                "row that is really data. Check the file, or raise MAX_INGEST_COLUMNS."
            )

    @property
    def row_count(self) -> int:
        return len(self.data)

    @property
    def column_types(self) -> dict[str, str]:
        return {column: str(dtype) for column, dtype in self.data.dtypes.items()}

    def metadata(self) -> dict[str, Any]:
        """Everything about this contract except the DataFrame itself, JSON-serializable."""
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "ingestion_timestamp": self.ingestion_timestamp.isoformat(),
            "row_count": self.row_count,
            "column_types": self.column_types,
            "detected_encoding": self.detected_encoding,
            "encoding_confidence": self.encoding_confidence,
            **self.connector_metadata,
        }
