"""TDD tests for src/slack_router.py — channel routing with priority overrides.

Scope (per adviserplan.md): 3 channels only.
  #support-refunds, #support-technical, #support-complaints

Priority (collapsed for 3 channels):
  1. sentiment == angry  → #support-complaints
  2. by intent:
     refund              → #support-refunds
     technical           → #support-technical
     billing, complaint, FAQ, info, basic_technical, other → #support-technical (catch-all)

TDD discipline: tests written before implementation.
"""

from src.slack_router import route_channel
from src.state import AgentState, initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(**overrides: object) -> AgentState:
    """Build a minimal AgentState with sensible defaults."""
    state = initial_state(
        ticket_id="T-001",
        customer_message="test message",
        email_thread_id="<msg-001@mail.example.com>",
        send_idempotency_key="idem-001",
    )
    # Apply caller-supplied overrides
    for key, value in overrides.items():
        state[key] = value  # type: ignore[literal-required]
    return state


# ---------------------------------------------------------------------------
# Priority 1 — angry sentiment → #support-complaints regardless of intent
# ---------------------------------------------------------------------------

def test_angry_routes_to_complaints():
    """Angry sentiment is the highest priority: goes to #support-complaints."""
    state = make_state(sentiment="angry", intent="refund")
    assert route_channel(state) == "#support-complaints"


def test_angry_overrides_technical_intent():
    """Angry + technical intent → still #support-complaints."""
    state = make_state(sentiment="angry", intent="technical")
    assert route_channel(state) == "#support-complaints"


def test_angry_overrides_billing_intent():
    """Angry + billing intent → still #support-complaints."""
    state = make_state(sentiment="angry", intent="billing")
    assert route_channel(state) == "#support-complaints"


# ---------------------------------------------------------------------------
# Intent routing — non-angry sentiment
# ---------------------------------------------------------------------------

def test_refund_intent_routes_to_refunds():
    """Non-angry refund intent → #support-refunds."""
    state = make_state(sentiment="neutral", intent="refund")
    assert route_channel(state) == "#support-refunds"


def test_technical_intent_routes_to_technical():
    """Non-angry technical intent → #support-technical."""
    state = make_state(sentiment="neutral", intent="technical")
    assert route_channel(state) == "#support-technical"


def test_billing_intent_falls_through_to_technical():
    """Billing intent → catch-all #support-technical (billing channel deferred)."""
    state = make_state(sentiment="neutral", intent="billing")
    assert route_channel(state) == "#support-technical"


def test_faq_intent_falls_through_to_technical():
    """FAQ intent → catch-all #support-technical."""
    state = make_state(sentiment="neutral", intent="FAQ")
    assert route_channel(state) == "#support-technical"


def test_complaint_intent_falls_through_to_technical():
    """Complaint intent (non-angry) → catch-all #support-technical."""
    state = make_state(sentiment="neutral", intent="complaint")
    assert route_channel(state) == "#support-technical"


def test_other_intent_falls_through_to_technical():
    """Other/unknown intent → catch-all #support-technical."""
    state = make_state(sentiment="neutral", intent="other")
    assert route_channel(state) == "#support-technical"


def test_basic_technical_intent_routes_to_technical():
    """basic_technical intent (auto-send safe set) → #support-technical."""
    state = make_state(sentiment="neutral", intent="basic_technical")
    assert route_channel(state) == "#support-technical"


def test_info_intent_routes_to_technical():
    """info intent (auto-send safe set) → #support-technical."""
    state = make_state(sentiment="neutral", intent="info")
    assert route_channel(state) == "#support-technical"


# ---------------------------------------------------------------------------
# Sentiment variants — positive and neutral both fall through to intent
# ---------------------------------------------------------------------------

def test_positive_sentiment_routes_by_intent():
    """Positive sentiment doesn't trigger complaints channel."""
    state = make_state(sentiment="positive", intent="refund")
    assert route_channel(state) == "#support-refunds"


def test_neutral_sentiment_routes_by_intent():
    """Neutral sentiment routes purely by intent."""
    state = make_state(sentiment="neutral", intent="technical")
    assert route_channel(state) == "#support-technical"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_sentiment_routes_by_intent():
    """Empty/missing sentiment defaults to intent routing, not complaints."""
    state = make_state(sentiment="", intent="refund")
    assert route_channel(state) == "#support-refunds"


def test_empty_intent_unknown_falls_to_catch_all():
    """Empty intent → catch-all #support-technical."""
    state = make_state(sentiment="neutral", intent="")
    assert route_channel(state) == "#support-technical"


def test_angry_with_empty_intent_still_complaints():
    """Angry + empty intent → #support-complaints (sentiment check runs first)."""
    state = make_state(sentiment="angry", intent="")
    assert route_channel(state) == "#support-complaints"


def test_return_value_includes_hash_prefix():
    """All returned channels start with '#'."""
    for intent in ["refund", "technical", "billing", "complaint", "FAQ", "other"]:
        for sentiment in ["angry", "neutral", "positive"]:
            state = make_state(sentiment=sentiment, intent=intent)
            result = route_channel(state)
            assert result.startswith("#"), f"Expected '#' prefix, got: {result!r}"


def test_route_channel_writes_slack_channel_to_state():
    """route_channel stores the chosen channel in state['slack_channel']."""
    state = make_state(sentiment="neutral", intent="refund")
    channel = route_channel(state)
    assert state["slack_channel"] == channel
