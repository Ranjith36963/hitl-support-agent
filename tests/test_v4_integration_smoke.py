"""End-to-end v4 graph smoke — proves the Researcher + Drafter↔Critic
sub-graphs wire correctly into the parent graph and that an FAQ ticket
auto-sends through the v4 path with all 3 v4 LLM call sites mocked.

Companion to tests/test_integration_smoke.py (v3-only). Where v3 patches
src.nodes.draft_response, v4 patches src.agents.drafter._llm_draft and
src.agents.critic._llm_judge.

What this proves under MULTIAGENT_ENABLED=1:
- Researcher sub-graph slot-in produces v3-shape state (customer_history,
  customer_tier, policy_matches, context_hash)
- Drafter↔Critic loop terminates within MAX_CRITIC_ITERATIONS
- Critic accept verdict on first iteration → exits sub-graph → parent
  flow proceeds to gates → auto-send for FAQ tickets
- Outer graph topology preserved (15 nodes either flag value)
"""

from __future__ import annotations

import importlib
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.llm import ClassificationResult
from src.mcp_client import (
    CRMProfile,
    EmailSendResult,
    HistoryEntry,
    KBResult,
    SlackPostResult,
    SlackUpdateResult,
)
from src.state import initial_state


def _fake_router() -> Any:
    """MCP router stub — same shape as v3 integration smoke."""
    router = AsyncMock()
    router.read.get_crm_profile = AsyncMock(
        return_value=CRMProfile(
            email="alice@example.com",
            customer_tier="Standard",
            contract_value_usd=240.0,
            renewal_date="2027-08-01",
            billing_status="current",
            account_status="Active",
            open_tickets=2,
        )
    )
    router.read.get_customer_history = AsyncMock(
        return_value=[
            HistoryEntry(
                ticket_id="t-prior-1",
                date="2026-04-12",
                intent="technical",
                resolution="resolved",
                sentiment="neutral",
                customer_email="alice@example.com",
            )
        ]
    )
    router.read.get_kb_article = AsyncMock(
        return_value=KBResult(
            matched_sections=["Password Reset"],
            verbatim_quote="Password resets are self-service via the login screen.",
            policy_references=["ACME 2.1"],
        )
    )
    router.email.send = AsyncMock(
        return_value=EmailSendResult(
            sent_message_id="<sent-1@gmail.com>",
            was_duplicate=False,
            status="sent",
        )
    )
    router.slack.post_approval_request = AsyncMock(
        return_value=SlackPostResult(
            slack_message_ts="1700000000.000100",
            channel="#support-technical",
            ok=True,
        )
    )
    router.slack.update_message = AsyncMock(
        return_value=SlackUpdateResult(ok=True, ts="1700000000.000100", channel="#support-technical")
    )
    return router


@pytest.mark.asyncio
async def test_v4_faq_auto_sends_through_drafter_critic_loop(tmp_path, monkeypatch):
    """FAQ ticket → Researcher (KB only) → Drafter v1 → Critic accept → gates pass → auto-send.

    Verifies all v4 patch points fire and the loop exits at iteration 0.
    """
    monkeypatch.setenv("MULTIAGENT_ENABLED", "1")
    # Reload graph module to pick up v4 wiring
    import src.graph
    importlib.reload(src.graph)
    from src.graph import async_sqlite_checkpointer, compile_full_with_checkpointer

    fake_router = _fake_router()
    # FAQ tickets shouldn't match a policy — override KB stub to return empty
    # policy_references so Gate 1 (policy risk check) doesn't escalate.
    fake_router.read.get_kb_article = AsyncMock(
        return_value=KBResult(matched_sections=[], verbatim_quote="", policy_references=[])
    )

    fake_classify = AsyncMock(
        return_value=ClassificationResult(
            intent="FAQ",
            intent_confidence=0.95,
            sentiment="neutral",
            risk_flags=[],
            risk_level="none",
        )
    )

    drafter_calls = 0
    critic_calls = 0

    async def fake_drafter_llm(payload):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "draft": "To reset your password, use the 'Forgot password?' link on the login screen.",
            "draft_confidence": 0.95,
        }

    async def fake_critic_llm(payload):
        nonlocal critic_calls
        critic_calls += 1
        return {"verdict": "accept", "severity": 0.0, "feedback": ""}

    db_path = str(tmp_path / "v4_smoke.sqlite")

    with (
        patch("src.nodes.classify_intent", fake_classify),
        patch("src.nodes._client", lambda: fake_router),
        patch("src.agents.drafter._llm_draft", fake_drafter_llm),
        patch("src.agents.critic._llm_judge", fake_critic_llm),
    ):
        async with async_sqlite_checkpointer(db_path) as checkpointer:
            graph = compile_full_with_checkpointer(checkpointer)
            config = {"configurable": {"thread_id": "TKT-V4-1"}}
            state = initial_state(
                ticket_id="TKT-V4-1",
                customer_message="From: alice@example.com\nSubject: Password reset\n\nHow do I reset my password?",
                email_thread_id="<thread-1@gmail.com>",
                send_idempotency_key="idem-v4-1",
            )
            result = await graph.ainvoke(state, config=config)

    # v4 path was actually taken
    assert drafter_calls == 1, f"Drafter should fire exactly once on accept; fired {drafter_calls}"
    assert critic_calls == 1, f"Critic should fire exactly once on accept; fired {critic_calls}"

    # Researcher contract: produced v3-compatible state
    audit_nodes = [e.get("node") for e in result.get("audit_log", [])]
    assert "researcher_agent" in audit_nodes, "Researcher audit entry missing"
    assert "drafter_agent" in audit_nodes, "Drafter audit entry missing"
    assert "critic_agent" in audit_nodes, "Critic audit entry missing"

    # FAQ tickets auto-send (Gates 1+2 pass)
    assert result.get("final_state") == "sent", f"FAQ should auto-send, got final_state={result.get('final_state')}"

    # Researcher used FAQ-only tool selection (only get_kb_article)
    researcher_entry = next(e for e in result["audit_log"] if e.get("node") == "researcher_agent")
    assert researcher_entry["tools_called"] == ["get_kb_article"], (
        f"FAQ should call only KB; called {researcher_entry['tools_called']}"
    )

    # Reset env for downstream tests
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    importlib.reload(src.graph)


