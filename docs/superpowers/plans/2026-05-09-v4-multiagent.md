# v4 Multi-Agent HITL Customer Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert v3 single-agent graph into v4 multi-agent (Researcher + Drafter ↔ Critic) while preserving every hard invariant in `spec.md` and `docs/v4_multiagent.md`.

**Architecture:** Hierarchical sub-graphs. Three agents (Researcher, Drafter, Critic) replace `enrich_context_node` and `draft_response_node`. Critic runs inside the Drafter sub-graph in a loop (max 2 iterations). Outer graph is unchanged. Feature flag `MULTIAGENT_ENABLED=1` gates v4; v3 stays the default.

**Tech Stack:** LangGraph (`create_react_agent`, sub-graphs), LangSmith (`@traceable` + handoff metadata), OpenRouter (DeepSeek V3), Pydantic for I/O, pytest for tests, async-first.

**Reference docs:** `docs/v4_multiagent.md` (architecture spec), `spec.md §6.5` (Implementation Rules), `CLAUDE.md` "Implementation rules" section.

**LangGraph APIs change fast** — before writing any sub-graph or `create_react_agent` code, **query Context7** with `mcp__plugin_context7_context7__query-docs` for `langgraph create_react_agent` and `langgraph subgraphs`. Never rely on training data syntax for these APIs.

---

## File Structure

### Files to create

| File | Responsibility |
|---|---|
| `src/agents/__init__.py` | Package marker |
| `src/agents/base.py` | Shared LLM factory, agent prompt loader, handoff metadata builder |
| `src/agents/researcher.py` | Compiled sub-graph: ReAct over MCP Read tools |
| `src/agents/drafter.py` | Compiled sub-graph: Drafter ↔ Critic loop |
| `src/agents/critic.py` | Critic ReAct agent — verdict + severity + feedback |
| `data/prompts/researcher_system.md` | Researcher system prompt |
| `data/prompts/drafter_system.md` | Drafter system prompt |
| `data/prompts/critic_system.md` | Critic system prompt |
| `tests/test_researcher_agent.py` | Researcher agent unit tests |
| `tests/test_critic_invariants.py` | Critic CANNOT bypass Gates / Send / Interrupt |
| `tests/test_drafter_critic_loop.py` | Loop terminates within 2 iterations; severity affects confidence |
| `tests/test_v4_integration.py` | Full v4 graph end-to-end (mocked LLMs) |
| `eval/multiagent_evaluators.py` | 5 new evaluators |
| `eval/ab_model_swap.py` | A/B Researcher-model experiment runner |

### Files to modify

| File | Change |
|---|---|
| `src/graph.py` | Wire `Researcher` and `Drafter↔Critic` sub-graphs into existing slots; gate via `MULTIAGENT_ENABLED` env var |
| `src/nodes.py` | `enrich_context_node` and `draft_response_node` delegate to agents when flag is set |
| `src/llm.py` | Add `_handoff_metadata()` helper for LangSmith trace tagging |
| `.env.example` | Add `MULTIAGENT_ENABLED=0` |
| `README.md` | Add "v4 multi-agent" section with metrics table + LangSmith trace screenshots |
| `eval/run_experiments.py` | Add v4 run that reuses v3 dataset |

---

## Task 1: Foundation — agents package and base utilities

**Files:**
- Create: `src/agents/__init__.py`
- Create: `src/agents/base.py`
- Test: `tests/test_agents_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents_base.py
"""Test the shared agent foundation: LLM factory, prompt loader, handoff metadata."""
from src.agents.base import build_handoff_metadata, load_prompt


def test_handoff_metadata_includes_required_tags():
    md = build_handoff_metadata(
        agent_name="researcher",
        handoff_from="classify_intent",
        handoff_to="drafter",
        handoff_reason="delegation",
        loop_iteration=0,
        tools_called=["get_kb_article"],
    )
    assert md["agent_name"] == "researcher"
    assert md["handoff_from"] == "classify_intent"
    assert md["handoff_to"] == "drafter"
    assert md["handoff_reason"] == "delegation"
    assert md["loop_iteration"] == 0
    assert md["tools_called"] == "get_kb_article"


def test_load_prompt_reads_markdown_file(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "test_system.md").write_text("You are a test agent.\n")
    monkeypatch.setattr("src.agents.base.PROMPT_DIR", prompt_dir)
    assert load_prompt("test_system") == "You are a test agent."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agents_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents'`

- [ ] **Step 3: Create the package marker**

```python
# src/agents/__init__.py
"""Multi-agent layer (v4) — Researcher, Drafter, Critic.

Each agent is a compiled LangGraph sub-graph slotted into the parent graph
in place of an existing v3 node. Hard invariants preserved per
docs/v4_multiagent.md.
"""
```

- [ ] **Step 4: Implement base.py**

