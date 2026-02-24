from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from .config import TargetConfig
from .http_client import HttpClient, HttpRequestError


class BackendType(str, Enum):
    UNKNOWN = "unknown"
    SNOWSTORM = "snowstorm"
    LITE = "lite"


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend_type: BackendType = BackendType.UNKNOWN
    has_fhir: bool = False
    has_native_api: bool = False
    has_lite_load_package: bool = False
    fhir_metadata: dict[str, Any] | None = None


class TargetStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reachable: bool
    base_url: str
    fhir_base_url: str
    capabilities: Capabilities
    error: str | None = None


def _probe_fhir_metadata(client: HttpClient, target: TargetConfig) -> tuple[bool, dict[str, Any] | None]:
    url = f"{target.fhir_base_url}/metadata"
    try:
        data = client.request("GET", url, expect_json=True)
    except HttpRequestError:
        return False, None
    return True, data


def _probe_native_codesystems(client: HttpClient, target: TargetConfig) -> bool:
    url = f"{target.base_url}/codesystems"
    try:
        response = client.request_allow_error("GET", url)
    except HttpRequestError:
        return False
    return response.status_code < 400


def _probe_native_browser_api(client: HttpClient, target: TargetConfig) -> bool:
    # Snowstorm native browser endpoints are a strong signal even if /codesystems is transiently failing.
    url = f"{target.base_url}/browser/MAIN/descriptions"
    try:
        response = client.request_allow_error("GET", url, params={"term": "test", "limit": 1, "active": "true"})
    except HttpRequestError:
        return False
    if response.status_code == 404:
        return False
    return response.status_code < 500


def _probe_lite_load_package(client: HttpClient, target: TargetConfig) -> bool:
    # Endpoint exists on Lite and may return 401/403/405 if method/auth are not suitable.
    url = f"{target.base_url}/fhir-admin/load-package"
    try:
        response = client.request_allow_error("GET", url)
    except HttpRequestError:
        return False
    return response.status_code != 404


def classify_backend(target: TargetConfig, has_fhir: bool, has_native_api: bool, has_lite: bool) -> BackendType:
    if target.mode == "snowstorm":
        return BackendType.SNOWSTORM
    if target.mode == "lite":
        return BackendType.LITE
    if has_native_api:
        return BackendType.SNOWSTORM
    if has_fhir and has_lite:
        return BackendType.LITE
    return BackendType.UNKNOWN


def probe_target(target: TargetConfig, *, client: HttpClient | None = None) -> TargetStatus:
    own_client = client is None
    client = client or HttpClient(target)
    try:
        has_fhir, metadata = _probe_fhir_metadata(client, target)
        has_native_codesystems = _probe_native_codesystems(client, target)
        has_native_browser = _probe_native_browser_api(client, target)
        has_lite = _probe_lite_load_package(client, target)
        has_native = has_native_codesystems or has_native_browser
        caps = Capabilities(
            backend_type=classify_backend(target, has_fhir, has_native, has_lite),
            has_fhir=has_fhir,
            has_native_api=has_native,
            has_lite_load_package=has_lite,
            fhir_metadata=metadata,
        )
        return TargetStatus(
            reachable=has_fhir or has_native,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=caps,
        )
    except HttpRequestError as exc:
        return TargetStatus(
            reachable=False,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=Capabilities(),
            error=str(exc),
        )
    finally:
        if own_client:
            client.close()