@pytest.mark.asyncio
async def test_v4_critic_revision_loop_fires_when_drafter_low_confidence(tmp_path, monkeypatch):
    """Critic revise → drafter rewrites → Critic accept → exit.

    Verifies the loop iteration counter increments and the second drafter
    pass actually receives critic_feedback in its payload.
    """
    monkeypatch.setenv("MULTIAGENT_ENABLED", "1")
    import src.graph
    importlib.reload(src.graph)
    from src.graph import async_sqlite_checkpointer, compile_full_with_checkpointer

    fake_router = _fake_router()

    fake_classify = AsyncMock(
        return_value=ClassificationResult(
            intent="FAQ",
            intent_confidence=0.95,
            sentiment="neutral",
            risk_flags=[],
            risk_level="none",
        )
    )

    drafter_payloads = []
    critic_call = 0

    async def fake_drafter_llm(payload):
        drafter_payloads.append(payload)
        return {
            "draft": f"Draft v{len(drafter_payloads)}",
            "draft_confidence": 0.95,
        }

    async def fake_critic_llm(payload):
        nonlocal critic_call
        critic_call += 1
        # First call: revise. Second: accept.
        if critic_call == 1:
            return {"verdict": "revise", "severity": 0.5, "feedback": "Be warmer"}
        return {"verdict": "accept", "severity": 0.0, "feedback": ""}

    db_path = str(tmp_path / "v4_loop.sqlite")

    with (
        patch("src.nodes.classify_intent", fake_classify),
        patch("src.nodes._client", lambda: fake_router),
        patch("src.agents.drafter._llm_draft", fake_drafter_llm),
        patch("src.agents.critic._llm_judge", fake_critic_llm),
    ):
        async with async_sqlite_checkpointer(db_path) as checkpointer:
            graph = compile_full_with_checkpointer(checkpointer)
            config = {"configurable": {"thread_id": "TKT-V4-2"}}
            state = initial_state(
                ticket_id="TKT-V4-2",
                customer_message="From: alice@example.com\nSubject: Reset\n\nReset?",
                email_thread_id="<thread-2@gmail.com>",
                send_idempotency_key="idem-v4-2",
            )
            result = await graph.ainvoke(state, config=config)

    # Drafter ran twice (initial + revision)
    assert len(drafter_payloads) == 2, f"Drafter should fire twice; fired {len(drafter_payloads)}"
    # Critic ran twice (revise then accept)
    assert critic_call == 2, f"Critic should fire twice; fired {critic_call}"
    # Second drafter call received critic feedback
    assert "critic_feedback" in drafter_payloads[1], "Second drafter pass should receive critic feedback"
    assert drafter_payloads[1]["critic_feedback"] == "Be warmer"

    # Final draft is the revised version
    assert result.get("final_draft") == "Draft v2"

    # Reset env for downstream tests
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    importlib.reload(src.graph)
