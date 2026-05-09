"""Drafter ↔ Critic loop: bounded, terminates within 2 iterations,
respects critic verdict, preserves v3 state contract."""
from unittest.mock import patch

import pytest

from src.agents.drafter import build_drafter_subgraph


@pytest.mark.asyncio
async def test_loop_terminates_on_accept_first_iteration():
    """If Critic accepts at iteration 0, exit immediately — no second draft."""
    drafter_calls = 0
    critic_calls = 0

    async def fake_draft(state):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "original_draft": state.get("original_draft") or "draft v1",
            "final_draft": "draft v1",
            "draft_confidence": 0.95,
            "audit_log": (state.get("audit_log") or []) + [{"node": "drafter_agent"}],
        }

    async def fake_critic(state):
        nonlocal critic_calls
        critic_calls += 1
        return {
            "draft_confidence": 0.95,
            "_critic_verdict": "accept",
            "_critic_severity": 0.0,
            "_critic_iteration": state.get("_critic_iteration", 0),
            "audit_log": (state.get("audit_log") or []) + [{"node": "critic_agent"}],
        }

    with patch("src.agents.drafter.drafter_node", side_effect=fake_draft), \
         patch("src.agents.drafter.critic_node", side_effect=fake_critic):
        subgraph = build_drafter_subgraph()
        state = {"ticket_id": "T1", "intent": "FAQ", "audit_log": []}
        result = await subgraph.ainvoke(state)

    assert drafter_calls == 1
    assert critic_calls == 1
    assert result["final_draft"] == "draft v1"


@pytest.mark.asyncio
async def test_loop_caps_at_two_iterations_even_when_critic_keeps_revising():
    """Critic returns 'revise' forever; loop must still exit at iteration cap."""
    drafter_calls = 0
    critic_calls = 0

    async def fake_draft(state):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "original_draft": state.get("original_draft") or f"draft {drafter_calls}",
            "final_draft": f"draft {drafter_calls}",
            "draft_confidence": 0.7,
            "audit_log": state.get("audit_log") or [],
        }

    async def fake_critic(state):
        nonlocal critic_calls
        critic_calls += 1
        return {
            "draft_confidence": 0.5,
            "_critic_verdict": "revise",
            "_critic_severity": 0.6,
            "_critic_iteration": state.get("_critic_iteration", 0),
            "audit_log": state.get("audit_log") or [],
        }

    with patch("src.agents.drafter.drafter_node", side_effect=fake_draft), \
         patch("src.agents.drafter.critic_node", side_effect=fake_critic):
        subgraph = build_drafter_subgraph()
        state = {"ticket_id": "T1", "intent": "refund", "audit_log": []}
        await subgraph.ainvoke(state)

    # Hard cap is MAX_CRITIC_ITERATIONS=2: at most 2 drafts, 2 critic runs
    assert drafter_calls <= 2, f"drafter called {drafter_calls} times — cap broken"
    assert critic_calls <= 2, f"critic called {critic_calls} times — cap broken"


@pytest.mark.asyncio
async def test_loop_respects_critic_revision_when_under_cap():
    """If Critic asks for one revision, drafter must run twice."""
    drafter_calls = 0
    critic_calls = 0

    async def fake_draft(state):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "original_draft": state.get("original_draft") or "v1",
            "final_draft": f"v{drafter_calls}",
            "draft_confidence": 0.8,
            "audit_log": state.get("audit_log") or [],
        }

    async def fake_critic(state):
        nonlocal critic_calls
        critic_calls += 1
        # First call: revise. Second call: accept.
        if critic_calls == 1:
            return {
                "draft_confidence": 0.6,
                "_critic_verdict": "revise",
                "_critic_feedback": "tighten",
                "_critic_severity": 0.5,
                "_critic_iteration": 0,
                "audit_log": state.get("audit_log") or [],
            }
        return {
            "draft_confidence": 0.85,
            "_critic_verdict": "accept",
            "_critic_severity": 0.0,
            "_critic_iteration": 1,
            "audit_log": state.get("audit_log") or [],
        }

    with patch("src.agents.drafter.drafter_node", side_effect=fake_draft), \
         patch("src.agents.drafter.critic_node", side_effect=fake_critic):
        subgraph = build_drafter_subgraph()
        state = {"ticket_id": "T1", "intent": "refund", "audit_log": []}
        result = await subgraph.ainvoke(state)

    assert drafter_calls == 2
    assert critic_calls == 2
    assert result["final_draft"] == "v2"
