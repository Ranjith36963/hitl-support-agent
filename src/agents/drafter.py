"""Drafter Agent + Drafter↔Critic loop sub-graph.

Replaces draft_response_node when MULTIAGENT_ENABLED=1. Internally:
1. Drafter writes a reply
2. Critic audits it
3. If Critic verdict == "revise" AND iteration < MAX → loop to step 1
4. Else → exit, parent graph proceeds to gates

Hard cap: MAX_CRITIC_ITERATIONS=3 (up to 2 revision passes). Always exits.

Output shape matches v3 draft_response_node so downstream nodes are unchanged:
  original_draft, final_draft, draft_confidence, audit_log.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from src.agents.base import build_handoff_metadata, get_llm, get_model_id, load_prompt
from src.agents.critic import critic_node
from src.llm import track_llm_usage
from src.state import AgentState

# Loop cap. The route condition is `iteration < MAX_CRITIC_ITERATIONS - 1`,
# so 3 lets the Critic loop at iterations 0 and 1 → at most 3 drafts / 2
# revision passes, then a hard exit. Keep this small: every extra iteration is
# another Drafter + Critic LLM round-trip. Do NOT raise it without re-sizing
# the bound check in tests/test_drafter_critic_loop.py.
MAX_CRITIC_ITERATIONS = 3
_DRAFTER_PROMPT = load_prompt("drafter_system")


@traceable(run_type="llm", name="drafter_llm")
async def _llm_draft(
    payload: dict[str, Any], state: AgentState | None = None
) -> dict[str, Any]:
    """Module-level so tests can patch / mock cleanly.

    `state` is optional. When supplied, token+cost telemetry from the response
    is folded into AgentState via `track_llm_usage` under the "drafter" label.
    Tests that monkeypatch this function pass `state=None` or omit it.
    """
    client = get_llm()
    model = get_model_id("DRAFTER_MODEL_OVERRIDE")
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _DRAFTER_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    track_llm_usage(state, "drafter", model, getattr(resp, "usage", None))
    return json.loads((resp.choices[0].message.content or "").strip())


@traceable(run_type="chain", name="drafter_agent")
async def drafter_node(state: AgentState) -> dict[str, Any]:
    """Write (or rewrite) the draft. Picks up Critic feedback if present."""
    iteration = state.get("_critic_iteration", 0)
    is_revision = bool(state.get("_critic_feedback"))

    payload: dict[str, Any] = {
        "customer_message": state.get("customer_message", ""),
        "intent": state.get("intent", "other"),
        "customer_tier": state.get("customer_tier", "Free"),
        "customer_history": state.get("customer_history") or [],
        "policy_quotes": state.get("policy_matches") or [],
        "iteration": iteration,
    }
    if is_revision:
        payload["critic_feedback"] = state.get("_critic_feedback", "")
    if state.get("rejection_reason"):
        payload["prior_rejection_reason"] = state["rejection_reason"]

    result = await _llm_draft(payload, state=state)

    return {
        # Preserve original_draft from iteration 0; later iterations overwrite final_draft only
        "original_draft": state.get("original_draft") or result["draft"],
        "final_draft": result["draft"],
        "draft_confidence": float(result.get("draft_confidence", 0.5)),
        "audit_log": (state.get("audit_log") or [])
        + [
            {
                "ts": datetime.now(UTC).isoformat(),
                "node": "drafter_agent",
                "ticket_id": state.get("ticket_id", ""),
                "iteration": iteration,
                "is_revision": is_revision,
                "metadata": build_handoff_metadata(
                    agent_name="drafter",
                    handoff_from="critic" if is_revision else "researcher",
                    handoff_to="critic",
                    handoff_reason="initial_draft" if not is_revision else "revision_attempt",
                    loop_iteration=iteration,
                ),
            }
        ],
    }


def _route_after_critic(state: AgentState) -> str:
    """Loop back to drafter if revision requested AND under cap; else exit."""
    verdict = state.get("_critic_verdict", "accept")
    iteration = state.get("_critic_iteration", 0)
    if verdict == "revise" and iteration < MAX_CRITIC_ITERATIONS - 1:
        return "drafter_loop"
    return END


def _bump_iteration(state: AgentState) -> dict[str, Any]:
    """Iteration counter — runs between Critic-revise and the next Drafter pass."""
    return {"_critic_iteration": state.get("_critic_iteration", 0) + 1}


def build_drafter_subgraph() -> Any:
    """Compile the Drafter↔Critic loop sub-graph.

    Layout:
        START → drafter → critic → (revise && under cap) → drafter_loop → drafter
                                  → (else) → END
    """
    builder = StateGraph(AgentState)
    builder.add_node("drafter", drafter_node)
    builder.add_node("critic", critic_node)
    builder.add_node("drafter_loop", _bump_iteration)

    builder.add_edge(START, "drafter")
    builder.add_edge("drafter", "critic")
    builder.add_conditional_edges(
        "critic",
        _route_after_critic,
        {
            "drafter_loop": "drafter_loop",
            END: END,
        },
    )
    builder.add_edge("drafter_loop", "drafter")
    return builder.compile()


__all__ = ["MAX_CRITIC_ITERATIONS", "_llm_draft", "build_drafter_subgraph", "drafter_node"]
