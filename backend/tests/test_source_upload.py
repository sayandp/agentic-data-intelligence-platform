"""POST /sources/upload - the dashboard's Upload button. Registers a `file`
source from bytes the browser sends directly, instead of a path the app
process already had filesystem access to. Reuses FileConnector completely
unchanged: the uploaded bytes are saved to disk once, and the resulting
source is indistinguishable from one registered by hand-typing a
connection_config.path - proven here by actually ingesting it end to end,
not just checking the source row got created.
"""

from __future__ import annotations

import io

import pandas as pd

from app.routers import sources as sources_router
from tests.golden_scenarios import ingest_and_wait


def _csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _clean_df(n: int = 60) -> pd.DataFrame:
    return pd.DataFrame({"id": range(n), "city": ["a", "b"] * (n // 2), "amount": [float(i) for i in range(n)]})


def test_upload_registers_a_file_source_and_it_ingests_successfully(client):
    resp = client.post(
        "/sources/upload",
        files={"file": ("orders.csv", _csv_bytes(_clean_df()), "text/csv")},
    )
    assert resp.status_code == 200
    source_id = resp.json()["id"]

    source = client.get(f"/sources/{source_id}").json()
    assert source["type"] == "file"
    assert source["connection_config"]["original_filename"] == "orders.csv"
    assert source["connection_config"]["path"]  # a real, resolvable path was assigned

    ingest = ingest_and_wait(client, source_id)
    assert ingest["status"] == "completed"


def test_upload_shows_up_in_sources_list_with_original_filename(client):
    client.post("/sources/upload", files={"file": ("my_data.csv", _csv_bytes(_clean_df()), "text/csv")})
    listed = client.get("/sources").json()
    assert any(s["connection_config"].get("original_filename") == "my_data.csv" for s in listed)


def test_upload_rejects_unsupported_extension(client):
    resp = client.post(
        "/sources/upload",
        files={"file": ("not_data.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "unsupported file extension" in resp.json()["detail"]


def test_upload_rejects_empty_file(client):
    resp = client.post(
        "/sources/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]


def test_upload_ignores_directory_components_in_a_hostile_filename(client, tmp_path):
    resp = client.post(
        "/sources/upload",
        files={"file": ("../../evil.csv", _csv_bytes(_clean_df()), "text/csv")},
    )
    assert resp.status_code == 200
    source = client.get(f"/sources/{resp.json()['id']}").json()
    saved_path = source["connection_config"]["path"]
    # The saved path stays inside the configured upload directory - the
    # hostile "../../" never gets a chance to act as a path component,
    # because only Path(filename).name (the basename) is ever read.
    upload_dir = sources_router._upload_dir()
    assert upload_dir.name in saved_path.replace("\\", "/").split("/")
    assert ".." not in saved_path


def test_upload_rejects_a_file_over_the_configured_size_limit(client, monkeypatch):
    monkeypatch.setattr(sources_router, "MAX_UPLOAD_BYTES", 10)
    resp = client.post(
        "/sources/upload",
        files={"file": ("big.csv", b"x" * 1000, "text/csv")},
    )
    assert resp.status_code == 413