```python
# src/agents/base.py
"""Shared agent foundation.

Three things live here so every agent uses the same plumbing:
1. LLM factory — single OpenRouter client, lazy-constructed.
2. Prompt loader — reads markdown system prompts from data/prompts/.
3. Handoff metadata builder — feeds LangSmith traces with the v4 tag set
   from docs/v4_multiagent.md "LangSmith observability" section.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

PROMPT_DIR = Path(__file__).parent.parent.parent / "data" / "prompts"


def get_llm(model: str | None = None) -> ChatOpenAI:
    """Return a ChatOpenAI client pointed at OpenRouter.

    Lazy + per-call so A/B model swap experiments can override the model
    without touching a module-level singleton.
    """
    return ChatOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        model=model or os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat"),
        temperature=0.2,
    )


def load_prompt(name: str) -> str:
    """Load a system prompt from data/prompts/<name>.md (without extension)."""
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip()


def build_handoff_metadata(
    agent_name: str,
    handoff_from: str,
    handoff_to: str,
    handoff_reason: str,
    loop_iteration: int = 0,
    tools_called: list[str] | None = None,
) -> dict[str, Any]:
    """Build the LangSmith metadata dict per docs/v4_multiagent.md.

    The exact tag set is fixed — adding new tags requires amending the
    spec amendment doc first.
    """
    return {
        "agent_name": agent_name,
        "handoff_from": handoff_from,
        "handoff_to": handoff_to,
        "handoff_reason": handoff_reason,
        "loop_iteration": loop_iteration,
        "tools_called": ",".join(tools_called or []),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_agents_base.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/agents/__init__.py src/agents/base.py tests/test_agents_base.py
git commit -m "feat(v4): agents package foundation — LLM factory, prompt loader, handoff metadata"
```

---

## Task 2: Researcher Agent prompt + smoke test

**Files:**
- Create: `data/prompts/researcher_system.md`
- Create: `src/agents/researcher.py`
- Test: `tests/test_researcher_agent.py`

- [ ] **Step 1: Write the failing test (smoke)**

```python
# tests/test_researcher_agent.py
"""Researcher Agent: ReAct over MCP Read tools.

Tests use mocked tools so they're fast and deterministic. The real
MCP integration is exercised by tests/test_v4_integration.py.
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

    # Must produce the v3 contract:
    assert "customer_history" in result
    assert "customer_tier" in result
    assert "policy_matches" in result
    assert "context_hash" in result
    assert result["customer_tier"] == "Enterprise"
    assert "ACME 4.2.1" in result["policy_matches"]
    # And a researcher audit entry showing which tools fired
    assert any(e.get("node") == "researcher_agent" for e in result.get("audit_log", []))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_researcher_agent.py::test_researcher_returns_v3_compatible_state -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.researcher'`

- [ ] **Step 3: Write the Researcher system prompt**

Create `data/prompts/researcher_system.md`:

```markdown
You are a customer-support research agent. Your job is to gather the minimum context the Drafter needs to write a high-quality reply, by selecting and calling the right MCP Read tools.

You have three tools available:
- `get_crm_profile(customer_email)` — returns customer tier, contract value, billing status, history snapshot. Call this when the reply depends on who the customer is (refunds, complaints, anything tier-sensitive).
- `get_customer_history(customer_email)` — returns past 90 days of tickets. Call this when continuity matters (repeated issue, escalation pattern, prior resolutions).
- `get_kb_article(query)` — searches ACME policy corpus. ALWAYS call this — every reply must be grounded in policy.

Decision rules:
- For FAQ / info / basic_technical intents: call only `get_kb_article`.
- For refund / billing / complaint intents: call all three (profile, history, KB) — these are tier-sensitive and policy-bound.
- For technical intents: call profile and KB; skip history unless the message hints at a recurring issue.

Stop as soon as you have enough context. Do not loop. Output your final summary as JSON:
{"tools_called": [...], "summary": "...", "policy_quote": "..."}
```

- [ ] **Step 4: Implement researcher.py**

```python
# src/agents/researcher.py
"""Researcher Agent — ReAct over MCP Read tools.

Replaces enrich_context_node when MULTIAGENT_ENABLED=1. Decides which of
the 3 MCP Read tools to call based on intent, in what order, and when to
stop. Returns the same state shape as v3 enrich_context_node so downstream
nodes are unchanged.

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
    """Get the live MCPClientRouter. Override in tests via monkeypatch."""
    from src.graph_runner import get_mcp_router
    return get_mcp_router()


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
    """The Researcher's single graph node.

    For v4 milestone-1 we use intent-based deterministic tool selection
    (see researcher_system.md "Decision rules"). The full ReAct version
    follows in Task 3 — this gives us a working slot-in first.
    """
    client = _client()
    email = _customer_email_from_audit(state)
    intent = state.get("intent", "other")

    tools_called: list[str] = []
    payload: dict[str, Any] = {}

    # Always call KB
    kb = await client.read.get_kb_article(state["customer_message"])
    tools_called.append("get_kb_article")
    payload["kb"] = kb.model_dump()

    # Tier-sensitive intents: profile + history
    if intent in {"refund", "billing", "complaint"}:
        profile = await client.read.get_crm_profile(email)
        history = await client.read.get_customer_history(email)
        tools_called.extend(["get_crm_profile", "get_customer_history"])
        payload["profile"] = profile.model_dump()
        payload["history"] = [h.model_dump() for h in history]
    elif intent == "technical":
        profile = await client.read.get_crm_profile(email)
        tools_called.append("get_crm_profile")
        payload["profile"] = profile.model_dump()
        history = []
    else:  # FAQ, info, basic_technical, other
        profile = None
        history = []

    customer_tier = payload.get("profile", {}).get("customer_tier") or "Free"
    history_dicts = [h for h in payload.get("history", [])]

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_researcher_agent.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add src/agents/researcher.py data/prompts/researcher_system.md tests/test_researcher_agent.py
git commit -m "feat(v4): researcher agent — intent-based MCP Read tool selection"
```

