from __future__ import annotations

import json
import os
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
    def validate_mode_requirements(self) -> "AuthConfig":
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

    base_url: str
    mode: Literal["auto", "snowstorm", "lite"] = "auto"
    fhir_path: str = "/fhir"
    timeout_seconds: float = Field(default=10.0, gt=0)
    verify_tls: bool = True
    auth: AuthConfig = Field(default_factory=AuthConfig)
    terminology_name: str | None = None

    @model_validator(mode="after")
    def normalize_fields(self) -> "TargetConfig":
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


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: dict[str, TargetConfig]
    default_terminology: str | None = None


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


def load_config(path: str | Path | None = None) -> AppConfig:
    raw_path = path or os.getenv("SNOWSTORM_MCP_CONFIG")
    if not raw_path:
        raise ValueError("No config path provided (argument or SNOWSTORM_MCP_CONFIG)")
    cfg_path = Path(raw_path).expanduser().resolve()
    data = _load_raw_data(cfg_path)
    return AppConfig.model_validate(data)

