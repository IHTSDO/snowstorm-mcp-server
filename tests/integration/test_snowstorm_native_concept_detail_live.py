from __future__ import annotations

import os
from pathlib import Path

import pytest

from snowstorm_mcp_server.config import load_config
from snowstorm_mcp_server.snowstorm_native import SnowstormNativeService


CONFIG_PATH = Path(
    os.getenv(
        "SNOWSTORM_MCP_TEST_CONFIG",
        str(Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"),
    )
)
MYOCARDIAL_INFARCTION = ("22298006", "Myocardial infarction")


@pytest.mark.integration
def test_snowstorm_native_get_concept_detail_for_myocardial_infarction() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    targets = load_config(CONFIG_PATH).targets
    if "snowstorm" not in targets:
        pytest.skip("snowstorm target missing in local config")

    mi_code, mi_desc = MYOCARDIAL_INFARCTION
    with SnowstormNativeService(targets["snowstorm"]) as svc:
        detail = svc.get_concept(concept_id=mi_code, max_synonyms=20)

    assert detail.concept_id == mi_code, mi_desc
    assert detail.pt and "myocardial infarction" in detail.pt.lower()
    assert detail.fsn and "(disorder)" in detail.fsn.lower()
    assert detail.semantic_tag == "disorder"
    assert any("heart attack" in s.lower() for s in detail.synonyms)
