"""TDD tests for src/policy.py — two-gate routing logic.

Gates per CLAUDE.md "Two-gate routing":

  Gate 1 — Policy Risk (gate_one_policy_risk):
    Escalate if ANY true:
      - refund or money mention (keyword scan on customer_message)
      - angry sentiment
      - edge-case intent (complaint | other — documented in policy.py)
      - explicit policy match (cancellation, billing dispute, account recovery, legal)
    Returns bool (True = escalate). Side effect: populates risk_flags,
    risk_level, policy_matches in state.

  Gate 2 — Confidence (gate_two_confidence):
    Only runs if Gate 1 passes (no risk detected).
    Escalate if intent_confidence < 0.85 OR draft_confidence < 0.85.
    Returns bool (True = escalate).

  should_auto_send:
    True only when:
      - gate_one_policy_risk returns False (no risk)
      - gate_two_confidence returns False (both confidences >= 0.85)
      - intent in {FAQ, info, basic_technical}

TDD discipline: one failing test at a time. Tests written before implementation.
"""

from src.policy import gate_one_policy_risk, gate_two_confidence, should_auto_send
from src.state import AgentState, initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(**overrides: object) -> AgentState:
    """Build a minimal AgentState with safe defaults."""
    state = initial_state(
        ticket_id="T-001",
        customer_message="I need help with my account.",
        email_thread_id="<msg-001@mail.example.com>",
        send_idempotency_key="idem-001",
    )
    for key, value in overrides.items():
        state[key] = value  # type: ignore[literal-required]
    return state


# ===========================================================================
# Gate 1 — Policy Risk Check
# ===========================================================================

# ---------------------------------------------------------------------------
# Clean pass (no risk) — Gate 1 should return False
# ---------------------------------------------------------------------------

def test_gate1_no_risk_faq():
    """Clean FAQ ticket: no risk flags → Gate 1 passes (False = no escalation)."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="How do I reset my password?",
        risk_flags=[],
        policy_matches=[],
    )
    assert gate_one_policy_risk(state) is False


def test_gate1_no_risk_technical():
    """Clean technical ticket: no risk → Gate 1 passes."""
    state = make_state(
        intent="technical",
        sentiment="neutral",
        customer_message="My dashboard is loading slowly.",
        risk_flags=[],
        policy_matches=[],
    )
    assert gate_one_policy_risk(state) is False


# ---------------------------------------------------------------------------
# Angry sentiment → Gate 1 escalates
# ---------------------------------------------------------------------------

def test_gate1_angry_sentiment_escalates():
    """Angry sentiment alone triggers Gate 1 escalation."""
    state = make_state(
        intent="FAQ",
        sentiment="angry",
        customer_message="Why isn't this working!",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_angry_populates_risk_flags():
    """Gate 1 adds 'angry' to risk_flags when sentiment == angry."""
    state = make_state(
        intent="FAQ",
        sentiment="angry",
        customer_message="This is unacceptable!",
        risk_flags=[],
    )
    gate_one_policy_risk(state)
    assert "angry" in state["risk_flags"]


# ---------------------------------------------------------------------------
# Refund / money mention in message text
# ---------------------------------------------------------------------------

def test_gate1_refund_word_escalates():
    """The word 'refund' in the message body triggers Gate 1."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I want a refund for my subscription.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_money_amount_escalates():
    """A dollar amount in the message triggers Gate 1 (money mention)."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I was charged $250 incorrectly.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_charge_word_escalates():
    """The word 'charge' in the message triggers Gate 1."""
    state = make_state(
        intent="billing",
        sentiment="neutral",
        customer_message="There is an incorrect charge on my account.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_money_back_phrase_escalates():
    """The phrase 'money back' in the message triggers Gate 1."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I want my money back immediately.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_money_flag_added_to_risk_flags():
    """Gate 1 adds 'money_mention' to risk_flags on dollar sign match."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I was billed $99 and I shouldn't have been.",
        risk_flags=[],
    )
    gate_one_policy_risk(state)
    assert "money_mention" in state["risk_flags"]


# ---------------------------------------------------------------------------
# Refund intent (not just text keyword)
# ---------------------------------------------------------------------------

def test_gate1_refund_intent_escalates():
    """intent == refund always escalates via Gate 1."""
    state = make_state(
        intent="refund",
        sentiment="neutral",
        customer_message="I need assistance please.",  # no refund keyword in text
    )
    assert gate_one_policy_risk(state) is True


# ---------------------------------------------------------------------------
# Edge-case intents — complaint | other
# ---------------------------------------------------------------------------

def test_gate1_complaint_intent_escalates():
    """intent == complaint is an edge-case intent → Gate 1 escalates."""
    state = make_state(
        intent="complaint",
        sentiment="neutral",
        customer_message="Your service is terrible.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_other_intent_escalates():
    """intent == other is an edge-case intent → Gate 1 escalates."""
    state = make_state(
        intent="other",
        sentiment="neutral",
        customer_message="Random query not clearly categorized.",
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_edge_intent_adds_risk_flag():
    """Edge-case intent adds 'edge_case_intent' to risk_flags."""
    state = make_state(
        intent="complaint",
        sentiment="neutral",
        customer_message="Your service is bad.",
        risk_flags=[],
    )
    gate_one_policy_risk(state)
    assert "edge_case_intent" in state["risk_flags"]


# ---------------------------------------------------------------------------
# Explicit policy matches already in state
# ---------------------------------------------------------------------------

def test_gate1_policy_match_in_state_escalates():
    """If policy_matches is pre-populated (by Enrich Context), Gate 1 escalates."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I need help.",
        policy_matches=["ACME 4.2.1"],
    )
    assert gate_one_policy_risk(state) is True