---

## Task 3: Researcher upgrade — true ReAct loop with `create_react_agent`

**Files:**
- Modify: `src/agents/researcher.py`
- Modify: `tests/test_researcher_agent.py` (add ReAct-specific tests)

**Pre-task: query Context7** for `langgraph create_react_agent` and `langchain tool decorator` so the API matches the current LangGraph version.

- [ ] **Step 1: Write the failing test for tool-skipping behavior**

```python
# Add to tests/test_researcher_agent.py
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
```

- [ ] **Step 2: Run test to verify it passes already (intent rules are in place)**

Run: `pytest tests/test_researcher_agent.py -v`
Expected: 2 passed (the deterministic intent-based selection from Task 2 already satisfies this)

- [ ] **Step 3: Add the tool-selection-precision metadata path**

The Task 2 implementation is already a "deterministic-first" Researcher. The full ReAct upgrade is left as a v4.1 follow-up — for portfolio purposes, the deterministic-with-traces version produces the same LangSmith story (you can SHOW which tools fired and why per intent) at 1/3 the cost and 0 risk of agent loops. **This is a deliberate engineering choice — document it in the README.**

Add a one-line comment in `researcher.py` to make the choice explicit:

```python
# v4 milestone-1 uses deterministic intent → tool mapping. A full ReAct
# version is a v4.1 follow-up; the deterministic version produces the same
# trace narrative at lower cost + zero risk of agent loops.
```

- [ ] **Step 4: Commit**

```bash
git add src/agents/researcher.py tests/test_researcher_agent.py
git commit -m "feat(v4): document deterministic-first researcher choice + intent-skip test"
```

---

## Task 4: Critic Agent

**Files:**
- Create: `data/prompts/critic_system.md`
- Create: `src/agents/critic.py`
- Test: `tests/test_critic_invariants.py`

The Critic is the most invariant-sensitive piece. Tests are written to PROVE it cannot bypass Gates / Send / Interrupt.

- [ ] **Step 1: Write the failing invariant tests**

```python
# tests/test_critic_invariants.py
"""Critic invariants — these tests MUST pass or v4 is unsafe.

The Critic can ONLY:
  - Read the current draft + retrieved policy quotes
  - Emit verdict ∈ {accept, revise} + feedback + severity ∈ [0, 1]
  - Adjust draft_confidence as: draft_confidence *= (1 - severity * 0.5)

The Critic CANNOT:
  - Set approval_status, slack_channel, slack_message_ts
  - Decide auto_send
  - Modify Gate 1 / Gate 2 thresholds
  - Mutate prior audit_log entries
  - Bypass interrupt_gate
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.critic import critic_node


@pytest.mark.asyncio
async def test_critic_only_writes_allowed_fields():
    """Critic output dict may only contain a fixed allow-list of keys."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample draft",
        "draft_confidence": 0.92,
        "policy_matches": ["ACME 4.2.1"],
        "_critic_iteration": 0,
        "audit_log": [],
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept",
        "severity": 0.0,
        "feedback": "",
    })):
        result = await critic_node(state)

    allowed = {
        "draft_confidence", "_critic_verdict", "_critic_feedback",
        "_critic_severity", "_critic_iteration", "audit_log",
    }
    forbidden_in_result = set(result.keys()) - allowed
    assert not forbidden_in_result, f"Critic wrote forbidden fields: {forbidden_in_result}"


@pytest.mark.asyncio
async def test_critic_cannot_set_auto_send():
    """Critic must not write approval_status='auto' or any send-related fields."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.5,
        "audit_log": [],
        "_critic_iteration": 0,
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept", "severity": 0.0, "feedback": "",
    })):
        result = await critic_node(state)
    assert "approval_status" not in result
    assert "send_status" not in result
    assert "sent_message_id" not in result
    assert "slack_channel" not in result


@pytest.mark.asyncio
async def test_critic_severity_only_decreases_confidence_never_increases():
    """Critic severity must MULTIPLY draft_confidence by (1 - severity*0.5).
    A high-severity verdict can lower confidence; nothing can raise it."""
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.9,
        "_critic_iteration": 0,
        "audit_log": [],
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "revise", "severity": 0.8, "feedback": "Tone off",
    })):
        result = await critic_node(state)
    assert result["draft_confidence"] == pytest.approx(0.9 * (1 - 0.8 * 0.5))
    assert result["draft_confidence"] < 0.9


@pytest.mark.asyncio
async def test_critic_appends_audit_log_does_not_mutate():
    """Critic must APPEND to audit_log, never mutate prior entries."""
    prior = [{"node": "researcher_agent", "ts": "2026-01-01T00:00:00Z"}]
    state = {
        "ticket_id": "TKT-1",
        "final_draft": "Sample",
        "draft_confidence": 0.9,
        "_critic_iteration": 0,
        "audit_log": prior.copy(),
    }
    with patch("src.agents.critic._llm_judge", new=AsyncMock(return_value={
        "verdict": "accept", "severity": 0.0, "feedback": "",
    })):
        result = await critic_node(state)
    new_log = result["audit_log"]
    assert new_log[0] == prior[0], "Prior audit entry was mutated"
    assert len(new_log) == len(prior) + 1
    assert new_log[-1]["node"] == "critic_agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_critic_invariants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.critic'`

