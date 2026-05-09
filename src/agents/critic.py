"""Critic Agent — audits Drafter output before downstream gates.

Hard contract (see tests/test_critic_invariants.py):
- Writes ONLY to: draft_confidence, _critic_verdict, _critic_feedback,
  _critic_severity, _critic_iteration, audit_log
- Cannot set approval_status / send_status / slack_* / sent_message_id
- Cannot mutate prior audit_log entries (append-only)
- draft_confidence is MULTIPLIED by (1 - severity * 0.5) — only decreases

Per docs/v4_multiagent.md: Critic verdict adjusts draft_confidence; Gate 1
and Gate 2 thresholds remain hard-coded in src/policy.py. The Critic
augments, never replaces, the deterministic safety gates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langsmith import traceable

from src.agents.base import build_handoff_metadata, get_llm, get_model_id, load_prompt
from src.state import AgentState

_CRITIC_PROMPT = load_prompt("critic_system")


@traceable(run_type="llm", name="critic_judge")
async def _llm_judge(payload: dict[str, Any]) -> dict[str, Any]:
    """Call the LLM to score the draft. Returns {verdict, severity, feedback}.

    Module-level so tests can patch `src.agents.critic._llm_judge`.

    Failure mode: if the LLM returns malformed JSON (response_format=json_object
    is reliable on DeepSeek but not bulletproof), escalate-on-uncertainty —
    return a `revise` verdict at severity 0.5. This matches the safety contract:
    when the auditor itself fails, lower draft_confidence so Gate 2 escalates
    to a human, never silently pass through.
    """
    client = get_llm()
    resp = await client.chat.completions.create(
        model=get_model_id("CRITIC_MODEL_OVERRIDE"),
        messages=[
            {"role": "system", "content": _CRITIC_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,  # determinism > creativity for an auditor
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Escalate-on-uncertainty — Critic failure must NOT auto-pass. Lowering
        # draft_confidence (via severity 0.5) routes to Gate 2 → human review.
        return {
            "verdict": "revise",
            "severity": 0.5,
            "feedback": "Critic returned malformed JSON; recommend human review.",
        }


@traceable(run_type="chain", name="critic_agent")
async def critic_node(state: AgentState) -> dict[str, Any]:
    """Audit the current draft and adjust draft_confidence accordingly.

    Looped from drafter_node up to MAX_CRITIC_ITERATIONS times.
    """
    iteration = state.get("_critic_iteration", 0)
    payload = {
        "customer_message": state.get("customer_message", ""),
        "draft": state.get("final_draft", ""),
        "policy_quotes": state.get("policy_matches") or [],
        "intent": state.get("intent", ""),
    }
    verdict = await _llm_judge(payload)

    severity = float(verdict.get("severity", 0.0))
    severity = max(0.0, min(1.0, severity))  # clamp to [0,1]
    new_confidence = state.get("draft_confidence", 1.0) * (1 - severity * 0.5)

    audit_entry = {
        "ts": datetime.now(UTC).isoformat(),
        "node": "critic_agent",
        "ticket_id": state.get("ticket_id", ""),
        "verdict": verdict.get("verdict", "accept"),
        "severity": severity,
        "feedback": verdict.get("feedback", ""),
        "iteration": iteration,
        "metadata": build_handoff_metadata(
            agent_name="critic",
            handoff_from="drafter",
            handoff_to="drafter" if verdict.get("verdict") == "revise" else "gates",
            handoff_reason="revision_requested"
                if verdict.get("verdict") == "revise"
                else "accepted",
            loop_iteration=iteration,
        ),
    }

    return {
        "draft_confidence": new_confidence,
        "_critic_verdict": verdict.get("verdict", "accept"),
        "_critic_feedback": verdict.get("feedback", ""),
        "_critic_severity": severity,
        "_critic_iteration": iteration,
        "audit_log": (state.get("audit_log") or []) + [audit_entry],
    }


__all__ = ["_llm_judge", "critic_node"]
