import pytest

from app.connectors.credentials import MissingCredentialError, resolve_env_var


def test_resolve_env_var_reads_value(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "topsecretvalue")
    assert resolve_env_var("SOME_SECRET") == "topsecretvalue"


def test_resolve_env_var_missing_name_raises():
    with pytest.raises(ValueError):
        resolve_env_var(None)
    with pytest.raises(ValueError):
        resolve_env_var("")


def test_resolve_env_var_unset_variable_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST_VAR", raising=False)
    with pytest.raises(MissingCredentialError) as exc_info:
        resolve_env_var("DOES_NOT_EXIST_VAR")
    assert "DOES_NOT_EXIST_VAR" in str(exc_info.value)
    assert exc_info.value.env_var_name == "DOES_NOT_EXIST_VAR"


def test_no_secret_persisted_or_returned_by_get_sources(client, monkeypatch):
    """The structural guarantee: connection_config only ever stores an env
    var NAME. This proves the actual secret VALUE never appears anywhere in
    the stored config or in what GET /sources returns."""
    secret_value = "sk-live-distinctive-fake-secret-value-9f3a2b"
    monkeypatch.setenv("PARTNER_API_TOKEN", secret_value)

    create_resp = client.post(
        "/sources",
        json={
            "type": "api",
            "connection_config": {
                "url": "https://example.test/items",
                "auth": {"type": "bearer", "token_env": "PARTNER_API_TOKEN"},
            },
        },
    )
    source_id = create_resp.json()["id"]

    assert secret_value not in create_resp.text

    detail_resp = client.get(f"/sources/{source_id}")
    assert detail_resp.status_code == 200
    assert secret_value not in detail_resp.text
    assert detail_resp.json()["connection_config"]["auth"]["token_env"] == "PARTNER_API_TOKEN"

    list_resp = client.get("/sources")
    assert secret_value not in list_resp.text
