import pytest

from app.connectors.file_connector import SNIFF_SAMPLE_BYTES, FileConnector, encodings_are_equivalent
from app.contract import SourceType


def test_csv_comma_default(tmp_path):
    path = tmp_path / "comma.csv"
    path.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

    contract = FileConnector(source_id="src-1", file_path=str(path)).fetch()

    assert contract.source_type == SourceType.FILE
    assert contract.row_count == 2
    assert list(contract.data.columns) == ["a", "b", "c"]


def test_csv_semicolon_delimiter_detected(tmp_path):
    path = tmp_path / "semicolon.csv"
    path.write_text(
        "name;age;city\nAlice;30;NYC\nBob;25;LA\nCarol;41;SF\n", encoding="utf-8"
    )

    contract = FileConnector(source_id="src-2", file_path=str(path)).fetch()

    assert list(contract.data.columns) == ["name", "age", "city"]
    assert contract.row_count == 3


def test_csv_latin1_encoding_detected(tmp_path):
    path = tmp_path / "latin1.csv"
    content = (
        "name,city\n"
        "Jos\xe9,S\xe3o Paulo\n"
        "Andr\xe9,Bras\xedlia\n"
        "In\xeas,Cuiab\xe1\n"
    )
    path.write_bytes(content.encode("latin-1"))

    contract = FileConnector(source_id="src-3", file_path=str(path)).fetch()

    assert contract.row_count == 3
    assert contract.data.iloc[0]["name"] == "José"
    assert contract.data.iloc[1]["city"] == "Brasília"


def test_excel_file_read(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    path = tmp_path / "data.xlsx"
    pd.DataFrame({"x": [1, 2], "y": ["a", "b"]}).to_excel(path, index=False)

    contract = FileConnector(source_id="src-4", file_path=str(path)).fetch()

    assert contract.row_count == 2
    assert list(contract.data.columns) == ["x", "y"]
    assert contract.metadata()["excel_sheet_count"] == 1


def test_excel_multi_sheet_without_sheet_reads_first_and_warns(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [2]}).to_excel(writer, sheet_name="second", index=False)

    contract = FileConnector(source_id="src-6", file_path=str(path)).fetch()
    metadata = contract.metadata()

    assert list(contract.data.columns) == ["x"]
    assert metadata["excel_sheet_count"] == 2
    assert metadata["excel_sheet_used"] == "first"
    assert len(metadata["warnings"]) == 1
    assert "2 sheets" in metadata["warnings"][0]


def test_excel_multi_sheet_with_sheet_selected(tmp_path):
    pytest.importorskip("openpyxl")
    import pandas as pd

    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"y": [2, 3]}).to_excel(writer, sheet_name="second", index=False)

    contract = FileConnector(source_id="src-7", file_path=str(path), sheet="second").fetch()

    assert list(contract.data.columns) == ["y"]
    assert contract.row_count == 2
    assert contract.metadata()["excel_sheet_used"] == "second"
    assert "warnings" not in contract.metadata()


def test_csv_metadata_includes_encoding_and_confidence(tmp_path):
    path = tmp_path / "comma.csv"
    path.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

    contract = FileConnector(source_id="src-8", file_path=str(path)).fetch()
    metadata = contract.metadata()

    assert metadata["encoding_used"] in ("utf-8", "ascii")
    assert metadata["detected_encoding"] is not None
    assert isinstance(metadata["encoding_confidence"], float)


def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError):
        FileConnector(source_id="src-5", file_path=str(path)).fetch()


# ---- Encoding detected from a SAMPLE, applied to the WHOLE file ----


