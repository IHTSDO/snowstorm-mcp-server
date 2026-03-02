from __future__ import annotations

import textwrap

import pytest

from snowstorm_mcp_server.config import AppConfig, load_config


def test_load_yaml_config_supports_multiple_targets(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080/
                auth:
                  mode: none
              lite:
                base_url: http://localhost:8081
                mode: lite
                fhir_path: fhir
                auth:
                  mode: none
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert isinstance(app, AppConfig)
    assert set(app.targets) == {"snowstorm", "lite"}
    assert app.targets["snowstorm"].base_url == "http://localhost:8080"
    assert app.targets["lite"].fhir_path == "/fhir"


def test_invalid_basic_auth_fails_fast(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
                auth:
                  mode: basic
                  username: admin
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_config(cfg)


def test_config_with_terminology_name_and_default(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            default_terminology: snomedct-us
            targets:
              snowstorm:
                base_url: http://localhost:8080
              lite:
                base_url: http://localhost:8081
                mode: lite
                terminology_name: SNOMEDCT-US
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert app.default_terminology == "snomedct-us"
    assert app.targets["lite"].terminology_name == "snomedct-us"
    assert app.targets["snowstorm"].terminology_name is None


def test_terminology_name_is_normalized(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              lite:
                base_url: http://localhost:8081
                mode: lite
                terminology_name: "  SNOMEDCT  "
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert app.targets["lite"].terminology_name == "snomedct"


def test_load_config_interpolates_env_placeholders(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SNOW_TOKEN", "secret-token")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
                auth:
                  mode: bearer
                  token: ${SNOW_TOKEN}
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert app.targets["snowstorm"].auth.token is not None
    assert app.targets["snowstorm"].auth.token.get_secret_value() == "secret-token"


def test_load_config_applies_secret_env_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SNOWSTORM_MCP_TARGETS__SNOWSTORM__AUTH__TOKEN", "override-token")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
                auth:
                  mode: bearer
                  token: from-file
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert app.targets["snowstorm"].auth.token is not None
    assert app.targets["snowstorm"].auth.token.get_secret_value() == "override-token"


def test_load_config_assigns_target_name_from_map_key(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              primary:
                base_url: http://localhost:8080
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert app.targets["primary"].name == "primary"
