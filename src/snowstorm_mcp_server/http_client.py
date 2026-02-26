from __future__ import annotations

from typing import Any

import httpx

from .config import AuthConfig, TargetConfig


class HttpRequestError(RuntimeError):
    """Raised when an HTTP request fails in a normalized way."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _build_auth(auth_cfg: AuthConfig) -> httpx.Auth | None:
    if auth_cfg.mode == "basic":
        if not auth_cfg.username or not auth_cfg.password:
            raise ValueError("basic auth requires username and password")
        return httpx.BasicAuth(auth_cfg.username, auth_cfg.password.get_secret_value())
    return None


def _build_headers(auth_cfg: AuthConfig) -> dict[str, str]:
    headers = dict(auth_cfg.headers)
    if auth_cfg.mode == "bearer":
        if not auth_cfg.token:
            raise ValueError("bearer auth requires token")
        headers.setdefault("Authorization", f"Bearer {auth_cfg.token.get_secret_value()}")
    return headers


class HttpClient:
    def __init__(
        self,
        target: TargetConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.target = target
        self._own_client = client is None
        self._auth = _build_auth(target.auth)
        self._headers = _build_headers(target.auth)
        self._client = client or httpx.Client(
            timeout=target.timeout_seconds,
            verify=target.verify_tls,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        expect_json: bool = False,
        **kwargs: Any,
    ) -> httpx.Response | dict[str, Any]:
        kwargs = self._apply_request_defaults(kwargs)
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise HttpRequestError(f"Request timed out: {method} {url}") from exc
        except httpx.RequestError as exc:
            raise HttpRequestError(f"Request failed: {method} {url}: {exc}") from exc

        if response.status_code >= 400:
            raise HttpRequestError(
                f"HTTP {response.status_code} for {method} {url}: {response.text[:200]}",
                status_code=response.status_code,
            )
        if expect_json:
            try:
                data = response.json()
            except ValueError as exc:
                raise HttpRequestError(f"Invalid JSON response from {method} {url}") from exc
            if not isinstance(data, dict):
                raise HttpRequestError(f"Expected JSON object from {method} {url}")
            return data
        return response

    def request_allow_error(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs = self._apply_request_defaults(kwargs)
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise HttpRequestError(f"Request timed out: {method} {url}") from exc
        except httpx.RequestError as exc:
            raise HttpRequestError(f"Request failed: {method} {url}: {exc}") from exc

    def _apply_request_defaults(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        merged = dict(kwargs)
        if self._auth is not None and "auth" not in merged:
            merged["auth"] = self._auth
        if self._headers:
            headers = dict(self._headers)
            if "headers" in merged and merged["headers"] is not None:
                headers.update(dict(merged["headers"]))
            merged["headers"] = headers
        return merged
