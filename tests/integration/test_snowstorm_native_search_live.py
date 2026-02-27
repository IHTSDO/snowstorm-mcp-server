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
def test_snowstorm_native_search_finds_myocardial_infarction() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")
    targets = load_config(CONFIG_PATH).targets
    if "snowstorm" not in targets:
        pytest.skip("snowstorm target missing in local config")

    mi_code, mi_desc = MYOCARDIAL_INFARCTION
    with SnowstormNativeService(targets["snowstorm"]) as svc:
        result = svc.search_concepts(term="myocardial infarction", limit=10, active_only=True)

    assert result.returned >= 1
    concept_ids = {hit.concept_id for hit in result.hits}
    assert mi_code in concept_ids, mi_desc
    top_text = " ".join([(result.hits[0].pt or ""), (result.hits[0].fsn or ""), (result.hits[0].matched_term or "")]).lower()
    assert "myocardial infarction" in top_text
