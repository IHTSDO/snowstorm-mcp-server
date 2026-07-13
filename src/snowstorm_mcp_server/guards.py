"""Performance guards for SNOMED Snowstorm queries.

Layered protections applied before and after calls hit the Snowstorm backend:
  1. ECL pre-screening — blocks known-expensive or unsupported patterns
  2. Rate limiting — rolling window, per-process (global and per-session)
  3. Concurrency cap — prevents burst parallelism
  4. Count capping — hard ceiling on concepts per call
  5. Large result advisory — warns when total exceeds threshold
  6. Children call tracking — detects recursive traversal anti-patterns
  7. Expansion size threshold — preflight summary check to catch novel large sets

All guards produce actionable error messages safe to return to the LLM.
"""

from __future__ import annotations

import logging
import re
import threading
import weakref
from collections import deque
from collections.abc import Callable, Hashable
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────


class SnowstormGuardError(Exception):
    """Raised when a query is blocked by a performance guard.

    Message is actionable and safe to surface directly to the caller.
    """


class SnowstormRateLimitError(SnowstormGuardError):
    """Raised when the per-process rate limit is exceeded."""


# ── ECL pre-screening ──────────────────────────────────────────────────

# ── Expensive root concepts (data-driven pattern generation) ─────────
#
# Adding a new root concept here automatically generates wildcard, dotted-
# notation, and bare-expansion blocked patterns — no regex duplication needed.

_EXPENSIVE_ROOTS: dict[str, tuple[str, str]] = {
    # concept_id: (label, approximate_count)
    "404684003": ("all clinical findings", "~350,000 concepts"),
    "71388002": ("all procedures", "~100,000 concepts"),
    "105590001": ("all substances", "~70,000 concepts"),
    "123037004": ("all body structures", "~40,000 concepts"),
    "138875005": ("SNOMED CT root", "~500,000 concepts"),
}

# Alternation of all expensive root IDs for use in combined regex patterns.
_ROOT_IDS_ALT = "|".join(_EXPENSIVE_ROOTS)


