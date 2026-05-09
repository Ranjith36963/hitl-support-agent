"""Researcher Agent — deterministic intent-based MCP Read tool selection.

Replaces enrich_context_node when MULTIAGENT_ENABLED=1. Decides which of
the 3 MCP Read tools to call based on intent. Returns the same state shape
as v3 enrich_context_node so downstream nodes are unchanged.

v4 milestone-1 uses deterministic intent → tool mapping. A full ReAct
version is a v4.1 follow-up; the deterministic version produces the same
trace narrative at lower cost + zero risk of agent loops.

Hard contract (do not break):
- MUST write `customer_history`, `customer_tier`, `policy_matches`,
  `context_hash` to state — Gate 1 (`gate_one_policy_risk`) reads
  `policy_matches` from state.
- MUST append an audit entry with `node="researcher_agent"`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from src.agents.base import build_handoff_metadata
from src.state import AgentState


def _client() -> Any:
    """Get the live MCPClientRouter.

    Delegates to src.nodes._client at call time so the eval harness's single
    patch point (`src.nodes._client`) reaches v4 too. Tests still monkeypatch
    `src.agents.researcher._client` directly — that replaces this function
    entirely and bypasses the delegation, which is the intended behavior.
    """
    from src.nodes import _client as nodes_client
    return nodes_client()


def _customer_email_from_audit(state: AgentState) -> str:
    """Recover customer email from the pii_redact audit entry."""
    for entry in reversed(state.get("audit_log") or []):
        if entry.get("node") == "pii_redact":
            tm = entry.get("token_map") or {}
            for token, original in tm.items():
                if token.startswith("[EMAIL_"):
                    return original
    return "unknown@example.com"


def _hash_context(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@traceable(run_type="chain", name="researcher_agent")
async def research_node(state: AgentState) -> dict[str, Any]:
    """The Researcher's single graph node — intent-routed tool selection."""
    client = _client()
    email = _customer_email_from_audit(state)
    intent = state.get("intent", "other")

    tools_called: list[str] = []
    payload: dict[str, Any] = {}

    kb = await client.read.get_kb_article(state["customer_message"])
    tools_called.append("get_kb_article")
    payload["kb"] = kb.model_dump()

    history_dicts: list[dict[str, Any]] = []
    customer_tier = "Free"

    if intent in {"refund", "billing", "complaint"}:
        profile = await client.read.get_crm_profile(email)
        history = await client.read.get_customer_history(email)
        tools_called.extend(["get_crm_profile", "get_customer_history"])
        payload["profile"] = profile.model_dump()
        payload["history"] = [h.model_dump() for h in history]
        history_dicts = payload["history"]
        customer_tier = profile.customer_tier or "Free"
    elif intent == "technical":
        profile = await client.read.get_crm_profile(email)
        tools_called.append("get_crm_profile")
        payload["profile"] = profile.model_dump()
        customer_tier = profile.customer_tier or "Free"

    return {
        "customer_history": history_dicts,
        "customer_tier": customer_tier,
        "policy_matches": kb.policy_references or [],
        "context_hash": _hash_context(payload),
        "audit_log": (state.get("audit_log") or [])
        + [
            {
                "ts": datetime.now(UTC).isoformat(),
                "node": "researcher_agent",
                "ticket_id": state.get("ticket_id", ""),
                "tools_called": tools_called,
                "kb_quote": kb.verbatim_quote,
                "metadata": build_handoff_metadata(
                    agent_name="researcher",
                    handoff_from="classify_intent",
                    handoff_to="drafter",
                    handoff_reason="context_ready",
                    tools_called=tools_called,
                ),
            }
        ],
    }


def build_researcher_subgraph() -> Any:
    """Compile the Researcher sub-graph — single node for v4 milestone-1."""
    builder = StateGraph(AgentState)
    builder.add_node("research", research_node)
    builder.add_edge(START, "research")
    builder.add_edge("research", END)
    return builder.compile()


__all__ = ["build_researcher_subgraph", "research_node"]
