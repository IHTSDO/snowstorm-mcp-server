"""Tests for snowstorm_mcp_server.guards."""

from __future__ import annotations

import threading
from time import monotonic

import pytest

from snowstorm_mcp_server.guards import (
    ChildrenCallTracker,
    ConcurrencyLimiter,
    QueryGuards,
    RollingRateLimiter,
    SnowstormGuardError,
    SnowstormRateLimitError,
    inject_large_result_advisory,
    pre_screen_ecl,
    safe_count,
)


# ── pre_screen_ecl ─────────────────────────────────────────────────────


class TestPreScreenEcl:
    def test_safe_query_passes(self):
        pre_screen_ecl("http://snomed.info/sct?fhir_vs=ecl/<<195967001")

    def test_blocks_history_supplement(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<195967001 {{+ HISTORY}}"
        with pytest.raises(SnowstormGuardError, match="History supplements"):
            pre_screen_ecl(url)

    def test_blocks_history_supplement_case_insensitive(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<195967001 {{+ history}}"
        with pytest.raises(SnowstormGuardError, match="History supplements"):
            pre_screen_ecl(url)

    def test_blocks_top_level_minus(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/(<<404684003:attr=<<A) MINUS (<<71388002)"
        with pytest.raises(SnowstormGuardError, match="Top-level MINUS"):
            pre_screen_ecl(url)

    def test_allows_minus_inside_attribute_value(self):
        url = (
            "http://snomed.info/sct?fhir_vs=ecl/"
            "<<404684003:{246075003=(<<105590001 MINUS <<410942007)}"
        )
        pre_screen_ecl(url)

    def test_blocks_wildcard_on_clinical_finding(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard.*clinical findings"):
            pre_screen_ecl(url)

    def test_blocks_wildcard_on_procedure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<71388002:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard.*procedures"):
            pre_screen_ecl(url)

    def test_blocks_bare_clinical_finding(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    def test_blocks_bare_procedure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<71388002"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    def test_blocks_bare_substance(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<105590001"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    def test_allows_clinical_finding_with_constraint(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:363698007=<<80891009"
        pre_screen_ecl(url)

    def test_error_message_has_error_code(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003"
        with pytest.raises(SnowstormGuardError, match=r"\[E_QUERY_BLOCKED\]"):
            pre_screen_ecl(url)


# ── RollingRateLimiter ─────────────────────────────────────────────────


class TestRollingRateLimiter:
    def test_allows_within_limit(self):
        limiter = RollingRateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            limiter.check()

    def test_blocks_over_limit(self):
        limiter = RollingRateLimiter(max_calls=2, window_seconds=60)
        limiter.check()
        limiter.check()
        with pytest.raises(SnowstormRateLimitError, match="Rate limit reached"):
            limiter.check()

    def test_window_expiry_allows_new_calls(self):
        limiter = RollingRateLimiter(max_calls=1, window_seconds=1)
        # Manually backdate the timestamp
        limiter._timestamps.append(monotonic() - 2)
        limiter.check()


# ── safe_count ─────────────────────────────────────────────────────────


class TestSafeCount:
    def test_under_limit(self):
        assert safe_count(50, 500) == 50

    def test_at_limit(self):
        assert safe_count(500, 500) == 500

    def test_over_limit(self):
        assert safe_count(1000, 500) == 500


# ── inject_large_result_advisory ───────────────────────────────────────


class TestLargeResultAdvisory:
    def test_no_advisory_under_threshold(self):
        result = {"total": 50, "items": []}
        out = inject_large_result_advisory(result, threshold=1000, returned_count=20, summary_only=False)
        assert "_guard_advisory" not in out

    def test_advisory_over_threshold(self):
        result = {"total": 5000, "items": []}
        out = inject_large_result_advisory(result, threshold=1000, returned_count=20, summary_only=False)
        assert "_guard_advisory" in out
        assert "5000" in out["_guard_advisory"]

    def test_no_advisory_for_summary(self):
        result = {"total": 5000, "items": []}
        out = inject_large_result_advisory(result, threshold=1000, returned_count=20, summary_only=True)
        assert "_guard_advisory" not in out


# ── ChildrenCallTracker ────────────────────────────────────────────────


class TestChildrenCallTracker:
    def test_allows_within_limit(self):
        tracker = ChildrenCallTracker(max_calls_per_minute=5)
        for _ in range(5):
            tracker.check("12345")

    def test_blocks_over_limit(self):
        tracker = ChildrenCallTracker(max_calls_per_minute=3)
        for _ in range(3):
            tracker.check("12345")
        with pytest.raises(SnowstormGuardError, match="recursive hierarchy traversal"):
            tracker.check("12345")

    def test_error_includes_concept_id(self):
        tracker = ChildrenCallTracker(max_calls_per_minute=1)
        tracker.check("99999")
        with pytest.raises(SnowstormGuardError, match="99999"):
            tracker.check("99999")

    def test_window_expiry(self):
        tracker = ChildrenCallTracker(max_calls_per_minute=1)
        # Backdate the call
        tracker._calls.append(monotonic() - 61)
        tracker.check("12345")


# ── ConcurrencyLimiter ─────────────────────────────────────────────────


class TestConcurrencyLimiter:
    def test_context_manager(self):
        limiter = ConcurrencyLimiter(max_concurrent=2)
        with limiter:
            with limiter:
                pass

    def test_blocks_beyond_limit(self):
        limiter = ConcurrencyLimiter(max_concurrent=1)
        acquired = threading.Event()
        release = threading.Event()
        blocked = threading.Event()

        def hold():
            with limiter:
                acquired.set()
                release.wait(timeout=5)

        def attempt():
            acquired.wait(timeout=5)
            blocked.set()
            with limiter:
                pass

        t1 = threading.Thread(target=hold)
        t2 = threading.Thread(target=attempt)
        t1.start()
        t2.start()
        blocked.wait(timeout=5)
        assert t2.is_alive()
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not t2.is_alive()


# ── QueryGuards orchestrator ───────────────────────────────────────────


class TestQueryGuards:
    def test_pre_expand_caps_count(self):
        g = QueryGuards(max_count_per_call=100)
        capped = g.pre_expand(None, 999)
        assert capped == 100

    def test_pre_expand_passes_count_under_limit(self):
        g = QueryGuards(max_count_per_call=500)
        assert g.pre_expand(None, 50) == 50

    def test_pre_expand_blocks_bad_ecl(self):
        g = QueryGuards()
        with pytest.raises(SnowstormGuardError):
            g.pre_expand("http://snomed.info/sct?fhir_vs=ecl/<<404684003", 20)

    def test_pre_expand_rate_limits(self):
        g = QueryGuards(rate_limit_calls=2, rate_limit_window_seconds=60)
        g.pre_expand(None, 20)
        g.pre_expand(None, 20)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_expand(None, 20)

    def test_post_expand_injects_advisory(self):
        g = QueryGuards(large_result_threshold=100)
        result = {"total": 5000, "items": []}
        out = g.post_expand(result, returned_count=20, summary_only=False)
        assert "_guard_advisory" in out

    def test_pre_hierarchy_tracks_calls(self):
        g = QueryGuards(max_children_calls_per_minute=2)
        g.pre_hierarchy("123")
        g.pre_hierarchy("456")
        with pytest.raises(SnowstormGuardError, match="recursive"):
            g.pre_hierarchy("789")