def _build_blocked_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Generate BLOCKED_PATTERNS from _EXPENSIVE_ROOTS plus structural rules."""
    patterns: list[tuple[re.Pattern[str], str]] = [
        # History supplements — not supported, will 500
        (
            re.compile(r"\{\{\s*\+\s*HISTORY", re.IGNORECASE),
            "History supplements ({{+HISTORY}}) are not supported on this server. "
            "Use snomed_validate_code to check individual inactive codes instead.",
        ),
        # Top-level MINUS between large bracketed expressions
        (
            re.compile(r"\)\s*MINUS\s*\(", re.IGNORECASE),
            "Top-level MINUS between two bracketed expressions will time out on large sets. "
            "Use MINUS inside an attribute value instead: "
            "<<X:{attr=(<<A MINUS <<B)} rather than (<<X:attr=<<A) MINUS (<<X:attr=<<B).",
        ),
    ]
    for cid, (label, count) in _EXPENSIVE_ROOTS.items():
        # Full wildcard attribute query
        patterns.append((
            re.compile(rf"<<\s*{cid}\s*:\s*\*\s*=\s*\*"),
            f"Full wildcard attribute query on <<{cid} ({label}, {count}) is too "
            f"expensive. Scope to a subhierarchy before using wildcards.",
        ))
        # Dotted attribute notation
        patterns.append((
            re.compile(rf"<<\s*{cid}\s*\."),
            f"Dotted attribute notation on <<{cid} ({label}, {count}) is too "
            f"expensive. Scope to a subhierarchy before using dotted attribute notation.",
        ))
        # Bare top-level hierarchy root with no constraints
        patterns.append((
            re.compile(rf"^http://snomed\.info/sct\?fhir_vs=ecl/<<\s*{cid}\s*$"),
            f"Bare <<{cid} ({label}, {count}) is too broad. "
            f"Narrow to a subhierarchy first.",
        ))
    return patterns


# Each entry: (compiled regex, error message returned to caller)
BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = _build_blocked_patterns()


# Patterns that are configurable — enabled by default but can be turned off.
# Each group is a named list so QueryGuards can include/exclude them at init time.

# [0..0] cardinality on large top-level roots: finds concepts where an attribute
# is absent by scanning the entire hierarchy, which is extremely expensive.
ZERO_CARDINALITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            rf"<<\s*(?:{_ROOT_IDS_ALT})\b"
            r".*\[0\s*\.\.\s*0\]"
        ),
        "[0..0] cardinality on a top-level hierarchy root requires scanning the entire "
        "concept set to find concepts missing the attribute — this will time out on large "
        "hierarchies. Scope to a subhierarchy first, e.g. "
        "<<50043002:[0..0]363698007=* for respiratory disorders with no finding site.",
    ),
]


def pre_screen_ecl(value_set_url: str) -> None:
    """Check value_set_url against known expensive/unsupported patterns.

    Raises ``SnowstormGuardError`` with an actionable message if blocked.
    """
    for pattern, message in BLOCKED_PATTERNS:
        if pattern.search(value_set_url):
            logger.warning("Blocked ECL pattern in: %r", value_set_url)
            raise SnowstormGuardError(f"[E_QUERY_BLOCKED] {message}")


# ── Rate limiter (rolling window, thread-safe) ─────────────────────────


class RollingRateLimiter:
    """Simple rolling-window rate limiter using a thread lock.

    One instance per MCP server process. For multi-process deployments,
    swap in a Redis-backed equivalent.
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            now = monotonic()
            while self._timestamps and self._timestamps[0] < now - self.window_seconds:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_calls:
                oldest = self._timestamps[0]
                retry_in = int(self.window_seconds - (now - oldest)) + 1
                logger.warning(
                    "Rate limit reached: %d calls in %ds window",
                    self.max_calls,
                    self.window_seconds,
                )
                raise SnowstormRateLimitError(
                    f"[E_RATE_LIMIT] Rate limit reached: maximum {self.max_calls} "
                    f"Snowstorm queries per {self.window_seconds}s. "
                    f"Retry in ~{retry_in}s, or reduce query frequency by using "
                    f"summary_only=true first and only fetching pages on explicit "
                    f"user request."
                )
            self._timestamps.append(now)


# ── Per-session rate limiter ───────────────────────────────────────────