def test_gate1_policy_match_adds_risk_flag():
    """Gate 1 adds 'policy_match' to risk_flags when policy_matches is present."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I need help.",
        policy_matches=["ACME 7.1"],
        risk_flags=[],
    )
    gate_one_policy_risk(state)
    assert "policy_match" in state["risk_flags"]


# ---------------------------------------------------------------------------
# risk_level populated correctly
# ---------------------------------------------------------------------------

def test_gate1_refund_sets_financial_risk_level():
    """Refund/money match should set risk_level to 'financial'."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="I want a refund please.",
    )
    gate_one_policy_risk(state)
    assert state["risk_level"] == "financial"


def test_gate1_clean_ticket_risk_level_none():
    """Clean ticket: Gate 1 leaves risk_level as 'none'."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="How do I update my email?",
    )
    gate_one_policy_risk(state)
    assert state["risk_level"] == "none"


def test_gate1_angry_sets_risk_level():
    """Angry sentinel sets risk_level to at least 'financial' or non-none."""
    state = make_state(
        intent="FAQ",
        sentiment="angry",
        customer_message="Terrible service!",
    )
    gate_one_policy_risk(state)
    assert state["risk_level"] != "none"


# ---------------------------------------------------------------------------
# Gate 1 does not overwrite pre-existing risk_flags (appends/dedupes)
# ---------------------------------------------------------------------------

def test_gate1_appends_to_existing_risk_flags():
    """Gate 1 appends its flags to pre-existing risk_flags without clobbering."""
    state = make_state(
        intent="FAQ",
        sentiment="angry",
        customer_message="I want a refund!",
        risk_flags=["pre_existing_flag"],
    )
    gate_one_policy_risk(state)
    # Original flag preserved
    assert "pre_existing_flag" in state["risk_flags"]
    # New flag added
    assert "angry" in state["risk_flags"]


# ===========================================================================
# Gate 2 — Confidence Check
# ===========================================================================

def test_gate2_both_high_confidence_passes():
    """Both confidences >= 0.85 → Gate 2 passes (False = no escalation)."""
    state = make_state(
        intent_confidence=0.90,
        draft_confidence=0.91,
    )
    assert gate_two_confidence(state) is False


def test_gate2_low_intent_confidence_escalates():
    """intent_confidence < 0.85 → Gate 2 escalates."""
    state = make_state(
        intent_confidence=0.70,
        draft_confidence=0.90,
    )
    assert gate_two_confidence(state) is True


def test_gate2_low_draft_confidence_escalates():
    """draft_confidence < 0.85 → Gate 2 escalates."""
    state = make_state(
        intent_confidence=0.90,
        draft_confidence=0.60,
    )
    assert gate_two_confidence(state) is True


def test_gate2_both_low_escalates():
    """Both below threshold → Gate 2 escalates."""
    state = make_state(
        intent_confidence=0.40,
        draft_confidence=0.40,
    )
    assert gate_two_confidence(state) is True


def test_gate2_exactly_at_threshold_passes():
    """Exactly 0.85 on both → Gate 2 passes (boundary inclusive)."""
    state = make_state(
        intent_confidence=0.85,
        draft_confidence=0.85,
    )
    assert gate_two_confidence(state) is False


def test_gate2_zero_confidence_escalates():
    """Missing/default confidence (0.0) → Gate 2 escalates (conservative)."""
    state = make_state(
        intent_confidence=0.0,
        draft_confidence=0.0,
    )
    assert gate_two_confidence(state) is True


# ===========================================================================
# should_auto_send — combines Gate 1 + Gate 2 + safe intent check
# ===========================================================================

def test_should_auto_send_faq_clean():
    """FAQ intent, no risk, both confidences high → auto-send True."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="How do I reset my password?",
        intent_confidence=0.90,
        draft_confidence=0.92,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is True


