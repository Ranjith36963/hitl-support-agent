"""LangGraph workflow with SQLite checkpointer for durable execution.

Phase 1 (this file): minimal skeleton that proves the durability contract —
a pre-interrupt node, a dedicated interrupt_gate node, and a post-resume node.
The interrupt_gate calls interrupt() and ONLY interrupt() — Implementation
Rule 1 from CLAUDE.md and spec.md §6.5.

Phase 2 will replace the dummy nodes with the real graph (Email Listener →
PII Redact → Classify → Enrich → Draft → Policy/Confidence Gates → Channel
Router → Slack Notification → Interrupt Gate → action handlers → Finalize →
Send → Audit).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.state import AgentState

# ---------------------------------------------------------------------------
# Phase 1 skeleton nodes — placeholders that prove the durability contract.
# They will be replaced wholesale in Phase 2 by the real node functions in
# src/nodes.py. Keeping them here for now keeps graph.py runnable on its own
# so the resume test can exercise the contract before any business logic ships.
# ---------------------------------------------------------------------------


def pre_interrupt_node(state: AgentState) -> dict[str, Any]:
    """Stand-in for the real Slack Notification node.

    Writes a marker to state so the resume test can verify side effects from
    BEFORE the interrupt are preserved across a process restart.
    """
    return {
        "slack_channel": "#support-refunds",
        "slack_message_ts": "1234567890.000100",
        "approval_status": "pending",
    }


def interrupt_gate(state: AgentState) -> dict[str, Any]:
    """Dedicated interrupt node — Implementation Rule 1.

    NOTHING ELSE may live in this node. No DB writes, no MCP calls, no audit
    log entries, no try/except. On resume the node restarts from the top, so
    any pre-interrupt side effects would duplicate.
    """
    action = interrupt(
        {
            "ticket_id": state.get("ticket_id", ""),
            "slack_channel": state.get("slack_channel", ""),
            "slack_message_ts": state.get("slack_message_ts", ""),
            "draft": state.get("original_draft", ""),
            "prompt": "Approve / Edit / Reject?",
        }
    )
    return {"approval_status": str(action)}


def post_resume_node(state: AgentState) -> dict[str, Any]:
    """Stand-in for Finalize → Send → Audit. Writes a terminal marker."""
    approval = state.get("approval_status", "")
    return {
        "final_state": "sent" if approval == "approve" else f"completed_{approval}",
        "send_status": "sent",
    }


# ---------------------------------------------------------------------------
# Graph builder + checkpointer factory
# ---------------------------------------------------------------------------


def build_graph_builder() -> StateGraph[AgentState]:
    """Construct the LangGraph builder. Compile is left to the caller so the
    same builder can be compiled with different checkpointers (in-memory for
    unit tests, SQLite for production and durability tests)."""
    builder = StateGraph(AgentState)
    builder.add_node("pre_interrupt", pre_interrupt_node)
    builder.add_node("interrupt_gate", interrupt_gate)
    builder.add_node("post_resume", post_resume_node)

    builder.add_edge(START, "pre_interrupt")
    builder.add_edge("pre_interrupt", "interrupt_gate")
    builder.add_edge("interrupt_gate", "post_resume")
    builder.add_edge("post_resume", END)
    return builder


@contextmanager
def sqlite_checkpointer(db_path: str) -> Iterator[SqliteSaver]:
    """Open a SqliteSaver against `db_path`. Caller-owned connection so the
    test can re-open the same file after a simulated process restart.

    `check_same_thread=False` is required because LangGraph may execute nodes
    on a different thread than the one that opened the connection.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def compile_with_checkpointer(checkpointer: SqliteSaver) -> Any:
    """Compile the SKELETON graph (Phase 1 contract test). Use
    `compile_full_with_checkpointer` for the production graph."""
    return build_graph_builder().compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Full production graph — wires every real node from src/nodes.py per the
