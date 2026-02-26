from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import TargetConfig
from .http_client import HttpClient, HttpRequestError


class LookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    system: str | None = None
    version: str | None = None
    display: str | None = None
    raw_parameters: dict[str, list[Any]] = Field(default_factory=dict)


class ValidateCodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    result: bool
    system: str | None = None
    version: str | None = None
    display: str | None = None
    message: str | None = None
    raw_parameters: dict[str, list[Any]] = Field(default_factory=dict)


class SubsumesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code_a: str
    code_b: str
    outcome: str
    system: str | None = None
    version: str | None = None
    raw_parameters: dict[str, list[Any]] = Field(default_factory=dict)


class ExpansionContainsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str | None = None
    version: str | None = None
    code: str | None = None
    display: str | None = None
    inactive: bool | None = None


class ExpandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value_set_url: str
    filter: str | None = None
    offset: int = 0
    count: int | None = None
    total: int | None = None
    returned: int = 0
    summary_only: bool = False
    truncated: bool = False
    contains: list[ExpansionContainsItem] = Field(default_factory=list)
    expansion_identifier: str | None = None
    raw_contains_count: int | None = None


def _parse_parameters_resource(data: dict[str, Any]) -> dict[str, list[Any]]:
    if data.get("resourceType") != "Parameters":
        raise HttpRequestError("FHIR operation did not return a Parameters resource")
    out: dict[str, list[Any]] = {}
    for param in data.get("parameter", []):
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not isinstance(name, str):
            continue
        value: Any = None
        for key, val in param.items():
            if key.startswith("value") and key != "name":
                value = val
                break
        if value is None and "part" in param:
            value = param["part"]
        out.setdefault(name, []).append(value)
    return out


