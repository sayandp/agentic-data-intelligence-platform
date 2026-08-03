"""Generic REST connector with pluggable pagination and auth.

Hard caps on pages and total rows exist so a misconfigured source (a cursor
that never terminates, an API that never returns an empty page) can't pull
indefinitely. Hitting the page cap is recorded as a connector warning -
without it, truncated-by-cap data is indistinguishable from a genuine
row_count_drop to the validation layer, and a human needs to be able to tell
those apart.
"""

from __future__ import annotations

import random
import time
from typing import Literal

import httpx
import pandas as pd
from pydantic import BaseModel, Field

from app.connectors.base import BaseConnector
from app.connectors.credentials import resolve_env_var
from app.contract import DataContract, SourceType
from app.datetime_coercion import normalize_datetime_columns
from app.retry import backoff_delay_seconds

DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 30.0
RETRYABLE_STATUS_CODES = {429}


class APIConnectorError(Exception):
    """Raised when a request exhausts its retries; the run fails with this
    message as the recorded reason."""


class AuthConfig(BaseModel):
    type: Literal["none", "bearer", "api_key_header", "basic"] = "none"
    token_env: str | None = None  # bearer
    header_name: str = "X-API-Key"  # api_key_header
    key_env: str | None = None  # api_key_header
    username_env: str | None = None  # basic
    password_env: str | None = None  # basic


class PaginationConfig(BaseModel):
    strategy: Literal["none", "page_number", "cursor", "offset_limit"] = "none"
    # page_number
    page_param: str = "page"
    start_page: int = 1
    # cursor
    cursor_param: str = "cursor"
    next_cursor_path: str | None = None  # dotted path in the response body to the next cursor value
    next_url_path: str | None = None  # dotted path to a full next-page URL (takes priority over next_cursor_path)
    # offset_limit
    offset_param: str = "offset"
    limit_param: str = "limit"
    page_size: int = 100


class APIConnectorConfig(BaseModel):
    url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    record_path: str | None = None  # dotted path to the record list; None means the body IS the list
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    max_pages: int = DEFAULT_MAX_PAGES
    max_rows: int | None = None


