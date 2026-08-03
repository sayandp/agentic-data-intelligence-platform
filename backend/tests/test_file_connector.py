import pytest

from app.connectors.file_connector import FileConnector
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