class SnomedLookupService:
    DEFAULT_IMPLICIT_SNOMED_VALUESET_URL = "http://snomed.info/sct?fhir_vs"

    def __init__(self, target: TargetConfig, *, client: HttpClient | None = None) -> None:
        self.target = target
        self._own_client = client is None
        self.client = client or HttpClient(target)

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> "SnomedLookupService":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def lookup(
        self,
        *,
        code: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> LookupResult:
        params = {"code": code, "system": system}
        if version:
            params["version"] = version
        url = f"{self.target.fhir_base_url}/CodeSystem/$lookup"
        data = self.client.request("GET", url, params=params, expect_json=True)
        parsed = _parse_parameters_resource(data)
        return LookupResult(
            code=code,
            system=_first_str(parsed.get("system")),
            version=_first_str(parsed.get("version")),
            display=_first_str(parsed.get("display")),
            raw_parameters=parsed,
        )

    def validate_code(
        self,
        *,
        code: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> ValidateCodeResult:
        url = f"{self.target.fhir_base_url}/CodeSystem/$validate-code"
        payload: dict[str, Any] = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": system},
                {"name": "code", "valueCode": code},
            ],
        }
        if version:
            payload["parameter"].append({"name": "version", "valueString": version})
        try:
            data = self.client.request(
                "POST",
                url,
                json=payload,
                headers={"Content-Type": "application/fhir+json"},
                expect_json=True,
            )
            parsed = _parse_parameters_resource(data)
            return ValidateCodeResult(
                code=code,
                result=bool(_first_bool(parsed.get("result"))),
                system=_first_str(parsed.get("system")) or system,
                version=_first_str(parsed.get("version")),
                display=_first_str(parsed.get("display")),
                message=_first_str(parsed.get("message")),
                raw_parameters=parsed,
            )
        except HttpRequestError as exc:
            if "not-supported" not in str(exc).lower():
                raise
            return self._validate_code_via_lookup(code=code, system=system, version=version)

    def subsumes(
        self,
        *,
        code_a: str,
        code_b: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> SubsumesResult:
        params: dict[str, Any] = {"system": system, "codeA": code_a, "codeB": code_b}
        if version:
            params["version"] = version
        url = f"{self.target.fhir_base_url}/CodeSystem/$subsumes"
        data = self.client.request("GET", url, params=params, expect_json=True)
        parsed = _parse_parameters_resource(data)
        return SubsumesResult(
            code_a=code_a,
            code_b=code_b,
            outcome=_first_str(parsed.get("outcome")) or "unknown",
            system=_first_str(parsed.get("system")) or system,
            version=_first_str(parsed.get("version")),
            raw_parameters=parsed,
        )

    def expand(
        self,
        *,
        value_set_url: str | None = None,
        filter: str | None = None,
        offset: int = 0,
        count: int = 20,
        summary_only: bool = False,
        max_contains: int = 100,
    ) -> ExpandResult:
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if count < 1:
            raise ValueError("count must be >= 1")
        if max_contains < 1:
            raise ValueError("max_contains must be >= 1")

        resolved_url = (value_set_url or "").strip() or self.DEFAULT_IMPLICIT_SNOMED_VALUESET_URL
        params: dict[str, Any] = {
            "url": resolved_url,
            "offset": offset,
            "count": count,
        }
        if filter:
            params["filter"] = filter

        url = f"{self.target.fhir_base_url}/ValueSet/$expand"
        data = self.client.request("GET", url, params=params, expect_json=True)
        if data.get("resourceType") != "ValueSet":
            raise HttpRequestError("FHIR $expand did not return a ValueSet resource")

        expansion = data.get("expansion")
        if not isinstance(expansion, dict):
            expansion = {}

        raw_contains = expansion.get("contains")
        parsed_contains: list[ExpansionContainsItem] = []
        raw_contains_count: int | None = None
        if isinstance(raw_contains, list):
            raw_contains_count = len(raw_contains)
            if not summary_only:
                for item in raw_contains[:max_contains]:
                    if not isinstance(item, dict):
                        continue
                    parsed_contains.append(
                        ExpansionContainsItem(
                            system=item.get("system") if isinstance(item.get("system"), str) else None,
                            version=item.get("version")
                            if isinstance(item.get("version"), str)
                            else None,
                            code=item.get("code") if isinstance(item.get("code"), str) else None,
                            display=item.get("display")
                            if isinstance(item.get("display"), str)
                            else None,
                            inactive=item.get("inactive")
                            if isinstance(item.get("inactive"), bool)
                            else None,
                        )
                    )

        server_offset = _as_int(expansion.get("offset"))
        return ExpandResult(
            value_set_url=resolved_url,
            filter=filter,
            offset=server_offset if server_offset is not None else offset,
            count=count,
            total=_as_int(expansion.get("total")),
            returned=0 if summary_only else len(parsed_contains),
            summary_only=summary_only,
            truncated=(
                (not summary_only)
                and raw_contains_count is not None
                and raw_contains_count > len(parsed_contains)
            ),
            contains=[] if summary_only else parsed_contains,
            expansion_identifier=(
                expansion.get("identifier") if isinstance(expansion.get("identifier"), str) else None
            ),
            raw_contains_count=raw_contains_count,
        )

    def _validate_code_via_lookup(
        self,
        *,
        code: str,
        system: str,
        version: str | None,
    ) -> ValidateCodeResult:
        try:
            result = self.lookup(code=code, system=system, version=version)
        except HttpRequestError as exc:
            message = str(exc)
            if _looks_like_not_found_lookup_error(exc, message):
                return ValidateCodeResult(
                    code=code,
                    result=False,
                    system=system,
                    version=version,
                    message="Validation emulated via lookup fallback: code not found.",
                    raw_parameters={},
                )
            raise
        return ValidateCodeResult(
            code=code,
            result=True,
            system=result.system or system,
            version=result.version or version,
            display=result.display,
            message="Validation emulated via lookup fallback (validate-code not supported).",
            raw_parameters=result.raw_parameters,
        )


def _first_str(values: list[Any] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, str) else None


def _first_bool(values: list[Any] | None) -> bool | None:
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, bool) else None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _looks_like_not_found_lookup_error(exc: HttpRequestError, message: str) -> bool:
    msg = message.lower()
    if exc.status_code in {400, 404}:
        return True
    if exc.status_code == 500 and (
        "concept\" is null" in msg
        or "concept is null" in msg
        or "nullpointerexception" in msg
    ):
        return True
    if "not found" in msg:
        return True
    return False