class APIConnector(BaseConnector):
    source_immutable = False  # the endpoint can return different data between fetches

    def __init__(
        self,
        source_id: str,
        config: APIConnectorConfig,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep=time.sleep,
        rng: random.Random | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.source_id = source_id
        self.config = config
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._transport = transport  # test hook: httpx.MockTransport

    def fetch(self) -> DataContract:
        headers, auth = self._resolve_auth()
        headers.update(self.config.headers)

        with httpx.Client(transport=self._transport) as client:
            records, pages_fetched, page_cap_hit = self._fetch_all_pages(client, headers, auth)

        df = pd.json_normalize(records) if records else pd.DataFrame()
        connector_metadata: dict = {
            "pages_fetched": pages_fetched,
            "total_rows": len(records),
            "page_cap_hit": page_cap_hit,
        }
        if page_cap_hit:
            connector_metadata["warnings"] = [
                f"page cap ({self.config.max_pages}) reached while paginating {self.config.url}; "
                "results are truncated. Truncated-by-cap data looks identical to a genuine row_count_drop "
                "to the validation layer - this warning is what tells them apart."
            ]

        # JSON has no declared schema either, but ISO-8601 date strings are
        # common enough in API payloads that the same parse-rate path
        # (Files' own path) is worth running here too - never a
        # declared-schema coercion, since REST responses don't carry one.
        normalization = normalize_datetime_columns(df)
        df = normalization.data
        if normalization.coercions:
            connector_metadata["datetime_coercions"] = normalization.coercions
        if normalization.parse_attempts:
            connector_metadata["datetime_parse_attempts"] = normalization.parse_attempts

        return DataContract(
            data=df, source_type=SourceType.API, source_id=self.source_id, connector_metadata=connector_metadata
        )

    # -- auth: every credential is env-resolved, never read from connection_config directly --

    def _resolve_auth(self) -> tuple[dict[str, str], httpx.Auth | None]:
        auth = self.config.auth
        if auth.type == "none":
            return {}, None
        if auth.type == "bearer":
            token = resolve_env_var(auth.token_env)
            return {"Authorization": f"Bearer {token}"}, None
        if auth.type == "api_key_header":
            key = resolve_env_var(auth.key_env)
            return {auth.header_name: key}, None
        if auth.type == "basic":
            username = resolve_env_var(auth.username_env)
            password = resolve_env_var(auth.password_env)
            return {}, httpx.BasicAuth(username, password)
        raise ValueError(f"unknown auth type '{auth.type}'")

    # -- pagination --

    def _fetch_all_pages(
        self, client: httpx.Client, headers: dict[str, str], auth: httpx.Auth | None
    ) -> tuple[list[dict], int, bool]:
        strategy = self.config.pagination.strategy
        records: list[dict] = []
        pages_fetched = 0
        page_cap_hit = False
        request_url = self.config.url
        request_params: dict | None = dict(self.config.params)

        while True:
            response = self._request_with_retry(client, request_url, headers, request_params, auth)
            pages_fetched += 1
            body = response.json()
            page_records = self._extract_records(body)
            records.extend(page_records)

            if self.config.max_rows is not None and len(records) >= self.config.max_rows:
                records = records[: self.config.max_rows]
                break

            if strategy == "none" or not page_records:
                break  # single page, or an empty page legitimately means "no more data"

            if pages_fetched >= self.config.max_pages:
                page_cap_hit = True
                break

            next_request = self._next_request(strategy, request_params, body)
            if next_request is None:
                break
            request_url, request_params = next_request

        return records, pages_fetched, page_cap_hit

    def _next_request(self, strategy: str, current_params: dict, body) -> tuple[str, dict | None] | None:
        pagination = self.config.pagination
        if strategy == "page_number":
            current_page = int(current_params.get(pagination.page_param, pagination.start_page))
            params = dict(current_params)
            params[pagination.page_param] = current_page + 1
            return self.config.url, params
        if strategy == "offset_limit":
            current_offset = int(current_params.get(pagination.offset_param, 0))
            params = dict(current_params)
            params[pagination.offset_param] = current_offset + pagination.page_size
            params[pagination.limit_param] = pagination.page_size
            return self.config.url, params
        if strategy == "cursor":
            next_url = _get_dotted(body, pagination.next_url_path) if pagination.next_url_path else None
            if next_url:
                # A full next-page URL carries its own query string - passing
                # params={} here (rather than None) makes httpx REPLACE that
                # query string with nothing, silently dropping it. None means
                # "don't touch whatever's already in the URL".
                return next_url, None
            cursor_value = _get_dotted(body, pagination.next_cursor_path) if pagination.next_cursor_path else None
            if cursor_value:
                params = dict(self.config.params)
                params[pagination.cursor_param] = cursor_value
                return self.config.url, params
            return None
        raise ValueError(f"unknown pagination strategy '{strategy}'")

    def _extract_records(self, body) -> list[dict]:
        if self.config.record_path is None:
            if isinstance(body, list):
                return body
            raise ValueError("response body is not a list and no record_path was configured to locate one")
        value = _get_dotted(body, self.config.record_path)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"record_path '{self.config.record_path}' did not resolve to a list")
        return value

    # -- retry: 429/5xx get exponential backoff + jitter, capped at max_attempts --

    def _request_with_retry(
        self, client: httpx.Client, url: str, headers: dict[str, str], params: dict | None, auth: httpx.Auth | None
    ) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = client.get(url, headers=headers, params=params, auth=auth, timeout=REQUEST_TIMEOUT_SECONDS)
            except httpx.HTTPError as exc:
                if attempt == self.max_attempts:
                    raise APIConnectorError(f"request to {url} failed after {attempt} attempt(s): {exc}") from exc
                self._sleep(backoff_delay_seconds(attempt, rng=self._rng))
                continue

            if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 500:
                if attempt == self.max_attempts:
                    raise APIConnectorError(
                        f"request to {url} failed after {attempt} attempt(s): "
                        f"HTTP {response.status_code}: {response.text[:500]}"
                    )
                self._sleep(backoff_delay_seconds(attempt, rng=self._rng))
                continue

            response.raise_for_status()
            return response
        raise AssertionError("unreachable: retry loop must return or raise")


def _get_dotted(obj, path: str):
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