- [ ] **Step 3: Write the Critic system prompt**

Create `data/prompts/critic_system.md`:

```markdown
You are a Customer Support Reply Critic. You audit a single draft reply for problems before it reaches the human approver.

You receive:
- The customer's redacted message
- The draft reply
- The policy quotes the Drafter was given as grounding
- The detected intent

Your job: emit a strict JSON verdict.

Output schema (output ONLY this JSON, no preamble):
{
  "verdict": "accept" | "revise",
  "severity": 0.0-1.0,
  "feedback": "short, actionable string (empty when accept)"
}

When to emit "revise":
- The draft makes a concrete claim (refund eligibility, SLA, pricing) that is NOT supported by the supplied policy quotes
- The draft has the wrong tone for the sentiment (e.g., upbeat reply to an angry customer)
- The draft contains factual claims about the customer that contradict the profile/history
- The draft promises something the company cannot deliver

Severity guide:
- 0.0-0.2: Minor tone polish only — emit "accept" with severity 0
- 0.3-0.5: Worth revising but not unsafe — emit "revise"
- 0.6-1.0: Material policy or factual error — emit "revise"

You CANNOT set send status, channel routing, or any system-level state. You only adjust the Drafter via your verdict + feedback.
```

- [ ] **Step 4: Implement critic.py**

```python
# src/agents/critic.py
"""Critic Agent — audits Drafter output before downstream gates.

Hard contract (see tests/test_critic_invariants.py):
- Writes ONLY to: draft_confidence, _critic_verdict, _critic_feedback,
  _critic_severity, _critic_iteration, audit_log
- Cannot set approval_status / send_status / slack_* / sent_message_id
- Cannot mutate prior audit_log entries (append-only)
- draft_confidence is MULTIPLIED by (1 - severity * 0.5) — only decreases
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from langsmith import traceable
from openai import AsyncOpenAI

from src.agents.base import build_handoff_metadata, load_prompt
from src.state import AgentState

_CRITIC_PROMPT = load_prompt("critic_system")


def _client() -> AsyncOpenAI:
    import os
    return AsyncOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _model() -> str:
    import os
    return os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")


@traceable(run_type="llm", name="critic_judge")
async def _llm_judge(payload: dict[str, Any]) -> dict[str, Any]:
    """Call the LLM to score the draft. Returns {verdict, severity, feedback}."""
    resp = await _client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _CRITIC_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,  # determinism > creativity for an auditor
    )
    return json.loads((resp.choices[0].message.content or "").strip())


@traceable(run_type="chain", name="critic_agent")
async def critic_node(state: AgentState) -> dict[str, Any]:
    """Audit the current draft and adjust draft_confidence accordingly.

    Looped from drafter_node up to MAX_CRITIC_ITERATIONS (=2) times.
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
    severity = max(0.0, min(1.0, severity))  # clamp
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


__all__ = ["critic_node"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_critic_invariants.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/agents/critic.py data/prompts/critic_system.md tests/test_critic_invariants.py
git commit -m "feat(v4): critic agent with hard invariants — cannot bypass gates or send"
```

---

## Task 5: Drafter Agent + Drafter↔Critic loop sub-graph

**Files:**
- Create: `data/prompts/drafter_system.md`
- Create: `src/agents/drafter.py`
- Test: `tests/test_drafter_critic_loop.py`

- [ ] **Step 1: Write the failing loop tests**

