from __future__ import annotations

from pathlib import Path

import pytest

from snowstorm_mcp_server.config import load_config
from snowstorm_mcp_server.snowstorm_native import SnowstormNativeService


CONFIG_PATH = Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"


@pytest.mark.integration
def test_snowstorm_native_get_concept_detail_for_myocardial_infarction() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    targets = load_config(CONFIG_PATH).targets
    if "snowstorm" not in targets:
        pytest.skip("snowstorm target missing in local config")

    with SnowstormNativeService(targets["snowstorm"]) as svc:
        detail = svc.get_concept(concept_id="22298006", max_synonyms=20)

    assert detail.concept_id == "22298006"
    assert detail.pt and "myocardial infarction" in detail.pt.lower()
    assert detail.fsn and "(disorder)" in detail.fsn.lower()
    assert detail.semantic_tag == "disorder"
    assert any("heart attack" in s.lower() for s in detail.synonyms)
