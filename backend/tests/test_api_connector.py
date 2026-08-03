import httpx
import pytest

from app.connectors.api_connector import (
    APIConnector,
    APIConnectorConfig,
    APIConnectorError,
    AuthConfig,
    PaginationConfig,
)
from app.connectors.credentials import MissingCredentialError


def _connector(config: APIConnectorConfig, handler, **kwargs) -> APIConnector:
    return APIConnector(source_id="s1", config=config, transport=httpx.MockTransport(handler), sleep=lambda _s: None, **kwargs)


# ---- pagination: none ----


def test_pagination_none_single_page():
    def handler(request):
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    contract = _connector(APIConnectorConfig(url="https://api.test/items"), handler).fetch()
    assert contract.row_count == 2
    assert contract.metadata()["pages_fetched"] == 1


# ---- pagination: page_number ----


def test_pagination_page_number_walks_until_empty():
    pages = {1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}], 3: []}

    def handler(request):
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages.get(page, []))

    config = APIConnectorConfig(url="https://api.test/items", pagination=PaginationConfig(strategy="page_number"))
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 3
    assert contract.metadata()["pages_fetched"] == 3
    assert contract.metadata()["page_cap_hit"] is False


# ---- pagination: offset_limit ----


def test_pagination_offset_limit_walks_until_empty_page():
    all_items = [{"id": i} for i in range(25)]

    def handler(request):
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", "10"))
        return httpx.Response(200, json=all_items[offset : offset + limit])

    config = APIConnectorConfig(
        url="https://api.test/items",
        pagination=PaginationConfig(strategy="offset_limit", page_size=10),
    )
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 25
    # 10 + 10 + 5 (a short but non-empty page) + 1 confirming empty page. A
    # short page isn't treated as "last page" on its own - only a genuinely
    # empty one is, since not every API signals "no more data" via a short
    # final page, and correctness matters more than saving one request.
    assert contract.metadata()["pages_fetched"] == 4


# ---- pagination: cursor (both next_cursor and full next_url variants) ----


def test_pagination_cursor_via_cursor_value():
    pages = {
        None: {"items": [{"id": 1}], "next": "abc"},
        "abc": {"items": [{"id": 2}], "next": "def"},
        "def": {"items": [{"id": 3}], "next": None},
    }

    def handler(request):
        cursor = request.url.params.get("cursor")
        body = pages[cursor]
        return httpx.Response(200, json=body)

    config = APIConnectorConfig(
        url="https://api.test/items",
        record_path="items",
        pagination=PaginationConfig(strategy="cursor", next_cursor_path="next"),
    )
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 3
    assert contract.metadata()["pages_fetched"] == 3


def test_pagination_cursor_via_full_next_url():
    def handler(request):
        if "page2" in str(request.url):
            return httpx.Response(200, json={"items": [{"id": 2}], "next_url": None})
        return httpx.Response(200, json={"items": [{"id": 1}], "next_url": "https://api.test/items?page2=1"})

    config = APIConnectorConfig(
        url="https://api.test/items",
        record_path="items",
        pagination=PaginationConfig(strategy="cursor", next_url_path="next_url"),
    )
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 2
    assert contract.metadata()["pages_fetched"] == 2


# ---- page cap ----


def test_page_cap_hit_is_recorded_as_a_warning():
    def handler(request):
        return httpx.Response(200, json=[{"id": 1}])  # never-ending pages

    config = APIConnectorConfig(
        url="https://api.test/items", pagination=PaginationConfig(strategy="page_number"), max_pages=3
    )
    contract = _connector(config, handler).fetch()

    assert contract.metadata()["pages_fetched"] == 3
    assert contract.metadata()["page_cap_hit"] is True
    assert any("page cap" in w for w in contract.metadata()["warnings"])


def test_page_cap_not_hit_when_data_ends_naturally():
    pages = {1: [{"id": 1}], 2: []}

    def handler(request):
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages.get(page, []))

    config = APIConnectorConfig(
        url="https://api.test/items", pagination=PaginationConfig(strategy="page_number"), max_pages=50
    )
    contract = _connector(config, handler).fetch()
    assert contract.metadata()["page_cap_hit"] is False
    assert "warnings" not in contract.metadata()