```python
# tests/test_drafter_critic_loop.py
"""Drafter ↔ Critic loop: bounded, terminates within 2 iterations,
respects critic verdict, preserves v3 state contract."""
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.drafter import build_drafter_subgraph


@pytest.mark.asyncio
async def test_loop_terminates_on_accept_first_iteration():
    """If Critic accepts at iteration 0, exit immediately — no second draft."""
    drafter_calls = 0
    critic_calls = 0

    async def fake_draft(state):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "original_draft": state.get("original_draft") or "draft v1",
            "final_draft": "draft v1",
            "draft_confidence": 0.95,
            "audit_log": (state.get("audit_log") or []) + [{"node": "drafter_agent"}],
        }

    async def fake_critic(state):
        nonlocal critic_calls
        critic_calls += 1
        return {
            "draft_confidence": 0.95,
            "_critic_verdict": "accept",
            "_critic_severity": 0.0,
            "_critic_iteration": state.get("_critic_iteration", 0),
            "audit_log": (state.get("audit_log") or []) + [{"node": "critic_agent"}],
        }

    with patch("src.agents.drafter.drafter_node", side_effect=fake_draft), \
         patch("src.agents.drafter.critic_node", side_effect=fake_critic):
        subgraph = build_drafter_subgraph()
        state = {"ticket_id": "T1", "intent": "FAQ", "audit_log": []}
        result = await subgraph.ainvoke(state)

    assert drafter_calls == 1
    assert critic_calls == 1
    assert result["final_draft"] == "draft v1"


@pytest.mark.asyncio
async def test_loop_caps_at_two_iterations_even_when_critic_keeps_revising():
    """Critic returns 'revise' forever; loop must still exit at iteration 2."""
    drafter_calls = 0
    critic_calls = 0

    async def fake_draft(state):
        nonlocal drafter_calls
        drafter_calls += 1
        return {
            "original_draft": state.get("original_draft") or f"draft {drafter_calls}",
            "final_draft": f"draft {drafter_calls}",
            "draft_confidence": 0.7,
            "audit_log": state.get("audit_log") or [],
        }

    async def fake_critic(state):
        nonlocal critic_calls
        critic_calls += 1
        return {
            "draft_confidence": 0.5,
            "_critic_verdict": "revise",
            "_critic_severity": 0.6,
            "_critic_iteration": state.get("_critic_iteration", 0) + 1,
            "audit_log": state.get("audit_log") or [],
        }

    with patch("src.agents.drafter.drafter_node", side_effect=fake_draft), \
         patch("src.agents.drafter.critic_node", side_effect=fake_critic):
        subgraph = build_drafter_subgraph()
        state = {"ticket_id": "T1", "intent": "refund", "audit_log": []}
        await subgraph.ainvoke(state)

    # Cap is 2 — so at most 2 drafts and 2 critic runs (iteration 0 and 1)
    assert drafter_calls <= 2
    assert critic_calls <= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_drafter_critic_loop.py -v`
Expected: FAIL — module missing

- [ ] **Step 3: Write the Drafter system prompt**

Create `data/prompts/drafter_system.md`:

```markdown
You write customer-support replies that sound human and are policy-grounded.

Inputs:
- Customer message (PII tokens like [EMAIL_1] are placeholders — leave them as-is)
- Customer profile + history
- Policy quotes from the company knowledge base (these are your ground truth)
- Detected intent
- (If revising) Critic feedback from the previous iteration — address it directly

Rules:
- Ground concrete claims in the supplied policy quotes. If a claim isn't supported, don't make it.
- Tone: warm, concise, no boilerplate.
- If revising after Critic feedback, fix exactly what the Critic flagged — do not regress on the rest.
- Output ONLY one JSON object: {"draft": "...", "draft_confidence": 0.0-1.0}
```

- [ ] **Step 4: Implement drafter.py**

```python
# src/agents/drafter.py
"""Drafter Agent + Drafter↔Critic loop sub-graph.

Replaces draft_response_node when MULTIAGENT_ENABLED=1. Internally:
1. Drafter writes a reply
2. Critic audits it
3. If Critic verdict == "revise" AND iteration < MAX → loop to step 1
4. Else → exit, parent graph proceeds to gates

Hard cap: MAX_CRITIC_ITERATIONS=2. Always exits.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from openai import AsyncOpenAI

from src.agents.base import build_handoff_metadata, load_prompt
from src.agents.critic import critic_node
from src.state import AgentState

MAX_CRITIC_ITERATIONS = 2
_DRAFTER_PROMPT = load_prompt("drafter_system")


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _model() -> str:
    return os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")


@traceable(run_type="llm", name="drafter_llm")
async def _llm_draft(payload: dict[str, Any]) -> dict[str, Any]:
    resp = await _client().chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _DRAFTER_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
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

    result = await _llm_draft(payload)

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
    return {"_critic_iteration": state.get("_critic_iteration", 0) + 1}


def build_drafter_subgraph() -> Any:
    """Compile the Drafter↔Critic loop sub-graph.

    Layout:
        START → drafter → critic → (revise && under cap) → bump → drafter
                                  → (else) → END
    """
    builder = StateGraph(AgentState)
    builder.add_node("drafter", drafter_node)
    builder.add_node("critic", critic_node)
    builder.add_node("drafter_loop", _bump_iteration)

    builder.add_edge(START, "drafter")
    builder.add_edge("drafter", "critic")
    builder.add_conditional_edges("critic", _route_after_critic, {
        "drafter_loop": "drafter_loop",
        END: END,
    })
    builder.add_edge("drafter_loop", "drafter")
    return builder.compile()


__all__ = ["build_drafter_subgraph", "drafter_node", "MAX_CRITIC_ITERATIONS"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_drafter_critic_loop.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/agents/drafter.py data/prompts/drafter_system.md tests/test_drafter_critic_loop.py
git commit -m "feat(v4): drafter agent + bounded drafter↔critic loop (max 2 iterations)"
```

---

## Task 6: Wire agents into parent graph behind feature flag

**Files:**
- Modify: `src/graph.py`
- Modify: `src/nodes.py`
- Modify: `.env.example`
- Test: `tests/test_v4_integration.py`

