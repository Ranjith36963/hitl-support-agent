"""Smoke tests for the v4 evaluators — verify each handles empty / typical inputs."""
from types import SimpleNamespace

from eval.multiagent_evaluators import (
    agent_cost_breakdown,
    critic_alignment_with_humans,
    critic_disagreement_with_drafter,
    loop_iteration_count,
    tool_selection_precision,
)


def _run(audit: list[dict], **outputs) -> SimpleNamespace:
    return SimpleNamespace(outputs={"audit_log": audit, **outputs})


def _example(**outputs) -> SimpleNamespace:
    return SimpleNamespace(outputs=outputs)


def test_tool_selection_precision_perfect_match():
    audit = [{"node": "researcher_agent", "tools_called": ["get_kb_article"]}]
    result = tool_selection_precision(_run(audit), _example(expected_intent="FAQ"))
    assert result == {"key": "tool_selection_precision", "score": 1.0}


def test_tool_selection_precision_mismatch():
    audit = [{"node": "researcher_agent", "tools_called": ["get_kb_article"]}]
    # FAQ should call only KB; refund should call all 3 — mismatch
    result = tool_selection_precision(_run(audit), _example(expected_intent="refund"))
    assert result["score"] == 0.0


def test_tool_selection_precision_missing_researcher():
    result = tool_selection_precision(_run([]), _example(expected_intent="FAQ"))
    assert result["score"] == 0.0


def test_critic_disagreement_rate():
    audit = [
        {"node": "critic_agent", "verdict": "accept"},
        {"node": "critic_agent", "verdict": "revise"},
    ]
    result = critic_disagreement_with_drafter(_run(audit), _example())
    assert result["score"] == 0.5


def test_critic_alignment_agreement():
    audit = [{"node": "critic_agent", "verdict": "revise"}]
    run = _run(audit, original_draft="v1", final_draft="v2")  # human edited
    result = critic_alignment_with_humans(run, _example())
    assert result["score"] == 1.0  # human edited AND critic flagged


def test_critic_alignment_disagreement():
    audit = [{"node": "critic_agent", "verdict": "accept"}]
    run = _run(audit, original_draft="v1", final_draft="v2")  # human edited but critic didn't flag
    result = critic_alignment_with_humans(run, _example())
    assert result["score"] == 0.0


def test_loop_iteration_count():
    audit = [
        {"node": "drafter_agent", "iteration": 0},
        {"node": "drafter_agent", "iteration": 1},
    ]
    result = loop_iteration_count(_run(audit), _example())
    assert result["score"] == 2.0


def test_agent_cost_breakdown():
    run = _run([], total_cost_usd=0.0042)
    result = agent_cost_breakdown(run, _example())
    assert result["score"] == 0.0042