def test_max_rows_caps_total_regardless_of_page_cap():
    def handler(request):
        return httpx.Response(200, json=[{"id": i} for i in range(10)])  # 10 items, every page, forever

    config = APIConnectorConfig(
        url="https://api.test/items",
        pagination=PaginationConfig(strategy="page_number"),
        max_rows=25,
        max_pages=50,
    )
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 25  # cut off mid-page-3, well under the page cap


# ---- retry: 429 and 5xx ----


def test_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=[{"id": 1}])

    contract = _connector(APIConnectorConfig(url="https://api.test/items"), handler).fetch()
    assert contract.row_count == 1
    assert calls["n"] == 3


def test_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=[{"id": 1}])

    contract = _connector(APIConnectorConfig(url="https://api.test/items"), handler).fetch()
    assert contract.row_count == 1
    assert calls["n"] == 2


def test_retry_exhaustion_fails_with_reason_recorded():
    def handler(request):
        return httpx.Response(500, text="server error")

    connector = _connector(APIConnectorConfig(url="https://api.test/items"), handler)
    with pytest.raises(APIConnectorError, match="500"):
        connector.fetch()


def test_non_retryable_4xx_fails_immediately():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    connector = _connector(APIConnectorConfig(url="https://api.test/items"), handler)
    with pytest.raises(httpx.HTTPStatusError):
        connector.fetch()
    assert calls["n"] == 1  # no retry burned on a non-retryable error


# ---- auth: each type resolves from env ----


def test_auth_none_sends_no_authorization_header():
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=[{"id": 1}])

    _connector(APIConnectorConfig(url="https://api.test/items"), handler).fetch()
    assert "authorization" not in captured["headers"]


def test_auth_bearer_resolves_from_env(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-token-value")
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=[{"id": 1}])

    config = APIConnectorConfig(url="https://api.test/items", auth=AuthConfig(type="bearer", token_env="MY_TOKEN"))
    _connector(config, handler).fetch()
    assert captured["headers"]["authorization"] == "Bearer secret-token-value"


def test_auth_api_key_header_resolves_from_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-key-value")
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=[{"id": 1}])

    config = APIConnectorConfig(
        url="https://api.test/items", auth=AuthConfig(type="api_key_header", header_name="X-Api-Key", key_env="MY_KEY")
    )
    _connector(config, handler).fetch()
    assert captured["headers"]["x-api-key"] == "secret-key-value"


def test_auth_basic_resolves_from_env(monkeypatch):
    monkeypatch.setenv("MY_USER", "alice")
    monkeypatch.setenv("MY_PASS", "hunter2")
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=[{"id": 1}])

    config = APIConnectorConfig(
        url="https://api.test/items",
        auth=AuthConfig(type="basic", username_env="MY_USER", password_env="MY_PASS"),
    )
    _connector(config, handler).fetch()
    assert captured["headers"]["authorization"].startswith("Basic ")


def test_auth_missing_env_var_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("NOT_SET_TOKEN", raising=False)

    def handler(request):
        return httpx.Response(200, json=[{"id": 1}])

    config = APIConnectorConfig(url="https://api.test/items", auth=AuthConfig(type="bearer", token_env="NOT_SET_TOKEN"))
    connector = _connector(config, handler)
    with pytest.raises(MissingCredentialError) as exc_info:
        connector.fetch()
    assert "NOT_SET_TOKEN" in str(exc_info.value)


# ---- record_path ----


def test_record_path_extracts_nested_list():
    def handler(request):
        return httpx.Response(200, json={"data": {"items": [{"id": 1}, {"id": 2}]}})

    config = APIConnectorConfig(url="https://api.test/items", record_path="data.items")
    contract = _connector(config, handler).fetch()
    assert contract.row_count == 2


def test_no_record_path_requires_body_to_be_a_list():
    def handler(request):
        return httpx.Response(200, json={"not": "a list"})

    connector = _connector(APIConnectorConfig(url="https://api.test/items"), handler)
    with pytest.raises(ValueError):
        connector.fetch()
