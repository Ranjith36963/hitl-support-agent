"""Critic invariants — these tests MUST pass or v4 is unsafe.

The Critic can ONLY:
  - Read the current draft + retrieved policy quotes
  - Emit verdict ∈ {accept, revise} + feedback + severity ∈ [0, 1]
  - Adjust draft_confidence as: draft_confidence *= (1 - severity * 0.5)

The Critic CANNOT:
  - Set approval_status, slack_channel, slack_message_ts
  - Decide auto_send
  - Modify Gate 1 / Gate 2 thresholds
  - Mutate prior audit_log entries
  - Bypass interrupt_gate
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.critic import critic_node


@pytest.mark.asyncio
async def test_critic_only_writes_allowed_fields():
    """Critic output dict may only contain a fixed allow-list of keys."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample draft",
        "draft_confidence": 0.92,
        "policy_matches": ["ACME 4.2.1"],
        "_critic_iteration": 0,
        "audit_log": [],
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept",
        "severity": 0.0,
        "feedback": "",
    })):
        result = await critic_node(state)

    allowed = {
        "draft_confidence", "_critic_verdict", "_critic_feedback",
        "_critic_severity", "_critic_iteration", "audit_log",
    }
    forbidden_in_result = set(result.keys()) - allowed
    assert not forbidden_in_result, f"Critic wrote forbidden fields: {forbidden_in_result}"


@pytest.mark.asyncio
async def test_critic_cannot_set_auto_send():
    """Critic must not write approval_status='auto' or any send-related fields."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.5,
        "audit_log": [],
        "_critic_iteration": 0,
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept", "severity": 0.0, "feedback": "",
    })):
        result = await critic_node(state)
    assert "approval_status" not in result
    assert "send_status" not in result
    assert "sent_message_id" not in result
    assert "slack_channel" not in result


@pytest.mark.asyncio
async def test_critic_severity_only_decreases_confidence_never_increases():
    """Critic severity must MULTIPLY draft_confidence by (1 - severity*0.5).
    A high-severity verdict can lower confidence; nothing can raise it."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.9,
        "_critic_iteration": 0,
        "audit_log": [],
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "revise", "severity": 0.8, "feedback": "Tone off",
    })):
        result = await critic_node(state)
    assert result["draft_confidence"] == pytest.approx(0.9 * (1 - 0.8 * 0.5))
    assert result["draft_confidence"] < 0.9


@pytest.mark.asyncio
async def test_critic_appends_audit_log_does_not_mutate():
    """Critic must APPEND to audit_log, never mutate prior entries."""
    prior = [{"node": "researcher_agent", "ts": "2026-01-01T00:00:00Z"}]
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.9,
        "_critic_iteration": 0,
        "audit_log": prior.copy(),
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept", "severity": 0.0, "feedback": "",
    })):
        result = await critic_node(state)
    new_log = result["audit_log"]
    assert new_log[0] == prior[0], "Prior audit entry was mutated"
    assert len(new_log) == len(prior) + 1
    assert new_log[-1]["node"] == "critic_agent"


@pytest.mark.asyncio
async def test_critic_clamps_severity_to_valid_range():
    """Bad LLM output (severity > 1.0 or < 0.0) must be clamped, not crash."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 1.0,
        "_critic_iteration": 0,
        "audit_log": [],
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "revise", "severity": 5.0, "feedback": "test",
    })):
        result = await critic_node(state)
    # severity clamped to 1.0 → confidence becomes 1.0 * (1 - 1.0 * 0.5) = 0.5
    assert result["_critic_severity"] == 1.0
    assert result["draft_confidence"] == pytest.approx(0.5)