class PerSessionRateLimiter:
    """Rolling-window rate limiter that tracks limits per session object.

    Each unique session gets its own independent RollingRateLimiter.
    Sessions are tracked via ``WeakKeyDictionary`` so entries are
    automatically cleaned up when the session object is garbage-collected,
    avoiding both unbounded memory growth and id-reuse bugs.

    The check-and-record operation is fully atomic — the per-session lock
    is held while calling into the inner ``RollingRateLimiter``.

    One instance per MCP server process. For multi-process deployments,
    swap in a Redis-backed equivalent keyed by session ID.
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._sessions: weakref.WeakKeyDictionary[object, RollingRateLimiter] = (
            weakref.WeakKeyDictionary()
        )
        self._lock = threading.Lock()

    def check(self, session: object) -> None:
        """Check and record a call for the given session.

        Raises ``SnowstormRateLimitError`` if this session has exceeded
        its per-session limit.
        """
        with self._lock:
            limiter = self._sessions.get(session)
            if limiter is None:
                limiter = RollingRateLimiter(self.max_calls, self.window_seconds)
                self._sessions[session] = limiter
            limiter.check()


# ── Expansion size cache ───────────────────────────────────────────────


class ExpansionSizeCache:
    """TTL cache mapping an expansion query signature to its total concept count.

    Used by the expansion threshold guard to skip repeated preflight queries for
    the same expansion parameters. Entries expire after ``ttl_seconds`` (default
    24 h — appropriate for quarterly SNOMED releases).

    A ``max_entries`` cap (default 2048) prevents unbounded memory growth
    from pathological workloads that query many unique ECL URLs. When the
    cap is reached, the oldest entry is evicted (FIFO).

    One instance per MCP server process.
    """

    def __init__(self, ttl_seconds: int = 86400, max_entries: int = 2048) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: dict[Hashable, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> int | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            count, timestamp = entry
            if monotonic() - timestamp > self._ttl:
                del self._cache[key]
                return None
            return count

    def set(self, key: Hashable, count: int) -> None:
        with self._lock:
            self._cache[key] = (count, monotonic())
            if len(self._cache) > self._max_entries:
                # Evict oldest entry (first key in insertion order).
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]


# ── Count cap ──────────────────────────────────────────────────────────


def safe_count(requested: int, max_count: int) -> int:
    """Enforce a hard ceiling on concepts returned per call."""
    capped = min(requested, max_count)
    if capped < requested:
        logger.info("Count capped from %d to %d", requested, capped)
    return capped


# ── Large result advisory ──────────────────────────────────────────────


def inject_large_result_advisory(
    result: dict[str, Any],
    threshold: int,
    returned_count: int,
    summary_only: bool,
) -> dict[str, Any]:
    """Inject an advisory when total exceeds threshold and this is not a summary call."""
    total = result.get("total", 0)
    if total > threshold and not summary_only:
        result["_guard_advisory"] = (
            f"Result set is large ({total} concepts). "
            f"Only the first {returned_count} were returned. "
            f"Confirm with the user before fetching further pages."
        )
        logger.info("Large result advisory injected: total=%d", total)
    return result


# ── Children call tracker (recursive traversal detection) ──────────────


class ChildrenCallTracker:
    """Detects recursive snomed_get_children call patterns.

    If more than ``max_calls`` children/ancestor/descendant calls happen
    within a rolling 60-second window, raises an error suggesting ECL
    transitive closure instead.
    """

    def __init__(self, max_calls_per_minute: int = 5) -> None:
        self.max_calls_per_minute = max_calls_per_minute
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, concept_id: str) -> None:
        with self._lock:
            now = monotonic()
            while self._calls and self._calls[0] < now - 60:
                self._calls.popleft()
            self._calls.append(now)
            if len(self._calls) > self.max_calls_per_minute:
                logger.warning(
                    "Recursive hierarchy traversal detected: %d calls in 60s (concept %s)",
                    len(self._calls),
                    concept_id,
                )
                raise SnowstormGuardError(
                    f"[E_RECURSIVE_TRAVERSAL] snomed_get_children/ancestors/descendants "
                    f"called {len(self._calls)} times in 60s — this looks like a "
                    f"recursive hierarchy traversal. Use snomed_expand with ECL "
                    f"transitive closure instead: "
                    f"value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<{concept_id}' "
                    f"This returns all descendants in a single call."
                )


# ── Concurrency limiter (thread-based semaphore) ───────────────────────


class ConcurrencyLimiter:
    """Wraps a threading.Semaphore to limit parallel Snowstorm requests."""

    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = threading.Semaphore(max_concurrent)

    def acquire(self) -> None:
        self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()

    def __enter__(self) -> ConcurrencyLimiter:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


# ── Guard orchestrator ─────────────────────────────────────────────────


class QueryGuards:
    """Aggregates all guard primitives into a single object.

    Create one instance in ``create_mcp_app`` and wire its methods
    into the tool handlers.
    """

    def __init__(
        self,
        *,
        rate_limit_calls: int = 10,
        rate_limit_window_seconds: int = 60,
        max_concurrent_requests: int = 3,
        max_count_per_call: int = 500,
        large_result_threshold: int = 1000,
        max_children_calls_per_minute: int = 5,
        per_session_rate_limit_calls: int | None = None,
        block_zero_cardinality_on_large_sets: bool = False,
        enable_expansion_size_guard: bool = False,
        expansion_count_threshold: int = 20000,
        size_cache_ttl_seconds: int = 86400,
    ) -> None:
        self.max_count_per_call = max_count_per_call
        self.large_result_threshold = large_result_threshold

        self._rate_limiter = RollingRateLimiter(rate_limit_calls, rate_limit_window_seconds)
        self._concurrency = ConcurrencyLimiter(max_concurrent_requests)
        self._children_tracker = ChildrenCallTracker(max_children_calls_per_minute)
        self._per_session_limiter = (
            PerSessionRateLimiter(per_session_rate_limit_calls, rate_limit_window_seconds)
            if per_session_rate_limit_calls is not None
            else None
        )
        self._extra_ecl_patterns = (
            ZERO_CARDINALITY_PATTERNS if block_zero_cardinality_on_large_sets else []
        )
        self._expansion_threshold = expansion_count_threshold if enable_expansion_size_guard else None
        self._size_cache = (
            ExpansionSizeCache(size_cache_ttl_seconds)
            if enable_expansion_size_guard
            else None
        )

    def _consume_request_budget(self, session: object | None = None) -> None:
        self._rate_limiter.check()
        self._check_per_session(session)

    def _check_per_session(self, session: object | None) -> None:
        if session is not None and self._per_session_limiter is not None:
            self._per_session_limiter.check(session)

    def _check_extra_ecl_patterns(self, value_set_url: str) -> None:
        for pattern, message in self._extra_ecl_patterns:
            if pattern.search(value_set_url):
                logger.warning("Blocked configurable ECL pattern in: %r", value_set_url)
                raise SnowstormGuardError(f"[E_QUERY_BLOCKED] {message}")

    def pre_expand(self, value_set_url: str | None, count: int, session: object | None = None) -> int:
        """Run pre-call guards for snomed_expand. Returns the capped count."""
        if value_set_url:
            pre_screen_ecl(value_set_url)
            self._check_extra_ecl_patterns(value_set_url)
        self._consume_request_budget(session)
        return safe_count(count, self.max_count_per_call)

    def post_expand(
        self,
        result: dict[str, Any],
        returned_count: int,
        summary_only: bool,
    ) -> dict[str, Any]:
        """Run post-call guards (large result advisory injection)."""
        return inject_large_result_advisory(
            result, self.large_result_threshold, returned_count, summary_only
        )

    def pre_hierarchy(self, concept_id: str, count: int, session: object | None = None) -> int:
        """Run pre-call guards for hierarchy tools (children/ancestors/descendants).

        Returns the capped count.
        """
        self._children_tracker.check(concept_id)
        self._consume_request_budget(session)
        return safe_count(count, self.max_count_per_call)

    def pre_lookup(self, session: object | None = None) -> None:
        """Run pre-call guards for single-concept lookup and search tools."""
        self._consume_request_budget(session)

    def expansion_preflight(
        self,
        cache_key: Hashable,
        fetch_total: Callable[[], int],
        session: object | None = None,
    ) -> None:
        """Check expansion size against the configured threshold.

        ``fetch_total`` is called only on a cache miss — it should perform a
        lightweight ``summary_only=True`` query and return the total concept count.
        Results are cached by expansion query signature so repeated calls to the
        same expression on the same target with the same filters pay no extra
        backend cost.

        No-op when ``expansion_count_threshold`` is not configured.
        Raises ``SnowstormGuardError`` when the total exceeds the threshold.
        """
        if self._expansion_threshold is None or self._size_cache is None:
            return
        total = self._size_cache.get(cache_key)
        if total is None:
            self._consume_request_budget(session)
            total = fetch_total()
            self._size_cache.set(cache_key, total)
        if total > self._expansion_threshold:
            logger.warning(
                "Expansion blocked by size guard: %d concepts (threshold %d)",
                total,
                self._expansion_threshold,
            )
            raise SnowstormGuardError(
                f"[E_QUERY_BLOCKED] This expansion contains {total:,} concepts, "
                f"which exceeds the server limit of {self._expansion_threshold:,}. "
                f"Narrow your ECL expression or scope to a subhierarchy first."
            )

    @property
    def concurrency(self) -> ConcurrencyLimiter:
        """Access the concurrency limiter for use as a context manager."""
        return self._concurrency
