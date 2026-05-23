"""Prometheus metric singletons + node timing decorator.

Exposes a small, deliberately-narrow set of metrics that map 1:1 to the
production-readiness questions an SRE asks first:

  - Are tickets flowing? (TICKETS_TOTAL by intent + outcome)
  - Are nodes erroring? (NODE_ERRORS by node name)
  - Where's the latency? (NODE_LATENCY + E2E_LATENCY)
  - What's the LLM cost? (LLM_LATENCY + LLM_TOKENS by call site + token kind)

Scope discipline: NO OpenTelemetry, NO Grafana JSON bundled here, NO custom
collectors. The `/metrics` endpoint in `src/server.py` exposes the default
registry; a Prometheus server scrapes it.

Why a single shared default registry: pytest tests that touch nodes will
increment counters, and tests run multiple times per session. `_TEST_RESET`
exposes a way to clear state in fixtures so a flaky test doesn't pollute
other tests' assertions.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Counters — monotonic; labels carry the slicing dimensions.
# ---------------------------------------------------------------------------

TICKETS_TOTAL = Counter(
    "hitl_tickets_total",
    "Number of tickets that have reached a terminal state.",
    ["intent", "outcome"],  # outcome: sent / escalated / manual_queue / failed_manual
)

NODE_ERRORS = Counter(
    "hitl_node_errors_total",
    "Exceptions raised inside a LangGraph node.",
    ["node"],
)

LLM_TOKENS = Counter(
    "hitl_llm_tokens_total",
    "Cumulative tokens consumed per LLM call site, split by prompt/completion.",
    ["call", "kind"],  # call: classify / draft / drafter / critic / summarize_changes
                       # kind: prompt / completion
)

# ---------------------------------------------------------------------------
# Histograms — for latency. Default buckets are tuned for an LLM-call-driven
# system: 50ms is fast, 30s is "something is wrong".
# ---------------------------------------------------------------------------

_LATENCY_BUCKETS = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0,
)

NODE_LATENCY = Histogram(
    "hitl_node_latency_seconds",
    "Wall-clock latency of one LangGraph node call.",
    ["node"],
    buckets=_LATENCY_BUCKETS,
)

E2E_LATENCY = Histogram(
    "hitl_ticket_e2e_seconds",
    "Wall-clock latency from ticket entry to terminal state.",
    buckets=(0.5, 1.0, 5.0, 30.0, 60.0, 300.0, 1800.0, 3600.0, 86400.0),
)

LLM_LATENCY = Histogram(
    "hitl_llm_latency_seconds",
    "Wall-clock latency of one LLM call.",
    ["call"],
    buckets=_LATENCY_BUCKETS,
)


# ---------------------------------------------------------------------------
# Node timing decorator. Wraps an async node function so the time it spends
# and any raised exception both register in the metrics above. Designed to
# be applied in `src/nodes.py` (v3) and `src/agents/*.py` (v4) at the @
# stack just above @traceable (so we time the same scope LangSmith does).
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def timed_node(node_name: str) -> Callable[[_F], _F]:
    """Decorator factory that records latency + errors for a single node.

    Usage:
        @timed_node("classify_intent")
        @traceable(run_type="chain", name="classify_intent")
        async def classify_intent_node(state: AgentState) -> dict[str, Any]:
            ...

    Order matters: `timed_node` should be OUTER (run first) so its timer
    captures the full @traceable cost. The decorator is intentionally a
    no-op when called inside a synchronous context; we never wrap sync nodes
    (there aren't any in this codebase).
    """

    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception:
                NODE_ERRORS.labels(node=node_name).inc()
                raise
            finally:
                NODE_LATENCY.labels(node=node_name).observe(time.monotonic() - start)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Test helper — never called in production. Pytest fixtures that assert on
# counter values should use this to reset between tests.
# ---------------------------------------------------------------------------


def _TEST_RESET() -> None:
    """Clear all counter / histogram state. **Test-only.**

    `prometheus_client` doesn't expose a public reset on its top-level
    metric types because monotonic counters are not supposed to decrease.
    For tests we reach into the private state — acceptable coupling
    because the alternative is per-test process isolation (slow) or a
    custom CollectorRegistry per test (verbose).

    Labeled metrics keep per-label samples in `_metrics`. Unlabeled
    metrics (E2E_LATENCY) keep samples directly on the wrapper object
    via `_sum` / `_count` / `_buckets`. We handle both.
    """
    for metric in (TICKETS_TOTAL, NODE_ERRORS, LLM_TOKENS,
                   NODE_LATENCY, E2E_LATENCY, LLM_LATENCY):
        # Labeled-metric path: clear the per-label-tuple sample dict.
        # We always attempt this; for unlabeled metrics the dict is empty.
        if hasattr(metric, "_metrics"):
            metric._metrics.clear()  # type: ignore[attr-defined]
        # Unlabeled-metric path: prometheus_client stores accumulators
        # directly on the wrapper. Counter has `_value`; Histogram has
        # `_sum` + `_buckets`. The public `clear()` method errors on
        # unlabeled metrics in prometheus_client≥0.21 (missing _lock),
        # so we reset the private state directly. Test-only path; the
        # coupling is acceptable.
        if hasattr(metric, "_value"):
            metric._value.set(0)  # type: ignore[attr-defined]
        if hasattr(metric, "_sum"):
            metric._sum.set(0)  # type: ignore[attr-defined]
        if hasattr(metric, "_buckets"):
            for bucket in metric._buckets:  # type: ignore[attr-defined]
                bucket.set(0)


__all__ = [
    "E2E_LATENCY",
    "LLM_LATENCY",
    "LLM_TOKENS",
    "NODE_ERRORS",
    "NODE_LATENCY",
    "TICKETS_TOTAL",
    "_TEST_RESET",
    "timed_node",
]
