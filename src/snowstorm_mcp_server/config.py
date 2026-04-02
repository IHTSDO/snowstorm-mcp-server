from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "basic", "bearer", "headers"] = "none"
    username: str | None = None
    password: SecretStr | None = None
    token: SecretStr | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> AuthConfig:
        if self.mode == "basic":
            if not self.username or not self.password:
                raise ValueError("basic auth requires username and password")
        elif self.mode == "bearer":
            if not self.token:
                raise ValueError("bearer auth requires token")
        elif self.mode == "headers":
            if not self.headers:
                raise ValueError("headers auth requires at least one header")
        return self


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    base_url: str
    mode: Literal["auto", "snowstorm", "lite"] = "auto"
    fhir_path: str = "/fhir"
    timeout_seconds: float = Field(default=10.0, gt=0)
    verify_tls: bool = True
    auth: AuthConfig = Field(default_factory=AuthConfig)
    terminology_name: str | None = None

    @model_validator(mode="after")
    def normalize_fields(self) -> TargetConfig:
        self.base_url = self.base_url.rstrip("/")
        if not self.fhir_path.startswith("/"):
            self.fhir_path = f"/{self.fhir_path}"
        self.fhir_path = self.fhir_path.rstrip("/") or "/fhir"
        if self.terminology_name is not None:
            self.terminology_name = self.terminology_name.strip().lower()
        return self

    @property
    def fhir_base_url(self) -> str:
        return f"{self.base_url}{self.fhir_path}"


class GuardConfig(BaseModel):
    """Performance guard tunables. All values have safe defaults."""

    model_config = ConfigDict(extra="forbid")

    rate_limit_calls: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    max_concurrent_requests: int = Field(default=3, ge=1)
    max_count_per_call: int = Field(default=500, ge=1)
    large_result_threshold: int = Field(default=1000, ge=1)
    max_children_calls_per_minute: int = Field(default=5, ge=1)
    per_session_rate_limit_calls: int | None = Field(default=None, ge=1)
    block_zero_cardinality_on_large_sets: bool = Field(default=True)
    enable_expansion_size_guard: bool = Field(default=False)
    expansion_count_threshold: int = Field(default=20000, ge=1)
    size_cache_ttl_seconds: int = Field(default=86400, ge=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class ResponseLimits(BaseModel):
        model_config = ConfigDict(extra="forbid")

        max_expand_contains: int = Field(default=100, ge=1)
        max_search_hits: int = Field(default=50, ge=1)
        max_synonyms: int = Field(default=25, ge=1)

    server_mode: Literal["snowstorm", "lite"] = "snowstorm"
    targets: dict[str, TargetConfig]
    default_terminology: str | None = None
    response_limits: ResponseLimits = Field(default_factory=ResponseLimits)
    guards: GuardConfig = Field(default_factory=GuardConfig)

    @model_validator(mode="after")
    def populate_target_names(self) -> AppConfig:
        for target_name, target_cfg in self.targets.items():
            target_cfg.name = target_name
        if self.default_terminology is not None:
            self.default_terminology = self.default_terminology.strip().lower()
        return self

    @model_validator(mode="after")
    def validate_no_mixed_backend_types(self) -> AppConfig:
        """Ensure all targets use the same backend type (snowstorm or lite).

        Mixing Snowstorm and Snowstorm Lite targets in a single config is not
        supported.  Multiple Lite instances are fine (one per edition).
        """
        modes = {
            name: ("snowstorm" if cfg.mode in ("snowstorm", "auto") else "lite")
            for name, cfg in self.targets.items()
        }
        unique = set(modes.values())
        if len(unique) > 1:
            detail = ", ".join(
                f"'{name}' (mode={self.targets[name].mode})"
                for name in sorted(modes)
            )
            raise ValueError(
                "All targets must use the same backend type. "
                "Mixing Snowstorm and Snowstorm Lite targets is not supported. "
                f"Targets: {detail}"
            )
        if unique:
            effective = unique.pop()
            if effective != self.server_mode:
                raise ValueError(
                    f"server_mode is '{self.server_mode}' but all targets "
                    f"are configured as '{effective}'. "
                    f"Set server_mode to '{effective}' or change target modes."
                )
        return self



def _load_raw_data(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"Unsupported config file type: {path.suffix}")
    if not isinstance(data, dict):
        raise ValueError("Config root must be an object/map")
    return data


_ENV_PLACEHOLDER_RE = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}$"
)


def _interpolate_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _interpolate_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_placeholders(v) for v in value]
    if isinstance(value, str):
        match = _ENV_PLACEHOLDER_RE.match(value.strip())
        if not match:
            return value
        env_name = match.group("name")
        if env_name in os.environ:
            return os.environ[env_name]
        default_value = match.group("default")
        if default_value is not None:
            return default_value
        raise ValueError(f"Missing environment variable for placeholder '{env_name}'")
    return value


def _apply_secret_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    targets = data.get("targets")
    if not isinstance(targets, dict):
        return data
    for target_name, target_data in targets.items():
        if not isinstance(target_data, dict):
            continue
        auth = target_data.setdefault("auth", {})
        if not isinstance(auth, dict):
            continue
        env_target = str(target_name).upper().replace("-", "_")
        for key in ("password", "token"):
            env_key = f"SNOWSTORM_MCP_TARGETS__{env_target}__AUTH__{key.upper()}"
            env_value = os.getenv(env_key)
            if env_value is not None:
                auth[key] = env_value
    return data


def load_config(path: str | Path | None = None) -> AppConfig:
    raw_path = path or os.getenv("SNOWSTORM_MCP_CONFIG")
    if not raw_path:
        raise ValueError("No config path provided (argument or SNOWSTORM_MCP_CONFIG)")
    cfg_path = Path(raw_path).expanduser().resolve()
    data = _load_raw_data(cfg_path)
    data = _interpolate_env_placeholders(data)
    data = _apply_secret_env_overrides(data)
    return AppConfig.model_validate(data)
