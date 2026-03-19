"""Performance guards for SNOMED Snowstorm queries.

Layered protections applied before and after calls hit the Snowstorm backend:
  1. ECL pre-screening — blocks known-expensive or unsupported patterns
  2. Rate limiting — rolling window, per-process
  3. Concurrency cap — prevents burst parallelism
  4. Count capping — hard ceiling on concepts per call
  5. Large result advisory — warns when total exceeds threshold
  6. Children call tracking — detects recursive traversal anti-patterns

All guards produce actionable error messages safe to return to the LLM.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
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

# Each entry: (compiled regex, error message returned to caller)
BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
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
    # Full wildcard on top-level clinical finding
    (
        re.compile(r"<<\s*404684003\s*:\s*\*\s*=\s*\*"),
        "Full wildcard attribute query on <<404684003 (all clinical findings) is too "
        "expensive. Scope to a subhierarchy before using wildcards.",
    ),
    # Full wildcard on top-level procedure
    (
        re.compile(r"<<\s*71388002\s*:\s*\*\s*=\s*\*"),
        "Full wildcard attribute query on <<71388002 (all procedures) is too expensive. "
        "Scope to a subhierarchy before using wildcards.",
    ),
    # Bare top-level hierarchy roots with no constraints
    (
        re.compile(r"^http://snomed\.info/sct\?fhir_vs=ecl/<<\s*404684003\s*$"),
        "Bare <<404684003 (all clinical findings, ~350,000 concepts) is too broad. "
        "Narrow to a subhierarchy first (e.g. <<50043002 for respiratory disorders).",
    ),
    (
        re.compile(r"^http://snomed\.info/sct\?fhir_vs=ecl/<<\s*71388002\s*$"),
        "Bare <<71388002 (all procedures, ~100,000 concepts) is too broad. "
        "Narrow to a subhierarchy first.",
    ),
    (
        re.compile(r"^http://snomed\.info/sct\?fhir_vs=ecl/<<\s*105590001\s*$"),
        "Bare <<105590001 (all substances, ~70,000 concepts) is too broad. "
        "Add constraints or narrow the scope first.",
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
                raise SnowstormRateLimitError(
                    f"[E_RATE_LIMIT] Rate limit reached: maximum {self.max_calls} "
                    f"Snowstorm queries per {self.window_seconds}s. "
                    f"Retry in ~{retry_in}s, or reduce query frequency by using "
                    f"summary_only=true first and only fetching pages on explicit "
                    f"user request."
                )
            self._timestamps.append(now)


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
    ) -> None:
        self.max_count_per_call = max_count_per_call
        self.large_result_threshold = large_result_threshold

        self._rate_limiter = RollingRateLimiter(rate_limit_calls, rate_limit_window_seconds)
        self._concurrency = ConcurrencyLimiter(max_concurrent_requests)
        self._children_tracker = ChildrenCallTracker(max_children_calls_per_minute)

    def pre_expand(self, value_set_url: str | None, count: int) -> int:
        """Run pre-call guards for snomed_expand. Returns the capped count."""
        if value_set_url:
            pre_screen_ecl(value_set_url)
        self._rate_limiter.check()
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

    def pre_hierarchy(self, concept_id: str) -> None:
        """Run pre-call guards for hierarchy tools (children/ancestors/descendants)."""
        self._children_tracker.check(concept_id)
        self._rate_limiter.check()

    @property
    def concurrency(self) -> ConcurrencyLimiter:
        """Access the concurrency limiter for use as a context manager."""
        return self._concurrency
