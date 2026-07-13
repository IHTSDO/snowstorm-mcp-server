"""Tests for snowstorm_mcp_server.guards."""

from __future__ import annotations

import threading
from time import monotonic

import pytest

from snowstorm_mcp_server.guards import (
    _EXPENSIVE_ROOTS,
    ZERO_CARDINALITY_PATTERNS,
    ChildrenCallTracker,
    ConcurrencyLimiter,
    ExpansionSizeCache,
    PerSessionRateLimiter,
    QueryGuards,
    RollingRateLimiter,
    SnowstormGuardError,
    SnowstormRateLimitError,
    inject_large_result_advisory,
    pre_screen_ecl,
    safe_count,
)


class _Session:
    """Minimal weakref-compatible stand-in for an MCP session."""
    pass


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

    def test_blocks_dotted_notation_on_clinical_finding(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003.363698007"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    def test_blocks_dotted_notation_on_procedure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<71388002.260686004"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    def test_blocks_dotted_notation_on_substance(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<105590001.246075003"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    def test_allows_dotted_notation_on_scoped_subhierarchy(self):
        # <<50043002 (respiratory disorders) is fine — not a blocked root
        url = "http://snomed.info/sct?fhir_vs=ecl/<<50043002.363698007"
        pre_screen_ecl(url)

    def test_blocks_wildcard_on_substance(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<105590001:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard"):
            pre_screen_ecl(url)

    def test_blocks_bare_snomed_root(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<138875005"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    def test_blocks_wildcard_on_snomed_root(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<138875005:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard"):
            pre_screen_ecl(url)

    def test_blocks_dotted_notation_on_snomed_root(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<138875005.363698007"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    def test_blocks_bare_body_structure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<123037004"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    def test_blocks_wildcard_on_body_structure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<123037004:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard"):
            pre_screen_ecl(url)

    def test_blocks_dotted_notation_on_body_structure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<123037004.272741003"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    def test_allows_body_structure_with_constraint(self):
        # <<80891009 (heart structure) is a scoped subhierarchy — fine
        url = "http://snomed.info/sct?fhir_vs=ecl/<<80891009:272741003=<<7771000"
        pre_screen_ecl(url)

    def test_error_message_has_error_code(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003"
        with pytest.raises(SnowstormGuardError, match=r"\[E_QUERY_BLOCKED\]"):
            pre_screen_ecl(url)


# ── Parameterized coverage for all expensive roots ────────────────────


class TestAllExpensiveRootsCoverage:
    """Ensure every root in _EXPENSIVE_ROOTS is blocked for all pattern types."""

    @pytest.mark.parametrize("concept_id", _EXPENSIVE_ROOTS.keys())
    def test_wildcard_blocked(self, concept_id):
        url = f"http://snomed.info/sct?fhir_vs=ecl/<<{concept_id}:*=*"
        with pytest.raises(SnowstormGuardError, match="wildcard"):
            pre_screen_ecl(url)

    @pytest.mark.parametrize("concept_id", _EXPENSIVE_ROOTS.keys())
    def test_dotted_notation_blocked(self, concept_id):
        url = f"http://snomed.info/sct?fhir_vs=ecl/<<{concept_id}.363698007"
        with pytest.raises(SnowstormGuardError, match="Dotted attribute notation"):
            pre_screen_ecl(url)

    @pytest.mark.parametrize("concept_id", _EXPENSIVE_ROOTS.keys())
    def test_bare_expansion_blocked(self, concept_id):
        url = f"http://snomed.info/sct?fhir_vs=ecl/<<{concept_id}"
        with pytest.raises(SnowstormGuardError, match="too broad"):
            pre_screen_ecl(url)

    @pytest.mark.parametrize("concept_id", _EXPENSIVE_ROOTS.keys())
    def test_scoped_subhierarchy_allowed(self, concept_id):
        """Constrained queries on expensive roots must still be allowed."""
        url = f"http://snomed.info/sct?fhir_vs=ecl/<<{concept_id}:363698007=<<80891009"
        pre_screen_ecl(url)


# ── ExpansionSizeCache ─────────────────────────────────────────────────


class TestExpansionSizeCache:
    def test_miss_returns_none(self):
        cache = ExpansionSizeCache()
        assert cache.get("http://snomed.info/sct?fhir_vs=ecl/<<195967001") is None

    def test_set_then_get(self):
        cache = ExpansionSizeCache()
        url = "http://snomed.info/sct?fhir_vs=ecl/<<195967001"
        cache.set(url, 42)
        assert cache.get(url) == 42

    def test_expired_entry_returns_none(self):
        cache = ExpansionSizeCache(ttl_seconds=1)
        url = "http://snomed.info/sct?fhir_vs=ecl/<<195967001"
        cache.set(url, 99)
        # Backdate the timestamp so the entry is stale
        cache._cache[url] = (99, monotonic() - 2)
        assert cache.get(url) is None

    def test_different_urls_are_independent(self):
        cache = ExpansionSizeCache()
        cache.set("http://snomed.info/sct?fhir_vs=ecl/<<195967001", 10)
        assert cache.get("http://snomed.info/sct?fhir_vs=ecl/<<50043002") is None

    def test_max_entries_evicts_oldest(self):
        cache = ExpansionSizeCache(max_entries=2)
        cache.set("url-a", 1)
        cache.set("url-b", 2)
        cache.set("url-c", 3)  # should evict url-a
        assert cache.get("url-a") is None
        assert cache.get("url-b") == 2
        assert cache.get("url-c") == 3


# ── ZERO_CARDINALITY_PATTERNS ──────────────────────────────────────────


class TestZeroCardinalityPatterns:
    """Tests for the configurable [0..0] cardinality pattern list."""

    _pattern, _message = ZERO_CARDINALITY_PATTERNS[0]

    def test_blocks_zero_cardinality_on_clinical_finding(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:[0..0]363698007=*"
        assert self._pattern.search(url)

    def test_blocks_zero_cardinality_on_procedure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<71388002:[0..0]260686004=*"
        assert self._pattern.search(url)

    def test_blocks_zero_cardinality_on_substance(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<105590001:[0..0]246075003=*"
        assert self._pattern.search(url)

    def test_blocks_zero_cardinality_on_body_structure(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<123037004:[0..0]272741003=*"
        assert self._pattern.search(url)

    def test_blocks_zero_cardinality_on_snomed_root(self):
        url = "http://snomed.info/sct?fhir_vs=ecl/<<138875005:[0..0]363698007=*"
        assert self._pattern.search(url)

    def test_allows_zero_cardinality_on_scoped_subhierarchy(self):
        # <<50043002 (respiratory disorders) is not a blocked root
        url = "http://snomed.info/sct?fhir_vs=ecl/<<50043002:[0..0]363698007=*"
        assert not self._pattern.search(url)

    def test_allows_nonzero_cardinality_on_large_root(self):
        # [1..1] cardinality on a large root is not caught by this pattern
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:[1..1]{363698007=<<80891009}"
        assert not self._pattern.search(url)


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


# ── PerSessionRateLimiter ──────────────────────────────────────────────


class TestPerSessionRateLimiter:
    def test_different_sessions_are_independent(self):
        limiter = PerSessionRateLimiter(max_calls=1, window_seconds=60)
        session_a = _Session()
        session_b = _Session()
        limiter.check(session_a)
        # session_b has its own counter — should not be blocked
        limiter.check(session_b)

    def test_same_session_shares_limit(self):
        limiter = PerSessionRateLimiter(max_calls=1, window_seconds=60)
        session = _Session()
        limiter.check(session)
        with pytest.raises(SnowstormRateLimitError):
            limiter.check(session)

    def test_one_session_blocked_does_not_affect_another(self):
        limiter = PerSessionRateLimiter(max_calls=1, window_seconds=60)
        session_a = _Session()
        session_b = _Session()
        limiter.check(session_a)
        with pytest.raises(SnowstormRateLimitError):
            limiter.check(session_a)
        # session_b is unaffected
        limiter.check(session_b)

    def test_gc_cleans_up_session_entries(self):
        """When a session is garbage-collected, its entry is removed from the limiter."""
        import gc

        limiter = PerSessionRateLimiter(max_calls=1, window_seconds=60)
        session = _Session()
        limiter.check(session)
        assert len(limiter._sessions) == 1
        del session
        gc.collect()
        assert len(limiter._sessions) == 0


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
        g.pre_hierarchy("123", 50)
        g.pre_hierarchy("456", 50)
        with pytest.raises(SnowstormGuardError, match="recursive"):
            g.pre_hierarchy("789", 50)

    def test_pre_hierarchy_caps_count(self):
        g = QueryGuards(max_count_per_call=500)
        assert g.pre_hierarchy("123", 50) == 50
        assert g.pre_hierarchy("456", 1_000_000) == 500

    def test_pre_lookup_applies_global_rate_limit(self):
        g = QueryGuards(rate_limit_calls=2, rate_limit_window_seconds=60)
        g.pre_lookup()
        g.pre_lookup()
        with pytest.raises(SnowstormRateLimitError):
            g.pre_lookup()

    def test_expansion_preflight_consumes_budget_on_cache_miss(self):
        g = QueryGuards(
            rate_limit_calls=2,
            rate_limit_window_seconds=60,
            enable_expansion_size_guard=True,
        )
        g.pre_expand("http://snomed.info/sct?fhir_vs=ecl/<<195967001", 20)
        g.expansion_preflight("query-a", fetch_total=lambda: 5)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_expand("http://snomed.info/sct?fhir_vs=ecl/<<50043002", 20)

    def test_expansion_preflight_cache_hit_does_not_consume_budget(self):
        g = QueryGuards(
            rate_limit_calls=2,
            rate_limit_window_seconds=60,
            enable_expansion_size_guard=True,
        )
        g.expansion_preflight("query-a", fetch_total=lambda: 5)
        g.expansion_preflight("query-a", fetch_total=lambda: 999)

    def test_per_session_blocks_heavy_session_not_others(self):
        g = QueryGuards(
            rate_limit_calls=100,
            per_session_rate_limit_calls=2,
        )
        session_a = _Session()
        session_b = _Session()
        g.pre_lookup(session_a)
        g.pre_lookup(session_a)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_lookup(session_a)
        # session_b is unaffected
        g.pre_lookup(session_b)

    def test_per_session_disabled_by_default(self):
        g = QueryGuards(rate_limit_calls=100)
        session = _Session()
        # Should not raise regardless of call count from same session
        for _ in range(20):
            g.pre_lookup(session)

    def test_per_session_applies_to_expand(self):
        g = QueryGuards(rate_limit_calls=100, per_session_rate_limit_calls=1)
        session = _Session()
        g.pre_expand(None, 20, session)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_expand(None, 20, session)

    def test_expansion_preflight_applies_per_session_limit(self):
        g = QueryGuards(
            rate_limit_calls=100,
            per_session_rate_limit_calls=2,
            enable_expansion_size_guard=True,
        )
        session = _Session()
        g.pre_expand("http://snomed.info/sct?fhir_vs=ecl/<<195967001", 20, session)
        g.expansion_preflight("query-a", fetch_total=lambda: 5, session=session)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_lookup(session)

    def test_zero_cardinality_blocked_when_enabled(self):
        g = QueryGuards(block_zero_cardinality_on_large_sets=True)
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:[0..0]363698007=*"
        with pytest.raises(SnowstormGuardError, match=r"\[E_QUERY_BLOCKED\]"):
            g.pre_expand(url, 20)

    def test_zero_cardinality_allowed_by_default(self):
        g = QueryGuards()
        # pre_expand will still try to call the rate limiter; patch it to isolate the flag
        g._rate_limiter.check = lambda: None  # type: ignore[method-assign]
        url = "http://snomed.info/sct?fhir_vs=ecl/<<404684003:[0..0]363698007=*"
        # Should not raise — zero-cardinality guard is off by default
        g.pre_expand(url, 20)

    def test_zero_cardinality_on_scoped_subhierarchy_always_allowed(self):
        g = QueryGuards(block_zero_cardinality_on_large_sets=True)
        g._rate_limiter.check = lambda: None  # type: ignore[method-assign]
        url = "http://snomed.info/sct?fhir_vs=ecl/<<50043002:[0..0]363698007=*"
        g.pre_expand(url, 20)

    def test_expansion_preflight_no_op_when_disabled(self):
        g = QueryGuards()  # enable_expansion_size_guard defaults to False
        called = []
        g.expansion_preflight("http://snomed.info/sct?fhir_vs=ecl/<<195967001", lambda: called.append(1) or 999999)
        assert called == [], "fetch_total should not be called when guard is disabled"

    def test_expansion_preflight_no_op_when_explicitly_disabled(self):
        g = QueryGuards(enable_expansion_size_guard=False, expansion_count_threshold=100)
        called = []
        g.expansion_preflight("http://snomed.info/sct?fhir_vs=ecl/<<195967001", lambda: called.append(1) or 999999)
        assert called == [], "fetch_total should not be called when guard is disabled"

    def test_expansion_preflight_blocks_over_threshold(self):
        g = QueryGuards(enable_expansion_size_guard=True, expansion_count_threshold=1000)
        with pytest.raises(SnowstormGuardError, match=r"\[E_QUERY_BLOCKED\]"):
            g.expansion_preflight(
                "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
                fetch_total=lambda: 5000,
            )

    def test_expansion_preflight_allows_under_threshold(self):
        g = QueryGuards(enable_expansion_size_guard=True, expansion_count_threshold=1000)
        # Should not raise
        g.expansion_preflight(
            "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
            fetch_total=lambda: 50,
        )

    def test_expansion_preflight_caches_result(self):
        g = QueryGuards(enable_expansion_size_guard=True, expansion_count_threshold=1000)
        call_count = []

        def _fetch():
            call_count.append(1)
            return 50

        url = "http://snomed.info/sct?fhir_vs=ecl/<<195967001"
        g.expansion_preflight(url, fetch_total=_fetch)
        g.expansion_preflight(url, fetch_total=_fetch)
        g.expansion_preflight(url, fetch_total=_fetch)
        assert len(call_count) == 1, "fetch_total should only be called once per URL"

    def test_expansion_preflight_error_message_includes_counts(self):
        g = QueryGuards(enable_expansion_size_guard=True, expansion_count_threshold=500)
        with pytest.raises(SnowstormGuardError, match="350,000") as exc_info:
            g.expansion_preflight(
                "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
                fetch_total=lambda: 350_000,
            )
        assert "500" in str(exc_info.value)

    def test_per_session_applies_to_hierarchy(self):
        g = QueryGuards(
            rate_limit_calls=100,
            max_children_calls_per_minute=100,
            per_session_rate_limit_calls=1,
        )
        session = _Session()
        g.pre_hierarchy("123", 50, session)
        with pytest.raises(SnowstormRateLimitError):
            g.pre_hierarchy("456", 50, session)
