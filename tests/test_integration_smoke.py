"""End-to-end graph smoke test — proves the full graph wires correctly.

External dependencies (LLM + MCP servers + Slack + Gmail) are mocked so this
test runs anywhere without secrets. The real end-to-end test happens with the
demo recording and Phase 4 eval — when GMAIL_USER / SLACK_BOT_TOKEN /
OPENROUTER_API_KEY are provisioned.

What this proves:
  - 15 nodes wire into a graph with no missing edges
  - Pydantic API contracts between nodes.py and mcp_client align
  - Two-gate routing reaches escalate vs auto-send paths
  - Interrupt fires at the dedicated interrupt_gate node (Slack post first)
  - Command(resume="approve") completes the ticket without re-running side effects
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# These tests patch v3 LLM call sites (src.nodes.classify_intent and
# src.nodes.draft_response). Under MULTIAGENT_ENABLED=1, draft_response_node
# is replaced by the Drafter sub-graph which calls src.agents.drafter._llm_draft
# and src.agents.critic._llm_judge — neither covered by these patches. v4 has
# its own integration smoke (tests/test_v4_integration_smoke.py).
pytestmark = pytest.mark.skipif(
    os.environ.get("MULTIAGENT_ENABLED", "0") == "1",
    reason="v3-only smoke tests; v4 path tested in test_v4_integration_smoke.py",
)
from langgraph.types import Command

from src.graph import async_sqlite_checkpointer, compile_full_with_checkpointer
from src.llm import ClassificationResult, DraftResult
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
    """Build a fake MCPClientRouter with all 7 tools stubbed."""
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
            matched_sections=["4.2.1"],
            verbatim_quote="Refunds $100-$500 require Tier-1 agent approval per ACME 4.2.1.",
            policy_references=["ACME 4.2.1"],
        )
    )
    router.slack.post_approval_request = AsyncMock(
        return_value=SlackPostResult(
            slack_message_ts="1234567890.000200", channel="#support-refunds", ok=True
        )
    )
    router.slack.update_message = AsyncMock(
        return_value=SlackUpdateResult(ok=True, ts="1234567890.000200", channel="#support-refunds")
    )
    router.email.send = AsyncMock(
        return_value=EmailSendResult(
            sent_message_id="<sent-001@example.com>", was_duplicate=False, status="sent"
        )
    )
    return router


@pytest.mark.asyncio
async def test_full_graph_refund_escalates_to_slack_then_resumes(tmp_path: Any) -> None:
    """A refund ticket: Gate 1 fails on policy match → channel router →
    slack_notification → interrupt_gate. Resume with 'approve' completes."""
    db_path = str(tmp_path / "smoke.sqlite")
    config = {"configurable": {"thread_id": "ticket-smoke-001"}}

    state = initial_state(
        ticket_id="ticket-smoke-001",
        customer_message=(
            "From: alice@example.com\nSubject: Refund please\n\n"
            "I want a $200 refund — your shirt didn't fit."
        ),
        email_thread_id="<orig-msg-001@example.com>",
        send_idempotency_key="idem-smoke-001",
    )

    fake_router = _fake_router()
    fake_classify = AsyncMock(
        return_value=ClassificationResult(
            intent="refund",
            intent_confidence=0.92,
            sentiment="neutral",
            risk_flags=["refund"],
            risk_level="financial",
        )
    )
    fake_draft = AsyncMock(
        return_value=DraftResult(
            draft="Hi Alice, I've initiated your $200 refund per ACME 4.2.1.",
            draft_confidence=0.91,
        )
    )

    async with async_sqlite_checkpointer(db_path) as cp:
        graph = compile_full_with_checkpointer(cp)

        with (
            patch("src.nodes.classify_intent", fake_classify),
            patch("src.nodes.draft_response", fake_draft),
            patch("src.nodes._client", lambda: fake_router),
        ):
            # Phase 1: graph runs to interrupt
            chunks: list[dict[str, Any]] = []
            async for c in graph.astream(state, config):
                chunks.append(c)

            assert any("__interrupt__" in c for c in chunks), (
                f"Graph never paused. Chunks: {[list(c.keys()) for c in chunks]}"
            )

            snap = await graph.aget_state(config)
            # Slack message_ts should be saved BEFORE interrupt — Implementation Rule 1
            assert snap.values["slack_message_ts"] == "1234567890.000200"
            assert snap.values["approval_status"] == "pending"
            assert snap.values["intent"] == "refund"
            assert "refund" in snap.values["risk_flags"]
            # Verify Slack post happened exactly once before interrupt
            assert fake_router.slack.post_approval_request.await_count == 1

            # Phase 2: human approves
            async for _ in graph.astream(
                Command(resume={"action": "approve", "approver_id": "U_SARAH"}), config
            ):
                pass

            final = await graph.aget_state(config)
            assert final.values["final_state"] == "sent"
            assert final.values["sent_message_id"] == "<sent-001@example.com>"
            # Slack post still exactly once — Implementation Rule 1 holds
            # (the interrupt restart did not duplicate the post)
            assert fake_router.slack.post_approval_request.await_count == 1
            # Email sent exactly once via the idempotent path
            assert fake_router.email.send.await_count == 1
            # Final Slack update happened (audit close)
            assert fake_router.slack.update_message.await_count >= 1


@pytest.mark.asyncio
async def test_full_graph_durable_resume_across_async_restart(tmp_path: Any) -> None:
    """Durability on the production async path.

    The skeleton resume test (`test_resume.py`) covers `SqliteSaver` + sync
    dummy nodes. The production graph uses `AsyncSqliteSaver` + async nodes
    — different code path, different reconnect semantics. This test simulates
    a process restart on THAT path:

      1. Open AsyncSqliteSaver, run the graph until it interrupts at the
         Slack notification → interrupt_gate boundary.
      2. Exit the async-with (closes the connection — simulated process death).
         Only the SQLite file on disk remains.
      3. Re-open a NEW AsyncSqliteSaver against the same file, resume with
         Command(resume="approve").
      4. Assert the ticket completes and side effects (Slack post, email)
         happened EXACTLY ONCE despite the restart — Implementation Rule 1.

    If this test goes red, Demo 1 (durable execution kill-and-restart) is at
    risk. The whole differentiator #12 in the README depends on this contract.
    """
    db_path = str(tmp_path / "durable_async.sqlite")
    thread_id = "ticket-durable-async-001"
    config = {"configurable": {"thread_id": thread_id}}

    state = initial_state(
        ticket_id=thread_id,
        customer_message=(
            "From: alice@example.com\nSubject: Refund please\n\n"
            "I want a $200 refund — your shirt didn't fit."
        ),
        email_thread_id="<orig-msg-durable@example.com>",
        send_idempotency_key="idem-durable-001",
    )

    fake_router = _fake_router()
    fake_classify = AsyncMock(
        return_value=ClassificationResult(
            intent="refund",
            intent_confidence=0.92,
            sentiment="neutral",
            risk_flags=["refund"],
            risk_level="financial",
        )
    )
    fake_draft = AsyncMock(
        return_value=DraftResult(
            draft="Hi Alice, I've initiated your $200 refund per ACME 4.2.1.",
            draft_confidence=0.91,
        )
    )

    # ---- Process #1: run to interrupt, then drop everything. ----
    async with async_sqlite_checkpointer(db_path) as cp1:
        graph1 = compile_full_with_checkpointer(cp1)
        with (
            patch("src.nodes.classify_intent", fake_classify),
            patch("src.nodes.draft_response", fake_draft),
            patch("src.nodes._client", lambda: fake_router),
        ):
            chunks: list[dict[str, Any]] = []
            async for c in graph1.astream(state, config):
                chunks.append(c)
            assert any("__interrupt__" in c for c in chunks), (
                "Graph #1 did not interrupt — async checkpointer write may have failed"
            )
            snap1 = await graph1.aget_state(config)
            assert snap1.values["slack_message_ts"] == "1234567890.000200"
            assert snap1.values["approval_status"] == "pending"

    # AsyncSqliteSaver context exited → connection closed. SQLite file on
    # disk is the only thing that survived.
    import os
    assert os.path.exists(db_path), "Async checkpoint database disappeared"

    # ---- Process #2: rebuild against the same file, resume. ----
    # (LLM mocks reset to fresh AsyncMocks — proves nothing leaks via Python state.)
    fresh_router = _fake_router()
    fresh_classify = AsyncMock()  # would raise if called — graph should NOT re-run pre-interrupt nodes
    fresh_draft = AsyncMock()

    async with async_sqlite_checkpointer(db_path) as cp2:
        graph2 = compile_full_with_checkpointer(cp2)

        # Pre-resume sanity: state is recoverable from disk.
        recovered = await graph2.aget_state(config)
        assert recovered.values["ticket_id"] == thread_id
        # customer_message was PII-redacted in process #1 — recovered form
        # contains the [EMAIL_*] token, NOT the original email. The token map
        # rides along inside the audit_log entry for the pii_redact node.
        assert "Subject: Refund please" in recovered.values["customer_message"]
        assert "[EMAIL_" in recovered.values["customer_message"], (
            "PII redaction state was not checkpointed — durability incomplete"
        )
        assert recovered.values["slack_message_ts"] == "1234567890.000200"
        assert recovered.values["intent"] == "refund"

        with (
            patch("src.nodes.classify_intent", fresh_classify),
            patch("src.nodes.draft_response", fresh_draft),
            patch("src.nodes._client", lambda: fresh_router),
        ):
            async for _ in graph2.astream(
                Command(resume={"action": "approve", "approver_id": "U_DURABLE"}), config
            ):
                pass

            final = await graph2.aget_state(config)
            assert final.values["final_state"] == "sent", (
                f"Graph did not complete after async durable resume. State: {final.values}"
            )
            assert final.values["sent_message_id"] == "<sent-001@example.com>"

            # Implementation Rule 1: side effects from the FIRST process were
            # checkpointed and NOT replayed in the second process. The fresh
            # router was only invoked for post-interrupt nodes (Send + Audit).
            assert fresh_classify.await_count == 0, (
                "classify_intent re-ran after restart — pre-interrupt nodes should not replay"
            )
            assert fresh_draft.await_count == 0, (
                "draft_response re-ran after restart — pre-interrupt nodes should not replay"
            )
            assert fresh_router.slack.post_approval_request.await_count == 0, (
                "Slack post duplicated on restart — Implementation Rule 1 broken"
            )
            # Send (post-interrupt) DID run on graph2.
            assert fresh_router.email.send.await_count == 1
            # And the original graph1's mock saw exactly one Slack post
            # (proving the post happened on the FIRST process, not duplicated).
            assert fake_router.slack.post_approval_request.await_count == 1


@pytest.mark.asyncio
async def test_full_graph_faq_auto_sends(tmp_path: Any) -> None:
    """FAQ ticket with high confidence: should_auto_send → skip Slack entirely."""
    db_path = str(tmp_path / "smoke_auto.sqlite")
    config = {"configurable": {"thread_id": "ticket-smoke-faq"}}

    state = initial_state(
        ticket_id="ticket-smoke-faq",
        customer_message=(
            "From: bob@example.com\nSubject: How do I reset my password?\n\n"
            "I forgot my password, how do I reset it?"
        ),
        email_thread_id="<orig-faq-001@example.com>",
        send_idempotency_key="idem-faq-001",
    )

    fake_router = _fake_router()
    # FAQ policy lookup should NOT match — re-stub to no policy match
    fake_router.read.get_kb_article = AsyncMock(
        return_value=KBResult(matched_sections=[], verbatim_quote="", policy_references=[])
    )
    fake_classify = AsyncMock(
        return_value=ClassificationResult(
            intent="FAQ",
            intent_confidence=0.97,
            sentiment="neutral",
            risk_flags=[],
            risk_level="none",
        )
    )
    fake_draft = AsyncMock(
        return_value=DraftResult(
            draft="Hi Bob, click 'Forgot Password' on the login page.",
            draft_confidence=0.95,
        )
    )

    async with async_sqlite_checkpointer(db_path) as cp:
        graph = compile_full_with_checkpointer(cp)
        with (
            patch("src.nodes.classify_intent", fake_classify),
            patch("src.nodes.draft_response", fake_draft),
            patch("src.nodes._client", lambda: fake_router),
        ):
            async for _ in graph.astream(state, config):
                pass

            final = await graph.aget_state(config)
            assert final.values["final_state"] == "sent"
            assert final.values["approval_status"] == "auto"
            # No Slack post for auto-send
            assert fake_router.slack.post_approval_request.await_count == 0
            # Email sent
            assert fake_router.email.send.await_count == 1
