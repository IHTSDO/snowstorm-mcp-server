from __future__ import annotations

from pathlib import Path

import pytest

from snowstorm_mcp_server.config import load_config
from snowstorm_mcp_server.fhir import SnomedLookupService


CONFIG_PATH = Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"


def _load_targets():
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    return load_config(CONFIG_PATH).targets


@pytest.mark.integration
@pytest.mark.parametrize("target_name", ["snowstorm"])
def test_validate_code_live(target_name: str) -> None:
    targets = _load_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in local config")
    with SnomedLookupService(targets[target_name]) as svc:
        valid = svc.validate_code(code="404684003")
        invalid = svc.validate_code(code="999999999999999999")

    assert valid.result is True
    assert "clinical finding" in (valid.display or "").lower()
    assert invalid.result is False


@pytest.mark.integration
@pytest.mark.parametrize("target_name", ["snowstorm"])
def test_subsumes_live(target_name: str) -> None:
    targets = _load_targets()
    if target_name not in targets:
        pytest.skip(f"Target {target_name} missing in local config")
    with SnomedLookupService(targets[target_name]) as svc:
        # Myocardial infarction subsumes Acute myocardial infarction.
        rel = svc.subsumes(code_a="22298006", code_b="57054005")

    assert rel.outcome in {"subsumes", "equivalent"}
