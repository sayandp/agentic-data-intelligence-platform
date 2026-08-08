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

#: Names chardet uses for plain 7-bit ASCII.
_ASCII_ALIASES = {"ascii", "us-ascii"}


def _normalize_encoding_name(name: str | None) -> str | None:
    return name.lower().replace("_", "-") if name else None


def encodings_are_equivalent(detected: str | None, used: str | None) -> bool:
    """True when reading as `used` instead of `detected` changed nothing.

    Only ASCII -> UTF-8 qualifies, and only in that direction. UTF-8 is a
    strict superset of ASCII: every ASCII byte decodes to the same code
    point under both, so widening is not a fallback and must not be reported
    as one.

    This exists so app/validation/engine.py's ENCODING_FALLBACK_OVERRULED
    rule - which treats a substituted encoding as a strong corruption
    indicator - does not fire on a substitution that, by construction,
    cannot corrupt anything. Falling back to latin-1 is a genuine overrule
    and stays flagged: it means bytes appeared that UTF-8 could not decode.
    """
    detected_name = _normalize_encoding_name(detected)
    used_name = _normalize_encoding_name(used)
    if detected_name is None or used_name is None:
        return False
    if detected_name == used_name:
        return True
    return detected_name in _ASCII_ALIASES and used_name == DEFAULT_ENCODING


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
        df, encoding_used, fallback_warning = self._read_with_encoding_fallback(path, encoding, delimiter)
        metadata = {"encoding_used": encoding_used}
        if fallback_warning:
            metadata["warnings"] = [fallback_warning]
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
    def _encoding_candidates(detected_encoding: str | None) -> list[str]:  # noqa: D401 - see encodings_are_equivalent
        """Ordered encodings to try, best guess first, ending in one that
        cannot raise.

        chardet answering "ascii" only ever means THE SAMPLE was ASCII - it
        is a statement about the first SNIFF_SAMPLE_BYTES, not about the
        file. A 94MB export whose first 64KB happens to be plain ASCII and
        whose non-ASCII bytes start megabytes later (a `£` inside a product
        description, at byte 2,784,238 in the case that surfaced this) was
        confidently reported as ascii, and every row after that point was
        undecodable.

        Promoting ascii to utf-8 is free: UTF-8 is a strict superset, so any
        byte sequence that decodes as ASCII decodes identically as UTF-8,
        and UTF-8 additionally handles the bytes the sample never saw.
        """
        if _normalize_encoding_name(detected_encoding) in _ASCII_ALIASES:
            detected_encoding = DEFAULT_ENCODING
        return list(dict.fromkeys([c for c in (detected_encoding, DEFAULT_ENCODING, "latin-1") if c]))

    @classmethod
    def _resolve_encoding(cls, path: Path, detected_encoding: str) -> tuple[str, str]:
        # chardet's guess can be wrong on short/ambiguous samples, so we verify it
        # actually decodes and fall back through safe defaults, ending in latin-1
        # (which never raises, since every byte maps to a valid code point).
        #
        # NOTE this only proves the SAMPLE decodes - the full file is what
        # actually has to parse, and _read_with_encoding_fallback is what
        # guarantees that. This picks a starting point and a sample to sniff
        # the delimiter from; it is not the safety net.
        last_error: Exception | None = None
        for encoding in cls._encoding_candidates(detected_encoding):
            try:
                with open(path, "r", encoding=encoding, newline="") as f:
                    sample = f.read(SNIFF_SAMPLE_BYTES)
                return encoding, sample
            except (UnicodeDecodeError, LookupError) as exc:
                last_error = exc
        raise ValueError(f"Unable to decode CSV file '{path}': {last_error}")

    @classmethod
    def _read_with_encoding_fallback(cls, path: Path, encoding: str, delimiter: str) -> tuple[pd.DataFrame, str, str | None]:
        """Parse the whole file, degrading through the same candidate chain.

        The fallback has to live HERE, around the real read. Verifying a
        64KB sample and then parsing 94MB is not a check - it is a check of
        a different, smaller file that happens to share a prefix. A
        mis-sniffed encoding used to surface as a raw UnicodeDecodeError
        from pandas, which the graph turned into a bare "failed" run with
        the reason buried in an AgentTrace.

        latin-1 terminates the chain and cannot raise: every byte 0x00-0xFF
        maps to a code point. Reaching it means the text may be mojibake, so
        it is reported as a warning rather than passed off as a clean read.
        """
        candidates = cls._encoding_candidates(encoding)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                df = pd.read_csv(path, encoding=candidate, delimiter=delimiter)
            except (UnicodeDecodeError, LookupError) as exc:
                last_error = exc
                continue
            if candidate == candidates[0]:
                return df, candidate, None
            return df, candidate, (
                f"'{path.name}' did not decode as {candidates[0]} beyond the {SNIFF_SAMPLE_BYTES}-byte "
                f"sample used to detect it; read as {candidate} instead. Characters outside {candidate} "
                "may be misrepresented."
            )
        raise ValueError(f"Unable to decode CSV file '{path}': {last_error}")

    @staticmethod
    def _detect_delimiter(sample: str) -> str:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return DEFAULT_DELIMITER
