from __future__ import annotations

import csv
from pathlib import Path

import chardet
import pandas as pd

from app.connectors.base import BaseConnector
from app.contract import DataContract, SourceType
from app.datetime_coercion import normalize_datetime_columns

EXCEL_EXTENSIONS = {".xlsx", ".xls"}
DEFAULT_ENCODING = "utf-8"
DEFAULT_DELIMITER = ","
SNIFF_SAMPLE_BYTES = 65536


class FileConnector(BaseConnector):
    """Reads a single local CSV or Excel file into a DataContract."""

    source_immutable = True  # re-fetching the same path returns the same bytes

    def __init__(self, source_id: str, file_path: str, sheet: str | int | None = None):
        self.source_id = source_id
        self.file_path = file_path
        self.sheet = sheet

    def fetch(self) -> DataContract:
        path = Path(self.file_path)
        extension = path.suffix.lower()
        detected_encoding: str | None = None
        encoding_confidence: float | None = None

        if extension == ".csv":
            df, connector_metadata, detected_encoding, encoding_confidence = self._read_csv(path)
        elif extension in EXCEL_EXTENSIONS:
            df, connector_metadata = self._read_excel(path)
        else:
            raise ValueError(f"Unsupported file extension: '{extension}'")

        # Neither format carries declared type metadata pandas actually uses
        # (CSV has none; Excel's cell formats are ignored by read_excel, per
        # the Phase 2 README notes) - only the parse-rate path ever applies
        # here, never the declared-schema one.
        normalization = normalize_datetime_columns(df)
        df = normalization.data
        if normalization.coercions:
            connector_metadata["datetime_coercions"] = normalization.coercions
        if normalization.parse_attempts:
            connector_metadata["datetime_parse_attempts"] = normalization.parse_attempts

        return DataContract(
            data=df,
            source_type=SourceType.FILE,
            source_id=self.source_id,
            connector_metadata=connector_metadata,
            detected_encoding=detected_encoding,
            encoding_confidence=encoding_confidence,
        )

    def _read_csv(self, path: Path) -> tuple[pd.DataFrame, dict, str, float]:
        detected_encoding, confidence = self._detect_encoding(path)
        encoding, sample = self._resolve_encoding(path, detected_encoding)
        delimiter = self._detect_delimiter(sample)
        df = pd.read_csv(path, encoding=encoding, delimiter=delimiter)
        metadata = {"encoding_used": encoding}
        return df, metadata, detected_encoding, confidence

    def _read_excel(self, path: Path) -> tuple[pd.DataFrame, dict]:
        with pd.ExcelFile(path) as excel_file:
            sheet_names = excel_file.sheet_names
            warnings: list[str] = []
            if self.sheet is not None:
                sheet = self.sheet
            else:
                sheet = sheet_names[0]
                if len(sheet_names) > 1:
                    warnings.append(
                        f"'{path.name}' has {len(sheet_names)} sheets {sheet_names}; "
                        f"read first sheet '{sheet}' only. Set connection_config.sheet to "
                        "pick a different one and silence this warning."
                    )
            df = pd.read_excel(excel_file, sheet_name=sheet)
        metadata = {"excel_sheet_count": len(sheet_names), "excel_sheet_used": sheet}
        if warnings:
            metadata["warnings"] = warnings
        return df, metadata

    @staticmethod
    def _detect_encoding(path: Path) -> tuple[str, float]:
        with open(path, "rb") as f:
            raw = f.read(SNIFF_SAMPLE_BYTES)
        result = chardet.detect(raw)
        return result.get("encoding") or DEFAULT_ENCODING, result.get("confidence") or 0.0

    @staticmethod
    def _resolve_encoding(path: Path, detected_encoding: str) -> tuple[str, str]:
        # chardet's guess can be wrong on short/ambiguous samples, so we verify it
        # actually decodes and fall back through safe defaults, ending in latin-1
        # (which never raises, since every byte maps to a valid code point).
        candidates = [c for c in (detected_encoding, DEFAULT_ENCODING, "latin-1") if c]
        last_error: Exception | None = None
        for encoding in dict.fromkeys(candidates):
            try:
                with open(path, "r", encoding=encoding, newline="") as f:
                    sample = f.read(SNIFF_SAMPLE_BYTES)
                return encoding, sample
            except (UnicodeDecodeError, LookupError) as exc:
                last_error = exc
        raise ValueError(f"Unable to decode CSV file '{path}': {last_error}")

    @staticmethod
    def _detect_delimiter(sample: str) -> str:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return DEFAULT_DELIMITER