# CLAUDE.md "Graph node order" line. Tracks B and C contracts (mcp_client,
# policy, slack_router, pii) are imported lazily inside nodes.py so this
# module is importable even before those tracks land — the nodes will fall
# back to placeholder stubs and any actual call to MCP fails loudly.
# ---------------------------------------------------------------------------


def build_full_graph_builder() -> StateGraph[AgentState]:
    """Construct the production graph per spec.md §6 + CLAUDE.md flow.

    Slack post BEFORE interrupt — Implementation Rule 1. interrupt_gate is a
    dedicated node containing only `interrupt()`. Reverse this and the graph
    pauses forever with no Slack message ever posted.
    """
    # Lazy import keeps the skeleton path independent of nodes.py's heavier
    # dependencies (openai, langsmith). If nodes.py isn't importable yet, the
    # skeleton resume test still works.
    from src.nodes import (
        audit_log_node,
        auto_send_marker_node,
        channel_router_node,
        classify_intent_node,
        draft_response_node,
        enrich_context_node,
        finalize_action_node,
        manual_queue_node,
        pii_redact_node,
        reject_increment_node,
        revalidate_context_node,
        route_after_action,
        route_after_draft,
        route_after_reject,
        route_after_revalidate,
        route_after_send,
        send_email_node,
        slack_notification_node,
        summarize_changes_node,
    )
    from src.nodes import (
        interrupt_gate as full_interrupt_gate,
    )

    builder = StateGraph(AgentState)

    # --- v4 multi-agent feature flag ---
    # MULTIAGENT_ENABLED=1 swaps in Researcher + Drafter↔Critic sub-graphs
    # in place of v3 enrich_context_node + draft_response_node.
    # Default 0 keeps v3 single-agent path intact. Toggle without code change.
    # See docs/v4_multiagent.md for the architecture lock + invariants.
    #
    # BOTH PATHS RETAINED INTENTIONALLY — do NOT delete the v3 path.
    # The v3-vs-v4 head-to-head IS the deliverable, not a stepping stone.
    #
    # History of this default:
    # - Through 2026-05-21: default = v3. The 10-ticket curated + 10-ticket
    #   Bitext evals tied on every safety metric, and v3 was ahead on
    #   response_quality. Default reflected the measured-best path.
    # - 2026-05-22: the 27-intent Bitext breadth eval ran. v3 produced 6
    #   dangerous false auto-sends (invoice queries, order tracking,
    #   change_shipping_address). v4 caught 5 of those 6, leaving only 1.
    #   See eval/bitext27_findings.md.
    # - 2026-05-22: 25-ticket adversarial red-team set ran. v3 = 18/25, v4 =
    #   21/25 — v4 catches 3 more classifier_trap cases. See
    #   eval/results_adversarial_v3.md vs _v4.md.
    # - 2026-05-23: default flipped to MULTIAGENT_ENABLED=1 (v4). The newer
    #   data shows v4 is materially safer on the harder distributions; the
    #   trade-offs (~2× cost per ticket, more conservative escalation on
    #   simple FAQs) are documented in METHODOLOGY.md and accepted.
    #
    # The Critic is structurally one-directional: it can only LOWER
    # draft_confidence, so v4 escalates >= v3 always. That is the mechanism
    # by which v4 catches v3's dangerous auto-sends; it is also why v4
    # over-escalates some benign FAQs.
    #
    # Keeping v3 costs almost nothing (two node functions); set
    # MULTIAGENT_ENABLED=0 to recover the baseline path for direct comparison.
    import os as _os
    _MULTIAGENT = _os.environ.get("MULTIAGENT_ENABLED", "1") == "1"

    # --- nodes ---
    builder.add_node("pii_redact", pii_redact_node)
    builder.add_node("classify_intent", classify_intent_node)
    if _MULTIAGENT:
        from src.agents.drafter import build_drafter_subgraph
        from src.agents.researcher import build_researcher_subgraph
        builder.add_node("enrich_context", build_researcher_subgraph())
        builder.add_node("draft_response", build_drafter_subgraph())
    else:
        builder.add_node("enrich_context", enrich_context_node)
        builder.add_node("draft_response", draft_response_node)
    builder.add_node("auto_send_marker", auto_send_marker_node)
    builder.add_node("channel_router", channel_router_node)
    builder.add_node("slack_notification", slack_notification_node)
    builder.add_node("interrupt_gate", full_interrupt_gate)
    builder.add_node("reject_increment", reject_increment_node)
    builder.add_node("revalidate_context", revalidate_context_node)
    builder.add_node("summarize_changes", summarize_changes_node)
    builder.add_node("finalize", finalize_action_node)
    builder.add_node("send_email", send_email_node)
    builder.add_node("audit_log", audit_log_node)
    builder.add_node("manual_queue", manual_queue_node)

    # --- linear pre-gate edges ---
    builder.add_edge(START, "pii_redact")
    builder.add_edge("pii_redact", "classify_intent")
    builder.add_edge("classify_intent", "enrich_context")
    builder.add_edge("enrich_context", "draft_response")

    # --- two-gate routing (combined into one conditional edge) ---
    builder.add_conditional_edges(
        "draft_response",
        route_after_draft,
        {
            "channel_router": "channel_router",
            "auto_send_marker": "auto_send_marker",
        },
    )

    # --- auto-send fast path ---
    builder.add_edge("auto_send_marker", "finalize")

    # --- escalate path: router → Slack post → DEDICATED interrupt ---
    builder.add_edge("channel_router", "slack_notification")
    builder.add_edge("slack_notification", "interrupt_gate")

    # --- after resume: action + elapsed-time decision in one conditional ---
    builder.add_conditional_edges(
        "interrupt_gate",
        route_after_action,
        {
            "reject_increment": "reject_increment",
            "finalize": "finalize",
            "revalidate_context": "revalidate_context",
        },
    )

    # --- reject path with 3-strike loop guard ---
    builder.add_conditional_edges(
        "reject_increment",
        route_after_reject,
        {
            "draft_response": "draft_response",
            "manual_queue": "manual_queue",
        },
    )

    # --- revalidate (slow path, > 15min pause) ---
    builder.add_conditional_edges(
        "revalidate_context",
        route_after_revalidate,
        {
            "summarize_changes": "summarize_changes",
            "finalize": "finalize",
        },
    )

    # --- summarize re-pauses by routing back through interrupt_gate ---
    builder.add_edge("summarize_changes", "interrupt_gate")

    # --- finalize → idempotent send → conditional retry / audit / manual ---
    builder.add_edge("finalize", "send_email")
    builder.add_conditional_edges(
        "send_email",
        route_after_send,
        {
            "audit_log": "audit_log",
            "send_email": "send_email",  # transient retry; idempotency check inside the node
            "manual_queue": "manual_queue",
        },
    )

    # --- terminal ---
    builder.add_edge("audit_log", END)
    builder.add_edge("manual_queue", END)

    return builder


def compile_full_with_checkpointer(checkpointer: Any) -> Any:
    """Compile the production graph against any checkpointer (sync SqliteSaver
    for skeleton/durability tests; AsyncSqliteSaver for the production path
    where nodes are async)."""
    return build_full_graph_builder().compile(checkpointer=checkpointer)


@asynccontextmanager
async def async_sqlite_checkpointer(db_path: str) -> AsyncIterator[AsyncSqliteSaver]:
    """Open an AsyncSqliteSaver against `db_path`. REQUIRED when the graph
    contains async nodes (the full production graph does — `client.read.x()`
    etc. are async tool calls).

    SqliteSaver is sync-only and raises NotImplementedError on `aput`
    invocations from async nodes. This is the long-lived service path.
    """
    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        yield checkpointer


__all__ = [
    "AgentState",
    "Command",
    "async_sqlite_checkpointer",
    "build_full_graph_builder",
    "build_graph_builder",
    "compile_full_with_checkpointer",
    "compile_with_checkpointer",
    "sqlite_checkpointer",
]
