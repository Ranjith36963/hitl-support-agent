# v4 — Multi-Agent HITL Customer Support (Spec Amendment)

> Amends `spec.md §18` ("Multi-agent critique" was cut for v3 24h scope) and locks the v4 architecture before any code lands. v3 stays the production-ready baseline; v4 is an iteration on top of it that preserves every hard invariant.

## Headline

**Multi-Agent HITL Customer Support System with LangGraph and LangSmith — Researcher Agent, Drafter Agent, Critic Agent, and Durable Human Approval.**

Three reasoning-heavy nodes from v3 become specialized agents coordinated by the same deterministic outer graph. Everything safety-critical stays hard-coded.

## What changes from v3 (only 2 nodes)

| v3 node | v4 replacement | What it gains |
|---|---|---|
| `enrich_context_node` | **Researcher Agent** (compiled sub-graph) | Decides which of the 3 MCP Read tools to call, in what order, when to stop. ReAct loop. |
| `draft_response_node` | **Drafter Agent + Critic Agent** (sub-graph with internal loop, max 2 iterations) | Self-critique against policy / tone / factuality before handoff to deterministic gates. |

Total agents: **3** (Researcher, Drafter, Critic). All compiled sub-graphs, all slot into existing parent-graph node positions.

## What stays UNCHANGED — non-negotiable hard invariants

These are the discriminating constraints. Any future change to v4 must preserve all of them.

| Invariant | Why it stays deterministic |
|---|---|
| `pii_redact_node` runs first, deterministically | LLM never sees real PII — no agent can be placed before redaction |
| `route_after_draft` (Gate 1 + Gate 2) | `false_auto_send_rate = 0%` requires hard thresholds, not LLM judgment |
| `channel_router_node` (priority list) | Priority-ordered routing is unit-tested and law: legal > enterprise+risk > angry > intent |
| `interrupt_gate` (Implementation Rule 1) | `interrupt()` alone in node, no `try/except`, no side effects |
| `slack_notification_node` BEFORE `interrupt_gate` | Reverse and the graph hangs forever |
| `send_email_node` app-layer idempotency | Check `sent_message_id` before SMTP — irreversible step is deterministic |
| `audit_log_node` append-only | Both `original_draft` AND `final_draft` saved on edit |
| MCP capability isolation (Read / Email Write / Slack Write) | Agents do NOT cross capability boundaries |
| `thread_id == ticket_id` | LangGraph thread stability for resume after restart |

**Critic agent is wired as input to `draft_confidence`, NOT as a replacement for Gates 1+2.** Single-line spec: *"Critic verdict adjusts `draft_confidence`; Gate 1 and Gate 2 thresholds remain hard-coded in `src/policy.py`."*

## Architecture

```
Email → pii_redact → classify_intent → [Researcher Agent] → [Drafter Agent ⇄ Critic Agent] → route_after_draft (Gates 1+2)
                                                                                                      ├─► auto_send → finalize → send → audit
                                                                                                      └─► escalate → channel_router → slack_notification
                                                                                                                                          → interrupt_gate (PAUSE)
                                                                                                                                          → resume → finalize → send → audit
```

### Agent specs

**Researcher Agent** (replaces `enrich_context_node`)

- Tools: `get_crm_profile`, `get_customer_history`, `get_kb_article` (MCP Read only)
- ReAct loop. Decides which subset of tools to call based on intent.
- For FAQ tickets, may call only KB. For refund tickets, calls all three. Saves tokens; surfaces agentic tool selection in traces.
- Output: same shape as v3 `enrich_context_node` — `customer_history`, `customer_tier`, `policy_matches`, `context_hash`, audit entry.

**Drafter Agent** (replaces `draft_response_node`)

- Tools: `policy_quote_lookup` (re-query MCP Read with focused queries), internal scratchpad
- ReAct loop. Drafts → may request a sharper policy quote → finalizes draft.
- Output: same shape as v3 — `original_draft`, `final_draft`, `draft_confidence`, audit entry.

**Critic Agent** (NEW — runs inside Drafter sub-graph after each draft)

- Tools: `check_grounding`, `check_factuality`, `check_tone` (lightweight rule-based + LLM-judge hybrid)
- Reads draft + retrieved policy quotes; emits `verdict ∈ {accept, revise}` + `feedback: str` + `severity ∈ [0,1]`.
- If `verdict == revise` AND iteration < 2 → loop back to Drafter with feedback in prompt.
- Else → exit sub-graph with final draft.
- Critic severity → `draft_confidence` adjustment: `draft_confidence *= (1 - severity * 0.5)`.
- **Hard cap: 2 iterations.** No infinite loops.

## LangSmith observability — handoff metadata schema

Every agent run captures a handoff-metadata dict via `src.agents.base.build_handoff_metadata()` with these fields:

