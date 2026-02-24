from __future__ import annotations

import pytest

from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.snowstorm_native import SnowstormNativeService


class _StubClient:
    def __init__(self) -> None:
        self.called = False

    def request(self, *_args, **_kwargs):
        self.called = True
        return {"items": [], "totalElements": 0}

    def close(self) -> None:
        return None


def test_snowstorm_native_search_rejects_short_term_with_clear_message() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    with SnowstormNativeService(target, client=_StubClient()) as svc:
        with pytest.raises(ValueError, match="at least 3 searchable characters"):
            svc.search_concepts(term="AD")


def test_snowstorm_native_search_allows_three_char_term_before_backend_call() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    with SnowstormNativeService(target, client=stub) as svc:
        assert svc.MIN_SEARCH_TERM_LENGTH == 3
        svc.search_concepts(term="A1C", limit=1)
        assert stub.called is True