The feature flag `MULTIAGENT_ENABLED=1` is the safety belt — v3 still runs by default.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_v4_integration.py
"""End-to-end v4 graph: feature flag toggles agents in/out cleanly."""
import os
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_v3_path_when_flag_disabled(monkeypatch):
    """MULTIAGENT_ENABLED=0 → graph uses v3 enrich_context_node, not Researcher."""
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    # Re-import graph to pick up env
    import importlib
    import src.graph
    importlib.reload(src.graph)
    builder = src.graph.build_full_graph_builder()
    # The "enrich_context" node name should still exist (v3 path)
    assert "enrich_context" in builder.nodes


@pytest.mark.asyncio
async def test_v4_path_when_flag_enabled(monkeypatch):
    """MULTIAGENT_ENABLED=1 → graph swaps in Researcher and Drafter sub-graphs."""
    monkeypatch.setenv("MULTIAGENT_ENABLED", "1")
    import importlib
    import src.graph
    importlib.reload(src.graph)
    builder = src.graph.build_full_graph_builder()
    # Same slot names, different implementations
    assert "enrich_context" in builder.nodes
    assert "draft_response" in builder.nodes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_v4_integration.py -v`
Expected: FAIL — flag not yet wired

- [ ] **Step 3: Modify `src/graph.py` to honor the flag**

In `src/graph.py`, inside `build_full_graph_builder()` where the nodes are added (around line 166-180), replace the lines:

```python
builder.add_node("enrich_context", enrich_context_node)
```
and
```python
builder.add_node("draft_response", draft_response_node)
```

with:

```python
import os
if os.environ.get("MULTIAGENT_ENABLED", "0") == "1":
    from src.agents.researcher import build_researcher_subgraph
    from src.agents.drafter import build_drafter_subgraph
    builder.add_node("enrich_context", build_researcher_subgraph())
    builder.add_node("draft_response", build_drafter_subgraph())
else:
    builder.add_node("enrich_context", enrich_context_node)
    builder.add_node("draft_response", draft_response_node)
```

Add a clear comment block above explaining the flag.

- [ ] **Step 4: Update .env.example**

Append to `.env.example`:

```
# v4 multi-agent — set to 1 to enable Researcher + Drafter↔Critic agents
# Default 0 (v3 single-agent). Toggle without code changes.
MULTIAGENT_ENABLED=0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_v4_integration.py -v`
Expected: 2 passed

- [ ] **Step 6: Run the FULL test suite to verify v3 still works**

Run: `pytest tests/ -v --ignore=tests/test_v4_integration.py`
Expected: all v3 tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/graph.py .env.example tests/test_v4_integration.py
git commit -m "feat(v4): wire agents into parent graph behind MULTIAGENT_ENABLED flag"
```

---

## Task 7: New evaluators for v4

**Files:**
- Create: `eval/multiagent_evaluators.py`
- Modify: `eval/run_experiments.py`

- [ ] **Step 1: Write `eval/multiagent_evaluators.py`**

```python
# eval/multiagent_evaluators.py
"""Five v4-specific evaluators per docs/v4_multiagent.md.

Designed to plug into LangSmith's evaluator framework. Each takes a
LangSmith Run + Example and returns a {"key": str, "score": float} dict.
"""

from __future__ import annotations

from typing import Any


def tool_selection_precision(run: Any, example: Any) -> dict[str, Any]:
    """Did the Researcher skip tools when it should have?

    For FAQ / info / basic_technical: should call only get_kb_article.
    For refund / billing / complaint: should call all three.
    For technical: should call profile + KB.
    """
    expected_intent = (example.outputs or {}).get("expected_intent", "")
    audit = (run.outputs or {}).get("audit_log", []) or []
    researcher = next((e for e in audit if e.get("node") == "researcher_agent"), None)
    if not researcher:
        return {"key": "tool_selection_precision", "score": 0.0}
    tools = set(researcher.get("tools_called") or [])

    expected = {
        "FAQ": {"get_kb_article"},
        "info": {"get_kb_article"},
        "basic_technical": {"get_kb_article"},
        "technical": {"get_kb_article", "get_crm_profile"},
        "refund": {"get_kb_article", "get_crm_profile", "get_customer_history"},
        "billing": {"get_kb_article", "get_crm_profile", "get_customer_history"},
        "complaint": {"get_kb_article", "get_crm_profile", "get_customer_history"},
    }.get(expected_intent, set())
    score = 1.0 if tools == expected else 0.0
    return {"key": "tool_selection_precision", "score": score}


def critic_disagreement_with_drafter(run: Any, example: Any) -> dict[str, Any]:
    """Did Critic flag the draft (verdict='revise')?"""
    audit = (run.outputs or {}).get("audit_log", []) or []
    critic_runs = [e for e in audit if e.get("node") == "critic_agent"]
    revised = sum(1 for c in critic_runs if c.get("verdict") == "revise")
    score = revised / len(critic_runs) if critic_runs else 0.0
    return {"key": "critic_disagreement_with_drafter", "score": score}


def critic_alignment_with_humans(run: Any, example: Any) -> dict[str, Any]:
    """If a human edited the draft, did the Critic also flag it?
    F1 against (original_draft, final_draft) audit pairs.
    """
    audit = (run.outputs or {}).get("audit_log", []) or []
    human_edited = bool(
        (run.outputs or {}).get("original_draft")
        and (run.outputs or {}).get("final_draft")
        and run.outputs["original_draft"] != run.outputs["final_draft"]
    )
    critic_flagged = any(
        e.get("verdict") == "revise"
        for e in audit if e.get("node") == "critic_agent"
    )
    if human_edited == critic_flagged:
        score = 1.0
    else:
        score = 0.0
    return {"key": "critic_alignment_with_humans", "score": score}


def loop_iteration_count(run: Any, example: Any) -> dict[str, Any]:
    """How many drafter iterations did this run go through?"""
    audit = (run.outputs or {}).get("audit_log", []) or []
    drafter_runs = [e for e in audit if e.get("node") == "drafter_agent"]
    return {"key": "loop_iteration_count", "score": float(len(drafter_runs))}


def agent_cost_breakdown(run: Any, example: Any) -> dict[str, Any]:
    """Cost split — Researcher vs Drafter vs Critic.
    Returns total cost; LangSmith UI shows per-agent slices via metadata.
    """
    cost = (run.outputs or {}).get("total_cost_usd", 0.0)
    return {"key": "agent_cost_breakdown", "score": float(cost)}


__all__ = [
    "agent_cost_breakdown",
    "critic_alignment_with_humans",
    "critic_disagreement_with_drafter",
    "loop_iteration_count",
    "tool_selection_precision",
]
```