| Field | Values |
|---|---|
| `agent_name` | `researcher` / `drafter` / `critic` |
| `handoff_from` | parent node name (e.g., `classify_intent`) |
| `handoff_to` | next agent or node |
| `handoff_reason` | `delegation` / `revision_requested` / `accepted` / `context_ready` / `initial_draft` / `revision_attempt` |
| `loop_iteration` | 0 / 1 (Drafter↔Critic only) |
| `tools_called` | comma-joined Researcher tool list (e.g., `get_kb_article` / `get_kb_article,get_crm_profile,get_customer_history`) |

### What shipped vs deferred (honest)

✅ **Shipped:** the dict above is appended to every agent's `audit_log` entry. `eval/results_v4_live.json` per-ticket audit logs contain it. Inspectable via `jq '.per_ticket[].audit_log[].metadata'`.

⚠️ **Deferred to v4.1:** auto-propagation into LangSmith run-tree metadata for UI-level filtering (so dashboards can group by `agent_name`). This requires `@traceable(metadata={...})` parameter wiring on the agent decorators, which we did not deliver. Today, per-agent slicing in LangSmith UI requires inspecting each run's input/output JSON — *not* a one-click filter.

This is **not** a v4 blocker — the data exists, just not in the most ergonomic location. The only honest path is to ship what we built and call out what we didn't.

## New evaluators (additions to the v3 set of 5)

| Evaluator | What it measures | Target |
|---|---|---|
| `tool_selection_precision` | Did Researcher call the *right* MCP Read tools (vs. all 3 always)? | Researcher skips ≥1 tool on ≥30% of FAQ tickets |
| `critic_disagreement_with_drafter` | How often Critic returns `revise` | Sanity range: 15–40% (too low = no signal; too high = bad Drafter) |
| `critic_alignment_with_humans` | Does Critic flag what humans also edit? (Slack Edit modal corpus) | F1 ≥ 0.6 against `(original_draft, final_draft)` audit pairs |
| `loop_iteration_distribution` | % of tickets ending at iteration 0 / 1 / 2 | Most at 0 or 1; 2 only on hard cases |
| `agent_cost_breakdown` | Cost per agent per ticket | Total v4 cost ≤ 3x v3 cost |

## A/B agent-model swap experiment (portfolio set-piece)

Run Researcher on DeepSeek V3 (default) vs. Haiku-tier (cheaper). Compare on the 50-ticket Bitext sample:

- `tool_selection_precision` (does Haiku still pick the right tools?)
- `agent_cost_breakdown` (does Haiku save real money?)
- `final_send_quality` (does the downstream output degrade?)

Result table in the README. This is the "I tuned my multi-agent system" signal recruiters value.

## Files added / modified

```
src/
  agents/                          ← NEW
    __init__.py
    base.py                        # shared LLM factory + agent prompt loader
    researcher.py                  # create_react_agent over MCP Read tools
    drafter.py                     # ReAct + policy_quote_lookup
    critic.py                      # ReAct + check_grounding/factuality/tone
  graph.py                         # MODIFY: 2 node slots become sub-graphs (5-line diff)
  nodes.py                         # MODIFY: enrich_context_node + draft_response_node delegate to agents
  llm.py                           # ADD: 3 @traceable wrappers + handoff metadata builder
data/
  prompts/                         ← NEW
    researcher_system.md
    drafter_system.md
    critic_system.md
eval/
  evaluators.py                    # ADD: 5 new evaluators per the table above
  ab_model_swap.py                 ← NEW (A/B experiment runner)
tests/
  test_researcher_agent.py         ← NEW
  test_drafter_critic_loop.py      ← NEW
  test_critic_invariants.py        ← NEW (Critic cannot escalate; can only adjust draft_confidence)
docs/
  v4_multiagent.md                 ← THIS FILE
```

## What this does NOT include (future work)

Cut for v4 24h scope:

- Specialist routing per intent (refund/billing/technical specialists) — Pattern B in the design doc; v5 if metrics justify
- Hierarchical teams / supervisor-of-supervisors — too complex for incremental win
- Online evals on live traffic — v5+
- Namespaced state (each agent in its own state shape) — sub-graphs handle this; not a portfolio differentiator

## Sign-off — when v4 is done

- All 3 agents implemented as compiled sub-graphs in `src/agents/`
- v3 graph still runs cleanly (sub-graph slot is reversible — feature flag `MULTIAGENT_ENABLED`)
- 5 new evaluators populated with real numbers on the 50-ticket Bitext sample
- v3 vs v4 comparison table in README with REAL numbers (response_quality, escalation_precision, false_auto_send_rate, agent_cost_breakdown)
- A/B model swap experiment results in README
- LangSmith trace screenshots showing nested agent run-trees
- One demo recording added: Critic catches a policy violation pre-Slack and triggers Drafter revision (the "agent-to-agent correction" set piece)
- Hard invariants preserved: PII determinism, `false_auto_send_rate=0%`, `interrupt_gate` isolation, idempotent send, append-only audit
