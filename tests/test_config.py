from __future__ import annotations

import textwrap

import pytest

from snowstorm_mcp_server.config import AppConfig, load_config


def test_load_yaml_config_supports_multiple_targets(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server_mode: lite
            targets:
              lite-int:
                base_url: http://localhost:8081/
                mode: lite
                terminology_name: snomedct
                fhir_path: fhir
                auth:
                  mode: none
              lite-nz:
                base_url: http://localhost:8082
                mode: lite
                terminology_name: snomedct-nz
                fhir_path: fhir
                auth:
                  mode: none
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)

    assert isinstance(app, AppConfig)
    assert set(app.targets) == {"lite-int", "lite-nz"}
    assert app.targets["lite-int"].base_url == "http://localhost:8081"
    assert app.targets["lite-nz"].fhir_path == "/fhir"


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
            server_mode: lite
            default_terminology: snomedct-us
            targets:
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


def test_terminology_name_is_normalized(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server_mode: lite
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


def test_dotenv_loaded_at_startup_provides_env_for_config(tmp_path, monkeypatch) -> None:
    """Simulates the startup flow: load_dotenv in __main__ then load_config."""
    from dotenv import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("DOTENV_TEST_TOKEN=from-dotenv\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
                auth:
                  mode: bearer
                  token: ${DOTENV_TEST_TOKEN}
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    load_dotenv(env_file)

    app = load_config(cfg)

    assert app.targets["snowstorm"].auth.token is not None
    assert app.targets["snowstorm"].auth.token.get_secret_value() == "from-dotenv"


def test_dotenv_loaded_at_startup_provides_config_path(tmp_path, monkeypatch) -> None:
    """Simulates the startup flow: load_dotenv in __main__ then load_config with no path."""
    from dotenv import load_dotenv

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
            """
        ),
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"SNOWSTORM_MCP_CONFIG={cfg}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SNOWSTORM_MCP_CONFIG", raising=False)
    load_dotenv(env_file)

    app = load_config()  # no path argument — picked up from .env via env var

    assert app.targets["snowstorm"].base_url == "http://localhost:8080"


def test_dotenv_does_not_override_existing_env(tmp_path, monkeypatch) -> None:
    from dotenv import load_dotenv

    monkeypatch.setenv("DOTENV_OVERRIDE_TEST", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("DOTENV_OVERRIDE_TEST=from-dotenv\n", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            targets:
              snowstorm:
                base_url: http://localhost:8080
                auth:
                  mode: bearer
                  token: ${DOTENV_OVERRIDE_TEST}
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    load_dotenv(env_file)

    app = load_config(cfg)

    assert app.targets["snowstorm"].auth.token.get_secret_value() == "from-shell"


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


def test_mixed_backend_types_rejected(tmp_path) -> None:
    """Mixing Snowstorm and Lite targets in one config must raise."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server_mode: snowstorm
            targets:
              snowstorm:
                base_url: http://localhost:8080
                mode: snowstorm
              lite-us:
                base_url: http://localhost:8081
                mode: lite
                terminology_name: snomedct-us
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="Mixing Snowstorm and Snowstorm Lite"):
        load_config(cfg)


def test_multiple_lite_targets_allowed(tmp_path) -> None:
    """Multiple Lite targets (one per edition) must be accepted."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server_mode: lite
            targets:
              lite-int:
                base_url: http://localhost:8081
                mode: lite
                terminology_name: snomedct
              lite-nz:
                base_url: http://localhost:8082
                mode: lite
                terminology_name: snomedct-nz
            """
        ),
        encoding="utf-8",
    )

    app = load_config(cfg)
    assert set(app.targets) == {"lite-int", "lite-nz"}


def test_server_mode_mismatch_rejected(tmp_path) -> None:
    """server_mode must match the target backend type."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            server_mode: snowstorm
            targets:
              lite-int:
                base_url: http://localhost:8081
                mode: lite
                terminology_name: snomedct
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="server_mode is 'snowstorm' but all targets"):
        load_config(cfg)
