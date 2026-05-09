"""Researcher Agent: deterministic intent → MCP Read tool selection.

Tests use mocked tools so they're fast and deterministic. The real
MCP integration is exercised by tests/test_v4_integration.py.

Per docs/v4_multiagent.md the Researcher's milestone-1 form is
deterministic intent-based selection (not full ReAct) — same trace
narrative at lower cost + zero risk of agent loops.
"""
from unittest.mock import AsyncMock

import pytest

from src.agents.researcher import build_researcher_subgraph
from src.mcp_client import CRMProfile, HistoryEntry, KBResult


@pytest.mark.asyncio
async def test_researcher_returns_v3_compatible_state(monkeypatch):
    """Researcher must return the same state shape v3 enrich_context_node returns."""
    fake_router = AsyncMock()
    fake_router.read.get_crm_profile = AsyncMock(
        return_value=CRMProfile(email="alice@example.com", customer_tier="Enterprise")
    )
    fake_router.read.get_customer_history = AsyncMock(
        return_value=[HistoryEntry(ticket_id="t1", date="2026-01-01")]
    )
    fake_router.read.get_kb_article = AsyncMock(
        return_value=KBResult(
            verbatim_quote="Refunds above $100 require approval per Policy 4.2.1",
            policy_references=["ACME 4.2.1"],
        )
    )
    monkeypatch.setattr("src.agents.researcher._client", lambda: fake_router)

    subgraph = build_researcher_subgraph()
    state = {
        "ticket_id": "TKT-1",
        "customer_message": "I want a $200 refund",
        "intent": "refund",
        "audit_log": [
            {"node": "pii_redact", "token_map": {"[EMAIL_1]": "alice@example.com"}}
        ],
    }
    result = await subgraph.ainvoke(state)

    assert "customer_history" in result
    assert "customer_tier" in result
    assert "policy_matches" in result
    assert "context_hash" in result
    assert result["customer_tier"] == "Enterprise"
    assert "ACME 4.2.1" in result["policy_matches"]
    assert any(e.get("node") == "researcher_agent" for e in result.get("audit_log", []))


@pytest.mark.asyncio
async def test_researcher_skips_history_for_faq_intent(monkeypatch):
    """For FAQ tickets, researcher should NOT call get_customer_history."""
    fake_router = AsyncMock()
    fake_router.read.get_crm_profile = AsyncMock(
        return_value=CRMProfile(email="bob@example.com")
    )
    fake_router.read.get_customer_history = AsyncMock(return_value=[])
    fake_router.read.get_kb_article = AsyncMock(
        return_value=KBResult(verbatim_quote="Password reset policy", policy_references=[])
    )
    monkeypatch.setattr("src.agents.researcher._client", lambda: fake_router)

    subgraph = build_researcher_subgraph()
    state = {
        "ticket_id": "TKT-2",
        "customer_message": "How do I reset my password?",
        "intent": "FAQ",
        "audit_log": [{"node": "pii_redact", "token_map": {"[EMAIL_1]": "bob@example.com"}}],
    }
    await subgraph.ainvoke(state)

    fake_router.read.get_kb_article.assert_called_once()
    fake_router.read.get_crm_profile.assert_not_called()
    fake_router.read.get_customer_history.assert_not_called()


@pytest.mark.asyncio
async def test_researcher_calls_all_three_for_refund(monkeypatch):
    """Refund intent must call profile + history + KB."""
    fake_router = AsyncMock()
    fake_router.read.get_crm_profile = AsyncMock(
        return_value=CRMProfile(email="c@example.com", customer_tier="SMB")
    )
    fake_router.read.get_customer_history = AsyncMock(return_value=[])
    fake_router.read.get_kb_article = AsyncMock(
        return_value=KBResult(verbatim_quote="Refund policy", policy_references=["P1"])
    )
    monkeypatch.setattr("src.agents.researcher._client", lambda: fake_router)

    subgraph = build_researcher_subgraph()
    state = {
        "ticket_id": "TKT-3",
        "customer_message": "$50 refund please",
        "intent": "refund",
        "audit_log": [{"node": "pii_redact", "token_map": {"[EMAIL_1]": "c@example.com"}}],
    }
    await subgraph.ainvoke(state)

    fake_router.read.get_crm_profile.assert_called_once()
    fake_router.read.get_customer_history.assert_called_once()
    fake_router.read.get_kb_article.assert_called_once()
