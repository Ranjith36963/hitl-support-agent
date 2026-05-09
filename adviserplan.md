# Advisor-Hardened Build Plan — HITL Customer Support Agent

> **Scope note:** this is the **v3 build plan** (single-agent, ship-on-day-1). The v4 multi-agent amendment built on top of it lives in [`docs/v4_multiagent.md`](./docs/v4_multiagent.md) with implementation tasks in [`docs/superpowers/plans/2026-05-09-v4-multiagent.md`](./docs/superpowers/plans/2026-05-09-v4-multiagent.md). A frozen v3 status snapshot taken the day v4 began is in [`docs/v3_completion_status.md`](./docs/v3_completion_status.md).

> 6-10 hour delegated build plan. Reviewed by advisor model. Honest scope, hard cuts, named critical path.

---

## Honest scope reset (must be agreed before any code)

- **Spec §12 budgets 19h core build + 5h ship = 24h.** 6-10h is materially less than that.
- **6-10h delivers:** v3 architecture, **happy path** end-to-end on real Gmail + Slack, **Demo 1 (durable execution)** recorded, **1 eval run** on a small hand-curated set
- **Stretch (only if hour 8 looks clean):** Demo 2 (edit), Demo 3 (SLA timeout), full Bitext 50 sweep
- **Cuts from spec:**
  - **6 Slack channels → 3** (`#support-refunds`, `#support-technical`, `#support-complaints`). Drop legal/enterprise/billing — config addition, not architecture. README footnote: "channel set is config-driven; production maps to actual team Slack."
  - **50 Bitext tickets → 10 hand-curated** covering one example per code path (FAQ auto-send, refund, angry, enterprise+risk, repeated reject, etc). Cheaper to label, harder to game, demonstrates routing better.
  - **v1 → v2 → v3 metrics table:** build v3 only; mark v1/v2 rows as "iteration deferred — no fake numbers per spec rule." Spec's own honesty rule applied honestly.
  - **LangSmith tags:** ship `graph_version` + `intent` + `outcome` + `final_state`. Defer `risk_flags` + `confidence_bucket`.
- **Never cut:** durable execution + kill-restart demo, append-only audit log, app-layer idempotency, MCP capability isolation, three threading headers, `interrupt()` in dedicated node, HMAC signature verify

---

## Phase 0 — User checklist (cannot be delegated, runs in parallel with scaffolding)

1. **Gmail** → enable 2FA → generate App Password for IMAP/SMTP
2. **Slack** → create workspace → 3 channels (refunds/technical/complaints) → create app → enable **Socket Mode + Interactivity** → grab `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` + `SLACK_SIGNING_SECRET` → install to workspace → invite bot to all 3 channels
3. **OpenRouter** → account → API key → verify DeepSeek V3 free tier accessible
4. **LangSmith** → project + API key
5. **Bitext** → download CSV from HuggingFace → I'll sample/label 10 myself once it's local

**Fallback:** if any of these isn't done by minute 30, swap in a CLI approver script and document the swap. Real Slack is P1, not P0.

---

## Critical path (advisor's hardest correction)

**Hour 1 must end with:** `state.py` → `graph.py` with one dummy node + dedicated interrupt node → SQLite checkpointer → integration test that **kills the process mid-pause and resumes**. Green or nothing else runs.

**Reason:** "Slack post BEFORE interrupt" + "no try/except around interrupt" can only be verified by an actual resume test. Every node, every demo, every eval downstream is built on this foundation. If broken at hour 4, we have nothing.

**Implication:** Tracks B and C do NOT start at hour 0. They start at hour ~1, after the skeleton resume test passes.

---

## Phase plan

### Phase 0 — Scaffold + setup (0-30 min, both)

- **Me (Opus 4.7, main session):** folder tree, `.env.example`, `requirements.txt`, `pyproject.toml`, `.gitignore`, package init. Query **Context7** for current `langgraph` + `langgraph.checkpoint.sqlite` + `slack_bolt` Socket Mode + `views.open` API signatures **before writing any graph code** (per CLAUDE.md and spec — APIs change fast, training data is stale).
- **You:** Phase 0 checklist above

### Phase 1 — Skeleton resume test (30 min - 1h 15min, me solo, Opus 4.7)

- `src/state.py` — full `AgentState` TypedDict per spec §5
- `src/graph.py` — minimal: dummy node → interrupt node → resume target node → SQLite checkpointer
- `tests/test_resume.py` — kill process mid-pause, restart, resume on `Command(resume=...)`, assert state survived
- **Gate: resume test green.** If red at 90 min, stop and debug — don't fan out onto a broken foundation.

### Phase 2 — Three parallel tracks (1h 15min - 4h, me + 2 sub-agents)