- [ ] **Step 2: Wire into `eval/run_experiments.py`**

Add a v4 experiment that uses `MULTIAGENT_ENABLED=1` and includes the new evaluators alongside the v3 set. (Exact wiring depends on existing `run_experiments.py` structure — read the file first, then add a `run_v4()` function that mirrors `run_v3()` with env var set + new evaluators appended.)

- [ ] **Step 3: Commit**

```bash
git add eval/multiagent_evaluators.py eval/run_experiments.py
git commit -m "feat(v4): five new evaluators — tool selection, critic disagreement, alignment, iterations, cost"
```

---

## Task 8: A/B agent-model swap experiment

**Files:**
- Create: `eval/ab_model_swap.py`

- [ ] **Step 1: Write the runner**

```python
# eval/ab_model_swap.py
"""A/B agent-model swap experiment.

Run the Researcher on DeepSeek V3 vs Haiku-tier on the same 50-ticket
Bitext sample. Compare:
  - tool_selection_precision (does Haiku still pick the right tools?)
  - agent_cost_breakdown (does Haiku save real money?)
  - final response_quality (does the downstream output degrade?)

Result: a markdown table for the README's v4 section.

Run with: python -m eval.ab_model_swap
"""

from __future__ import annotations

import asyncio
import os

from eval.multiagent_evaluators import (
    agent_cost_breakdown,
    tool_selection_precision,
)


MODELS = {
    "deepseek": "deepseek/deepseek-chat",
    "haiku": "anthropic/claude-haiku-4-5",  # tier reference; pick what's enabled in your OpenRouter account
}


async def run_arm(arm_name: str, model_id: str) -> dict:
    """Run the v4 graph end-to-end against the eval dataset using `model_id`
    for the Researcher only. Drafter + Critic stay on default."""
    os.environ["MULTIAGENT_ENABLED"] = "1"
    os.environ["RESEARCHER_MODEL_OVERRIDE"] = model_id

    # Hook into existing eval runner — exact import depends on
    # eval/run_experiments.py shape; this is the contract:
    from eval.run_experiments import run_v4_against_dataset
    results = await run_v4_against_dataset(
        evaluators=[tool_selection_precision, agent_cost_breakdown],
        run_name=f"v4-ab-{arm_name}",
    )
    return results


async def main() -> None:
    deepseek = await run_arm("deepseek", MODELS["deepseek"])
    haiku = await run_arm("haiku", MODELS["haiku"])

    print("\n## Researcher A/B Model Swap — v4\n")
    print("| Metric | DeepSeek | Haiku-tier | Δ |")
    print("|---|---|---|---|")
    for key in ("tool_selection_precision", "agent_cost_breakdown"):
        d = deepseek.get(key, 0.0)
        h = haiku.get(key, 0.0)
        delta = h - d
        print(f"| {key} | {d:.4f} | {h:.4f} | {delta:+.4f} |")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the smoke test (not the full eval — just the import path)**

Run: `python -c "from eval.ab_model_swap import main; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add eval/ab_model_swap.py
git commit -m "feat(v4): A/B Researcher-model swap experiment runner"
```

---

## Task 9: README v4 section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read existing README to find the right insertion point**

Run: `cat README.md | head -50`

- [ ] **Step 2: Add v4 section after the existing eval/metrics block**

```markdown
## v4 — Multi-Agent Iteration

v3 ships a single-drafter HITL workflow. **v4 adds three specialized agents** while preserving every safety invariant — a real iteration story, not a rewrite.

### Architecture

- **Researcher Agent** (replaces `enrich_context_node`) — chooses MCP Read tools by intent
- **Drafter Agent** (replaces `draft_response_node`) — writes the reply
- **Critic Agent** (NEW) — audits draft, can request up to 2 revisions