def _ascii_prefixed_utf8_csv(path, non_ascii_text: str = "Gift Voucher £80.00") -> int:
    """A CSV whose first SNIFF_SAMPLE_BYTES are pure ASCII and whose first
    non-ASCII character appears well past that window.

    This is the shape of every large real-world export: an ASCII header and
    thousands of ASCII rows, with a currency symbol or accented product name
    appearing much later. It is also the shape that defeats sniffing.
    """
    rows = ["id,description"]
    filler = "x" * 60
    # Comfortably past the 65536-byte sniff window before anything non-ASCII.
    while sum(len(r) + 1 for r in rows) < SNIFF_SAMPLE_BYTES * 2:
        rows.append(f"{len(rows)},{filler}")
    marker_row = len(rows) - 1
    rows.append(f"{len(rows)},{non_ascii_text}")
    path.write_bytes(("\n".join(rows) + "\n").encode("utf-8"))
    return marker_row


def test_a_non_ascii_byte_past_the_sniff_window_still_reads(tmp_path):
    """The bug this pins: chardet saw only the first 65536 bytes, reported
    `ascii` with confidence 1.0, and the full read then died on a `£`
    megabytes later - failing the whole ingest with a bare UnicodeDecodeError.

    An `ascii` verdict is a statement about the sample, never about the file.
    """
    path = tmp_path / "ascii_prefix.csv"
    _ascii_prefixed_utf8_csv(path)

    contract = FileConnector(source_id="src-sniff", file_path=str(path)).fetch()

    # Detection still honestly reports what it saw...
    assert contract.detected_encoding == "ascii"
    # ...but the file was read as UTF-8, of which ASCII is a strict subset,
    # so nothing is lost and the later bytes decode.
    assert contract.connector_metadata["encoding_used"] == "utf-8"
    assert contract.data.iloc[-1]["description"] == "Gift Voucher £80.00"
    # Genuinely the pound sign, not a replacement character.
    assert "�" not in contract.data.iloc[-1]["description"]


def test_an_encoding_that_fails_only_late_falls_back_and_says_so(tmp_path):
    """Even with the ascii->utf-8 promotion, a file can decode cleanly for
    65536 bytes and then not. The fallback has to guard the REAL read, not a
    sample of it - and reaching latin-1 means the text may be mojibake, so
    it is reported rather than passed off as a clean read."""
    path = tmp_path / "late_cp1252.csv"
    rows = ["id,description"]
    filler = "x" * 60
    while sum(len(r) + 1 for r in rows) < SNIFF_SAMPLE_BYTES * 2:
        rows.append(f"{len(rows)},{filler}")
    # 0x92 is a valid cp1252/latin-1 byte and an INVALID utf-8 start byte.
    blob = ("\n".join(rows) + "\n").encode("ascii") + b"99999,Bob\x92s Widgets\n"
    path.write_bytes(blob)

    contract = FileConnector(source_id="src-late", file_path=str(path)).fetch()

    assert contract.connector_metadata["encoding_used"] == "latin-1"
    warnings = contract.connector_metadata.get("warnings", [])
    assert warnings and "did not decode as utf-8" in warnings[0]
    assert "may be misrepresented" in warnings[0]
    assert len(contract.data) == len(rows)


def test_a_clean_utf8_read_reports_no_fallback_warning(tmp_path):
    """The warning must mean something - it cannot appear on a normal read."""
    path = tmp_path / "clean.csv"
    path.write_text("name,city\nJosé,São Paulo\n", encoding="utf-8")

    contract = FileConnector(source_id="src-clean", file_path=str(path)).fetch()

    assert contract.connector_metadata["encoding_used"] == "utf-8"
    assert "warnings" not in contract.connector_metadata


def test_widening_ascii_to_utf8_is_not_reported_as_a_fallback():
    """The connector widens an `ascii` detection to utf-8 because chardet
    only saw the sample. That is not a substitution and must not trip the
    corruption rule - a genuine latin-1 last resort still must."""
    assert encodings_are_equivalent("ascii", "utf-8")
    assert encodings_are_equivalent("US-ASCII", "utf-8")
    assert encodings_are_equivalent("utf-8", "utf-8")
    # Narrow on purpose: only ASCII -> UTF-8, only in that direction.
    assert not encodings_are_equivalent("utf-8", "latin-1")
    assert not encodings_are_equivalent("ascii", "latin-1")
    assert not encodings_are_equivalent("utf-8", "ascii")
    assert not encodings_are_equivalent(None, "utf-8")