| Track | Where | Tool | Scope |
|---|---|---|---|
| **A. Nodes + LLM + integration** | this session | **Opus 4.7** | `nodes.py` (Classify/Enrich/Draft/Finalize/Audit/Send), `llm.py` (OpenRouter + LangSmith decorators), wire policy + router into graph |
| **B. Three MCP servers** | `Agent(general-purpose)` shared tree, file-scoped to `mcp_server/` | **Sonnet 4.6** | `support_read.py`, `support_email_write.py`, `support_slack_write.py`, `src/mcp_client.py`. Spec §8 is the contract. |
| **C. Pure logic + fixtures + TDD** | `Agent(general-purpose)` shared tree, file-scoped to `policy.py`/`slack_router.py`/`pii.py`/`data/` | **Sonnet 4.6** | Invokes `superpowers:test-driven-development` skill. Tests first, then `policy.py` (Gate 1), `slack_router.py` (priority overrides for 3 channels), `pii.py` (round-trip). Plus `data/customers_seed.json` + `data/acme_policies.md` (small but rigorous corpus). |

**Why no worktrees:** Tracks own different files. No conflict. Branch/merge overhead loses to time saved on a 6-10h budget.

**Coordination:** I review B and C diffs as they land, before merging into the main flow.

### Phase 3 — Real I/O integration (4h - 6h, me solo, Opus 4.7)

- `email_listener.py` — IMAP IDLE primary + 30s poll fallback
- `slack_handler.py` — FastAPI webhook, HMAC-SHA256 verify, 5-min replay window, constant-time compare
- `server.py` — FastAPI app, edit modal via `views.open` (with `ui/edit.html` fallback if Socket Mode + modal interactions fight us)
- End-to-end smoke run with one hand-crafted ticket
- **Why serial:** debugging real network integrations in parallel is chaos

### Phase 4 — Eval + demo + ship (6h - 10h)

- `Agent(general-purpose)` on **Sonnet 4.6:** `eval/dataset.py` + `eval/evaluators.py` + `eval/run_experiments.py` against 10 hand-curated tickets. Verify `false_auto_send_rate == 0`.
- Me: dry-run + record **Demo 1 (durable execution)**. Stretch demos only if green.
- `Agent(coderabbit:code-review)`: full-branch review pass
- Me: invoke `superpowers:verification-before-completion` before claiming shipped. Write README honestly — real metrics only, deferred items called deferred.

---

## TDD vs spec-driven vs exploratory — explicit split

| Mode | Where | Why |
|---|---|---|
| **TDD** (test first) | `state.py`, `policy.py`, `slack_router.py`, `pii.py` | Pure functions. Bugs silent. Spec is testable directly. |
| **Spec-driven smoke** | `graph.py`, `nodes.py`, MCP servers | Write code from spec, validate with one ticket end-to-end. Unit tests where cheap. |
| **Exploratory** | LLM prompts in Classify/Draft, IMAP IDLE behavior, Slack Bolt integration | Live connection or output inspection required. TDD here wastes hours mocking. |

---

## Claude Code features actually used (no theater-invocation)

- **Sub-agents:** 2 in Phase 2 (Tracks B, C — Sonnet 4.6), 1 in Phase 4 (eval — Sonnet 4.6), 1 at end (`coderabbit:code-review`)
- **Skills:** `superpowers:test-driven-development` (Track C only), `superpowers:systematic-debugging` (only if stuck on interrupt), `superpowers:verification-before-completion` (before "done" claim)
- **MCPs:** **Context7 mandatory** before writing `graph.py` and `slack_handler.py` (LangGraph + slack_bolt API churn). Sequential Thinking if stuck mid-integration.
- **Plugins:** `coderabbit:code-review` at end
- **Skipped (over-engineering for 6-10h):** worktrees, hooks, brainstorming/writing-plans skills, claude-api skill (OpenRouter, not Anthropic SDK), feature-dev orchestrator (spec already is the architecture)

---

## Verification gates (Claude Code team pattern)

- **After each merge:** `pytest && mypy src/ && ruff check src/` — block merge if red
- **Before "done":** `python -m eval.run_experiments` → `false_auto_send_rate == 0` (spec's primary safety metric)
- **Before each demo recording:** dry-run cold once, fix, then record
- **Final pass:** `superpowers:verification-before-completion` skill before claiming shipped

---

## Why the critical path matters (explainer)

- LangGraph's `interrupt()` is the one piece where Python intuition fails. The node restarts on resume — pre-interrupt code re-runs. Only caught by killing and restarting; unit tests can't detect it.
- Building MCP servers and Slack handlers first means discovering the bug at hour 4 when the demo doesn't work, not at hour 1 when it's a 5-line fix.
- "Slack post BEFORE interrupt" rule from CLAUDE.md is enforceable only at the integration level. Skeleton-first proves the rule holds before any node depends on it.

## Why 3 channels isn't a real cut (explainer)

- Priority override logic in `slack_router.py` is identical for 3 vs 6 channels.
- Adding the other 3 in production is one config map.
- Architecture signal is the priority ordering, not the channel count.
- Reviewers grade this correctly when README footnotes the config swap.

---

## Pre-flight gate

Before any code touches disk:

- [ ] Verbal **yes** to: v3-only, 3 channels, 1 demo guaranteed, 10-ticket eval, real metrics only
- [ ] Confirm Phase 0 secrets are doable in the next 30 min
- [ ] User overrides anything they disagree with

On "go": Context7 query (LangGraph + slack_bolt) → scaffold → resume-test skeleton → only then fan out to Tracks B and C.