def test_should_auto_send_info_clean():
    """info intent, no risk, high confidence → auto-send True."""
    state = make_state(
        intent="info",
        sentiment="neutral",
        customer_message="What are your business hours?",
        intent_confidence=0.88,
        draft_confidence=0.90,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is True


def test_should_auto_send_basic_technical_clean():
    """basic_technical intent, no risk, high confidence → auto-send True."""
    state = make_state(
        intent="basic_technical",
        sentiment="neutral",
        customer_message="How do I clear my cache?",
        intent_confidence=0.95,
        draft_confidence=0.93,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is True


def test_should_not_auto_send_refund_intent():
    """Refund intent triggers Gate 1 → never auto-send."""
    state = make_state(
        intent="refund",
        sentiment="neutral",
        customer_message="Please refund my subscription.",
        intent_confidence=0.98,
        draft_confidence=0.98,
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_angry_sentiment():
    """Angry sentiment triggers Gate 1 → never auto-send."""
    state = make_state(
        intent="FAQ",
        sentiment="angry",
        customer_message="Why isn't this working!",
        intent_confidence=0.98,
        draft_confidence=0.98,
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_low_confidence():
    """Clean FAQ but low confidence → Gate 2 fails → no auto-send."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="How do I reset my password?",
        intent_confidence=0.70,
        draft_confidence=0.95,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_complaint_intent():
    """
    Complaint intent is edge-case → Gate 1 fails → no auto-send,
    even if confidence is high.  Advisor's required test.
    """
    state = make_state(
        intent="complaint",
        sentiment="neutral",
        customer_message="I am quite unhappy with the service.",
        intent_confidence=0.99,
        draft_confidence=0.99,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_billing_intent():
    """Billing intent is not in the auto-send safe set → never auto-send."""
    state = make_state(
        intent="billing",
        sentiment="neutral",
        customer_message="I have a question about my invoice.",
        intent_confidence=0.99,
        draft_confidence=0.99,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_technical_intent_not_in_safe_set():
    """
    'technical' (the spec §6 classifier label) is NOT in the auto-send safe
    set {FAQ, info, basic_technical} per CLAUDE.md contract.
    """
    state = make_state(
        intent="technical",
        sentiment="neutral",
        customer_message="My widget is slow.",
        intent_confidence=0.99,
        draft_confidence=0.99,
        risk_flags=[],
        policy_matches=[],
    )
    assert should_auto_send(state) is False


def test_should_not_auto_send_policy_match():
    """Policy match pre-loaded in state → Gate 1 escalates → no auto-send."""
    state = make_state(
        intent="FAQ",
        sentiment="neutral",
        customer_message="How do I cancel?",
        intent_confidence=0.95,
        draft_confidence=0.95,
        policy_matches=["ACME 5.1"],
        risk_flags=[],
    )
    assert should_auto_send(state) is False


# ---------------------------------------------------------------------------
# Gate ordering: Gate 2 only runs if Gate 1 passes
# ---------------------------------------------------------------------------

def test_gate2_not_run_when_gate1_fails():
    """
    When Gate 1 fails (risk detected), should_auto_send is False regardless
    of confidence scores — Gate 2 is never consulted.
    """
    # Intent=refund triggers Gate 1. Confidences are deliberately high.
    state = make_state(
        intent="refund",
        sentiment="neutral",
        customer_message="I want a refund for $500.",
        intent_confidence=0.99,
        draft_confidence=0.99,
        risk_flags=[],
        policy_matches=[],
    )
    # Gate 1 fails → auto-send must be False even with perfect confidence
    assert should_auto_send(state) is False
    # And Gate 1 must have run (risk_flags populated)
    assert len(state["risk_flags"]) > 0
