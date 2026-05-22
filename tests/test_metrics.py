"""Smoke tests for src/metrics.py — confirms the Prometheus singletons are
wired and the timing decorator records both happy-path and error-path.

These tests are deliberately tiny. The point is the metrics ENDPOINT is the
production observability artifact; this file just guarantees the metric
objects exist and respond to .inc() / .observe() / .labels() calls.
"""

from __future__ import annotations

import pytest

from src.metrics import (
    _TEST_RESET,
    LLM_LATENCY,
    LLM_TOKENS,
    NODE_ERRORS,
    NODE_LATENCY,
    TICKETS_TOTAL,
    timed_node,
)


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Clear all counters/histograms before each test."""
    _TEST_RESET()


def test_counter_increments_with_labels() -> None:
    TICKETS_TOTAL.labels(intent="FAQ", outcome="sent").inc()
    TICKETS_TOTAL.labels(intent="FAQ", outcome="sent").inc(2)
    # ._value is the prometheus_client internal; OK for a smoke assert.
    sample = TICKETS_TOTAL.labels(intent="FAQ", outcome="sent")
    assert sample._value.get() == 3.0


def test_histogram_observes_observation() -> None:
    NODE_LATENCY.labels(node="classify_intent").observe(0.12)
    NODE_LATENCY.labels(node="classify_intent").observe(0.34)
    h = NODE_LATENCY.labels(node="classify_intent")
    # _sum is the cumulative observed value across labels — sufficient signal
    # that observations land.
    assert h._sum.get() == pytest.approx(0.46, abs=1e-6)


def test_llm_tokens_label_split() -> None:
    LLM_TOKENS.labels(call="classify", kind="prompt").inc(120)
    LLM_TOKENS.labels(call="classify", kind="completion").inc(30)
    assert (
        LLM_TOKENS.labels(call="classify", kind="prompt")._value.get() == 120
    )
    assert (
        LLM_TOKENS.labels(call="classify", kind="completion")._value.get() == 30
    )


async def test_timed_node_records_latency_on_success() -> None:
    @timed_node("dummy_success")
    async def ok_node() -> int:
        return 42

    result = await ok_node()
    assert result == 42
    # Latency observed; error count untouched.
    assert NODE_LATENCY.labels(node="dummy_success")._sum.get() > 0


async def test_timed_node_increments_error_on_exception() -> None:
    @timed_node("dummy_error")
    async def boom() -> None:
        raise ValueError("simulated failure")

    with pytest.raises(ValueError, match="simulated failure"):
        await boom()
    assert NODE_ERRORS.labels(node="dummy_error")._value.get() == 1.0


def test_llm_latency_observes() -> None:
    # Make sure LLM_LATENCY responds to .labels().observe(...) — the wiring
    # in src/llm.py and src/agents/{drafter,critic}.py depends on this shape.
    LLM_LATENCY.labels(call="classify").observe(0.5)
    assert LLM_LATENCY.labels(call="classify")._sum.get() == pytest.approx(0.5, abs=1e-6)
