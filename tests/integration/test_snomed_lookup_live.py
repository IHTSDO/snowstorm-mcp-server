from __future__ import annotations

import os
from pathlib import Path

import pytest

from snowstorm_mcp_server.capabilities import BackendType, probe_target
from snowstorm_mcp_server.config import load_config
from snowstorm_mcp_server.fhir import SnomedLookupService


CONFIG_PATH = Path(
    os.getenv(
        "SNOWSTORM_MCP_TEST_CONFIG",
        str(Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"),
    )
)
CLINICAL_FINDING = ("404684003", "Clinical finding")
MYOCARDIAL_INFARCTION = ("22298006", "Myocardial infarction")
TEST_CASES = [
    (CLINICAL_FINDING, "clinical finding"),
    (MYOCARDIAL_INFARCTION, "myocardial infarction"),
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
@pytest.mark.parametrize(("concept_ref", "expected_display_fragment"), TEST_CASES)
def test_lookup_snomed_concepts_on_live_target(
    target_name: str,
    concept_ref: tuple[str, str],
    expected_display_fragment: str,
) -> None:
    code, concept_desc = concept_ref
    targets = _load_local_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in {CONFIG_PATH}")
    target = targets[target_name]

    status = probe_target(target)
    if not status.capabilities.has_fhir:
        pytest.skip(f"FHIR metadata not reachable for {target_name} ({target.fhir_base_url})")

    with SnomedLookupService(target) as svc:
        result = svc.lookup(code=code)

    assert result.code == code, concept_desc
    assert result.display is not None
    assert expected_display_fragment in result.display.lower()



@pytest.mark.integration
def test_lookup_snomed_concepts_on_live_lite_target_if_available() -> None:
    target_name, target = _find_target_by_backend(BackendType.LITE)
    clinical_finding_code, clinical_finding_desc = CLINICAL_FINDING

    with SnomedLookupService(target) as svc:
        result = svc.lookup(code=clinical_finding_code)

    assert result.code == clinical_finding_code, clinical_finding_desc
    assert result.display is not None
    assert "clinical finding" in result.display.lower()