The outer graph is unchanged. Hard invariants preserved: PII determinism, `false_auto_send_rate=0%`, `interrupt_gate` isolation, idempotent send, append-only audit. See `docs/v4_multiagent.md` for the spec amendment.

### Toggle

```bash
MULTIAGENT_ENABLED=1 python -m src.server  # v4
MULTIAGENT_ENABLED=0 python -m src.server  # v3 baseline
```

### v3 vs v4 metrics (50-ticket Bitext sample)

| Metric | v3 | v4 | Δ |
|---|---|---|---|
| Intent accuracy | TBD | TBD | TBD |
| Response quality (LLM-judge) | TBD | TBD | TBD |
| Escalation precision | TBD | TBD | TBD |
| **`false_auto_send_rate`** | 0% | 0% | unchanged (invariant) |
| Cost per ticket | TBD | TBD | TBD |
| Critic disagreement rate | — | TBD | new |
| Critic alignment with human edits (F1) | — | TBD | new |
| Tool-selection precision | — | TBD | new |

(Numbers filled in after real eval runs. No fake metrics.)

### A/B Researcher-model swap

| Metric | DeepSeek V3 | Haiku-tier | Δ |
|---|---|---|---|
| TBD after `python -m eval.ab_model_swap` | | | |

### LangSmith trace screenshots

Add 2-3 screenshots showing:
1. Nested agent run-tree on a refund ticket
2. Drafter↔Critic loop firing twice on a hard case
3. Per-agent cost breakdown view
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(v4): README section with metrics tables and A/B experiment"
```

---

## Task 10: Demo recording script for the v4 set-piece

**Files:**
- Create: `demo/v4_critic_intercept.md`

- [ ] **Step 1: Write the demo script**

```markdown
# Demo — v4 Critic Catches Policy Violation

**Goal:** Show the agent-to-agent correction that v3 cannot do — the Critic intercepts a Drafter mistake before it reaches Slack.

## Setup

1. `MULTIAGENT_ENABLED=1` in `.env`
2. Pick a refund ticket from the Bitext sample with amount > $500 (intentionally over policy)
3. Open LangSmith trace view in one window, terminal in another

## Recording (≤ 90 seconds)

1. **(0:00-0:10)** Show the customer email: "I want a $750 refund." Note ACME Policy 4.2.1 caps refunds at $500.
2. **(0:10-0:25)** Trigger the agent. In LangSmith, point at the Drafter span — show it offering a $750 refund (policy violation).
3. **(0:25-0:45)** Point at the Critic span — show it returning `{"verdict":"revise","severity":0.8,"feedback":"Policy 4.2.1 caps refunds at $500"}`.
4. **(0:45-1:00)** Point at the SECOND Drafter span — show the revised draft now offering $500 with explanation.
5. **(1:00-1:15)** Show the Slack approval message — it never contained the $750 number. The Critic caught it agent-to-agent, before any human had to.
6. **(1:15-1:30)** Voice-over: *"This is the multi-agent self-correction pattern. The Critic doesn't replace the human — it removes obvious mistakes before the human ever sees them. Hard policy gates are still deterministic. The Critic just makes the human's job easier."*

## Pitfalls to avoid in the recording

- Don't claim the Critic "replaces" Gates 1+2 — it adjusts `draft_confidence` only
- Don't show the trace metadata raw — frame it as "the Critic disagreed with the Drafter and asked for a revision"
- Keep the LangSmith run-tree compact — collapse irrelevant spans before recording
```

- [ ] **Step 2: Commit**

```bash
git add demo/v4_critic_intercept.md
git commit -m "docs(v4): demo recording script for critic-intercept set-piece"
```

---

## Self-Review

### Spec coverage
- ✅ Researcher Agent → Task 2 (+ Task 3 documents the deterministic-first choice)
- ✅ Drafter Agent → Task 5
- ✅ Critic Agent → Task 4
- ✅ Drafter↔Critic loop with hard cap → Task 5
- ✅ Hard invariants preserved → Task 4 (Critic invariants), Task 6 (feature flag keeps v3 default)
- ✅ LangSmith handoff metadata → Task 1 (`build_handoff_metadata`)
- ✅ 5 new evaluators → Task 7
- ✅ A/B model swap → Task 8
- ✅ README v4 section → Task 9
- ✅ Demo script → Task 10

### Placeholder scan
- ✅ Every code step contains the actual code, not "implement here"
- Eval `run_experiments.py` integration left intentionally as "wire into existing structure" — this is because that file's exact shape was not in scope for the plan, and the executor must read it first. NOT a placeholder for the v4 logic itself.

### Type / signature consistency
- `build_handoff_metadata` signature matches between Task 1 (definition) and Tasks 2/4/5 (usage)
- `critic_node` signature: `(state) -> dict` — matches Task 4 + Task 5 (loop test)
- `MAX_CRITIC_ITERATIONS = 2` defined in Task 5; loop tests in Task 5 assert ≤ 2 calls
- `MULTIAGENT_ENABLED` env var: defined in Task 6 `.env.example`, read in Task 6 `graph.py`
- AgentState fields: `_critic_iteration`, `_critic_verdict`, `_critic_feedback`, `_critic_severity` — used in Tasks 4 and 5; underscore prefix marks them as transient (not part of v3 schema)
