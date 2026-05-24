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

from src.agents.base import build_handoff_metadata, get_client
from src.metrics import timed_node
from src.state import AgentState


def _client() -> Any:
    """Get the live MCPClientRouter.

    Delegates to `src.agents.base.get_client` (which goes straight to
    `graph_runner.get_mcp_router`). v4 does NOT import from `src.nodes`
    anymore — module dependency arrow points only v3→shared and v4→shared,
    never v4→v3. Tests still monkeypatch `src.agents.researcher._client`
    directly to inject a fake router.
    """
    return get_client()


def _customer_email_from_audit(state: AgentState) -> str:
    """Thin wrapper around `src.pii.resolve_customer_email` — kept under
    the old name so existing call sites in this module don't need to
    change. See pii.py for the three-tier lookup, the
    `unknown@example.com` fail-closed sentinel, and the legacy audit_log
    compat path. DRY'd into pii.py on 2026-05-24 (audit Medium #15).
    """
    from src.pii import resolve_customer_email

    return resolve_customer_email(state)


def _hash_context(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@timed_node("researcher")
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
