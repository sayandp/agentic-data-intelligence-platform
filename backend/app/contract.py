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
