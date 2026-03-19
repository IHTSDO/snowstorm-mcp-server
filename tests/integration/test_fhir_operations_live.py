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
ACUTE_MYOCARDIAL_INFARCTION = ("57054005", "Acute myocardial infarction")
INVALID_TEST_CODE = ("999999999999999999", "Deliberately invalid test code")


def _load_targets():
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    return load_config(CONFIG_PATH).targets


def _find_target_by_backend(backend_type: BackendType):
    targets = _load_targets()
    for name, target in targets.items():
        status = probe_target(target)
        if not status.reachable or not status.capabilities.has_fhir:
            continue
        if status.capabilities.backend_type == backend_type:
            return name, target
    pytest.skip(f"No reachable {backend_type.value} target with FHIR configured in {CONFIG_PATH}")


@pytest.mark.integration
@pytest.mark.parametrize("target_name", ["snowstorm"])
def test_validate_code_live(target_name: str) -> None:
    targets = _load_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in local config")
    clinical_finding_code, _clinical_finding_desc = CLINICAL_FINDING
    invalid_code, _invalid_desc = INVALID_TEST_CODE
    with SnomedLookupService(targets[target_name]) as svc:
        valid = svc.validate_code(code=clinical_finding_code)
        invalid = svc.validate_code(code=invalid_code)

    assert valid.result is True
    assert "clinical finding" in (valid.display or "").lower()
    assert invalid.result is False


@pytest.mark.integration
@pytest.mark.parametrize("target_name", ["snowstorm"])
def test_subsumes_live(target_name: str) -> None:
    targets = _load_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in local config")
    mi_code, mi_desc = MYOCARDIAL_INFARCTION
    ami_code, ami_desc = ACUTE_MYOCARDIAL_INFARCTION
    with SnomedLookupService(targets[target_name]) as svc:
        # Myocardial infarction subsumes Acute myocardial infarction.
        rel = svc.subsumes(code_a=mi_code, code_b=ami_code)

    assert rel.outcome in {"subsumes", "equivalent"}, f"{mi_desc} vs {ami_desc}"


@pytest.mark.integration
def test_validate_code_live_on_lite_target_if_available() -> None:
    _target_name, target = _find_target_by_backend(BackendType.LITE)
    clinical_finding_code, _clinical_finding_desc = CLINICAL_FINDING
    invalid_code, _invalid_desc = INVALID_TEST_CODE
    with SnomedLookupService(target) as svc:
        valid = svc.validate_code(code=clinical_finding_code)
        invalid = svc.validate_code(code=invalid_code)

    assert valid.result is True
    assert "clinical finding" in (valid.display or "").lower()
    assert invalid.result is False


@pytest.mark.integration
def test_subsumes_live_on_lite_target_if_available() -> None:
    _target_name, target = _find_target_by_backend(BackendType.LITE)
    mi_code, mi_desc = MYOCARDIAL_INFARCTION
    ami_code, ami_desc = ACUTE_MYOCARDIAL_INFARCTION
    with SnomedLookupService(target) as svc:
        rel = svc.subsumes(code_a=mi_code, code_b=ami_code)

    assert rel.outcome in {"subsumes", "equivalent"}, f"{mi_desc} vs {ami_desc}"


@pytest.mark.integration
def test_expand_fuzzy_search_on_lite_target_if_available() -> None:
    """Fuzzy search via FHIR ValueSet/$expand on Snowstorm Lite using ~ suffix."""
    _target_name, target = _find_target_by_backend(BackendType.LITE)
    mi_code, _mi_desc = MYOCARDIAL_INFARCTION
    misspelled = "myocardal"

    with SnomedLookupService(target) as svc:
        # Without fuzzy, the misspelled term should return no results
        exact_result = svc.expand(filter=misspelled, count=10)
        assert exact_result.total == 0 or exact_result.returned == 0, (
            f"Expected no results for misspelled '{misspelled}' without fuzzy, "
            f"but got total={exact_result.total}, returned={exact_result.returned}"
        )

        # With fuzzy, the same misspelled term should match
        fuzzy_result = svc.expand(filter=misspelled, count=10, fuzzy=True)

    assert fuzzy_result.total is not None and fuzzy_result.total >= 1, (
        f"Fuzzy expand should return at least one result for misspelled '{misspelled}'"
    )
    codes = {item.code for item in fuzzy_result.contains}
    assert mi_code in codes, (
        f"Expected {mi_code} (Myocardial infarction) in fuzzy results, got: {codes}"
    )
