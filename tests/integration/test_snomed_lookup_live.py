from __future__ import annotations

from pathlib import Path

import pytest

from snowstorm_mcp_server.capabilities import BackendType, probe_target
from snowstorm_mcp_server.config import load_config
from snowstorm_mcp_server.fhir import SnomedLookupService


CONFIG_PATH = Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"
TEST_CASES = [
    ("404684003", "clinical finding"),
    ("22298006", "myocardial infarction"),
]


def _load_local_targets():
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    app = load_config(CONFIG_PATH)
    return app.targets


def _find_target_by_backend(backend_type: BackendType) -> tuple[str, object]:
    targets = _load_local_targets()
    for name, target in targets.items():
        status = probe_target(target)
        if not status.reachable or not status.capabilities.has_fhir:
            continue
        if status.capabilities.backend_type == backend_type:
            return name, target
    pytest.skip(f"No reachable {backend_type.value} target with FHIR configured in {CONFIG_PATH}")


@pytest.mark.integration
@pytest.mark.parametrize("target_name", ["snowstorm"])
@pytest.mark.parametrize(("code", "expected_display_fragment"), TEST_CASES)
def test_lookup_snomed_concepts_on_live_target(
    target_name: str,
    code: str,
    expected_display_fragment: str,
) -> None:
    targets = _load_local_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in {CONFIG_PATH}")
    target = targets[target_name]

    status = probe_target(target)
    if not status.capabilities.has_fhir:
        pytest.skip(f"FHIR metadata not reachable for {target_name} ({target.fhir_base_url})")

    with SnomedLookupService(target) as svc:
        result = svc.lookup(code=code)

    assert result.code == code
    assert result.display is not None
    assert expected_display_fragment in result.display.lower()
    assert result.version is None or "20251101" in result.version or "sct/" in (result.system or "")


@pytest.mark.integration
def test_lookup_snomed_concepts_on_live_lite_target_if_available() -> None:
    target_name, target = _find_target_by_backend(BackendType.LITE)

    with SnomedLookupService(target) as svc:
        result = svc.lookup(code="404684003")

    assert result.code == "404684003"
    assert result.display is not None
    assert "clinical finding" in result.display.lower()
