"""Five v4-specific evaluators per docs/v4_multiagent.md.

Each takes a LangSmith Run + Example and returns a {"key": str, "score": float}
dict — compatible with the LangSmith evaluator framework. Pure functions; no
external state. Safe to compose with the v3 evaluators in eval/evaluators.py.

Targets (from docs/v4_multiagent.md):
- tool_selection_precision: Researcher skips ≥1 tool on ≥30% of FAQ tickets
- critic_disagreement_with_drafter: 15-40% revise rate (sanity range)
- critic_alignment_with_humans: F1 ≥ 0.6 vs (original_draft, final_draft) audit pairs
- loop_iteration_distribution: most at 0 or 1; 2 only on hard cases
- agent_cost_breakdown: total v4 cost ≤ 3x v3 cost
"""

from __future__ import annotations

from typing import Any

# Expected MCP-Read tool sets per intent — duplicated from researcher.py's
# decision rules so the evaluator is self-documenting and doesn't depend on
# import-time state from src.agents.researcher.
EXPECTED_TOOLS_BY_INTENT: dict[str, set[str]] = {
    "FAQ": {"get_kb_article"},
    "info": {"get_kb_article"},
    "basic_technical": {"get_kb_article"},
    "technical": {"get_kb_article", "get_crm_profile"},
    "refund": {"get_kb_article", "get_crm_profile", "get_customer_history"},
    "billing": {"get_kb_article", "get_crm_profile", "get_customer_history"},
    "complaint": {"get_kb_article", "get_crm_profile", "get_customer_history"},
}


def _audit(run: Any) -> list[dict[str, Any]]:
    return (run.outputs or {}).get("audit_log", []) or []


def tool_selection_precision(run: Any, example: Any) -> dict[str, Any]:
    """Did the Researcher call exactly the right MCP Read tools for the intent?

    Returns 1.0 if tools_called set matches EXPECTED_TOOLS_BY_INTENT, else 0.0.
    """
    expected_intent = (example.outputs or {}).get("expected_intent", "")
    researcher = next(
        (e for e in _audit(run) if e.get("node") == "researcher_agent"), None
    )
    if not researcher:
        return {"key": "tool_selection_precision", "score": 0.0}
    tools = set(researcher.get("tools_called") or [])
    expected = EXPECTED_TOOLS_BY_INTENT.get(expected_intent, set())
    score = 1.0 if tools == expected else 0.0
    return {"key": "tool_selection_precision", "score": score}


def critic_disagreement_with_drafter(run: Any, example: Any) -> dict[str, Any]:
    """How often did Critic flag the draft (verdict='revise')?

    Per-run rate (revise / total critic calls). Aggregate across runs to get
    the dataset-level disagreement rate. Sanity target: 15-40%.
    """
    critic_runs = [e for e in _audit(run) if e.get("node") == "critic_agent"]
    revised = sum(1 for c in critic_runs if c.get("verdict") == "revise")
    score = revised / len(critic_runs) if critic_runs else 0.0
    return {"key": "critic_disagreement_with_drafter", "score": score}


def critic_alignment_with_humans(run: Any, example: Any) -> dict[str, Any]:
    """Per-run agreement: did Critic flag iff a human edited?

    Returns 1.0 on agreement, 0.0 on disagreement. Aggregate across runs to
    derive accuracy / F1 vs human-edit ground truth.
    """
    outputs = run.outputs or {}
    human_edited = bool(
        outputs.get("original_draft")
        and outputs.get("final_draft")
        and outputs["original_draft"] != outputs["final_draft"]
    )
    critic_flagged = any(
        e.get("verdict") == "revise"
        for e in _audit(run) if e.get("node") == "critic_agent"
    )
    score = 1.0 if human_edited == critic_flagged else 0.0
    return {"key": "critic_alignment_with_humans", "score": score}


def loop_iteration_count(run: Any, example: Any) -> dict[str, Any]:
    """How many Drafter iterations did this run trigger?

    1 = no revision needed. 2 = Critic asked for one revision. Cap is 2 — see
    src/agents/drafter.MAX_CRITIC_ITERATIONS.
    """
    drafter_runs = [e for e in _audit(run) if e.get("node") == "drafter_agent"]
    return {"key": "loop_iteration_count", "score": float(len(drafter_runs))}


def agent_cost_breakdown(run: Any, example: Any) -> dict[str, Any]:
    """Total cost per ticket. LangSmith UI splits by agent_name metadata."""
    cost = (run.outputs or {}).get("total_cost_usd", 0.0)
    return {"key": "agent_cost_breakdown", "score": float(cost)}


__all__ = [
    "EXPECTED_TOOLS_BY_INTENT",
    "agent_cost_breakdown",
    "critic_alignment_with_humans",
    "critic_disagreement_with_drafter",
    "loop_iteration_count",
    "tool_selection_precision",
]
