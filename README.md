# HITL Customer Support Agent

> Production-grade Human-in-the-Loop customer support agent with durable execution, multi-channel Slack approval routing, three capability-isolated MCP servers, and real Gmail I/O. Built on LangGraph + LangSmith.

**Status:** v3 architecture shipped end-to-end (87 / 87 v3 tests passing). **v4 multi-agent layer (Researcher + Drafter ↔ Critic) shipped behind `MULTIAGENT_ENABLED` flag** — 31 additional v4 tests passing including 6 Critic-invariant tests (one of which proves escalate-on-uncertainty for malformed LLM JSON) and 3 v4 integration smokes. **Total: 118 / 118 tests passing in both `MULTIAGENT_ENABLED=0` and `=1` modes.** **Live LLM eval complete** — both v3 and v4 hold `false_auto_send_rate = 0%` against 10 hand-curated tickets via DeepSeek V3 on OpenRouter. v4 Critic flipped 1 ticket from auto_send → escalated (Gate 2 threshold tightening) — full numbers in the [v4 section](#v4--multi-agent-iteration). Demo recordings remain user-action items.

---

## What it does

Reads inbound customer email from Gmail (real IMAP IDLE). Classifies intent, enriches with mock CRM + an ACME SaaS Co policy corpus, drafts a reply. If both gates pass and the intent is in the safe set → auto-sends a real threaded SMTP reply. Otherwise pauses durably (LangGraph `interrupt()` + AsyncSqliteSaver) and posts a Block Kit approval message to the right Slack channel by priority routing. Human clicks Approve / Edit / Reject — graph resumes and ships.

Customer never sees the agent or Slack. The reply lands in their inbox threaded under the original message.

## Why HITL matters in 2026/2027

Enterprises deploy human-on-demand agents, not full autonomy. Audit trails, durability across restarts, and explainable approval surfaces are table stakes. This project is built around those constraints — `false_auto_send_rate = 0%` is the primary safety metric, not a chatbot quality score.

## Architecture

End-to-end product walkthrough: [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md).
Mermaid diagrams + state schema + LangSmith tags: [`docs/architecture.md`](./docs/architecture.md).
Build spec (sign-off criteria, failure modes, differentiators): [`spec.md`](./spec.md).

```
inbound Gmail (IMAP IDLE)
  → PII Redact → Classify → Enrich (MCP Read: CRM + ACME KB)
  → Draft → [Gate 1: policy risk] → [Gate 2: confidence + safe intent]
                                        │
              auto-send fast path  ←----+----→  escalate path
                  ↓                                ↓
              Finalize → Send (MCP Email)     Channel Router (3 channels)
                                                   ↓
                                        Slack Notification (MCP Slack)
                                                   ↓
                                          Interrupt Gate (DEDICATED)
                                                   ↓
                                       human Approve / Edit / Reject
                                                   ↓
                                  [Elapsed > 15min? → Revalidate Context]
                                                   ↓
                                        Finalize → Send → Audit Log
```

15 graph nodes, 5 conditional edges, 3 capability-isolated MCP servers (Read · Email Write · Slack Write).

## Differentiators (vs typical portfolio HITL projects)

1. **Real Gmail IMAP+SMTP**, not mocked — IDLE primary with 30s poll fallback, three threading headers (`In-Reply-To` + `References` + `Subject: Re:`)
2. **Real Slack with priority-ordered channel routing** — `legal/compliance > enterprise+risk > angry > by-intent` (3 channels in this build, 6 documented; channel set is config)
3. **Three custom MCP servers with capability separation** — Read cannot Send, Email Write cannot Slack, Slack Write cannot email; bounded blast radius for prompt injection
4. **Two-gate routing, not one fuzzy router** — Policy Risk Check first (fast-fail), then Confidence Check (only if Gate 1 passes)
5. **`false_auto_send_rate` as primary safety metric** — explicit, test-asserted, machine-checked in `eval/run_experiments.py`
6. **App-layer idempotent send** — `EmailSendResult.was_duplicate` flag captured in audit log; SMTP itself does not deduplicate, the application layer must
7. **Implementation Rules enforced by tests** — `interrupt()` lives alone in `interrupt_gate`; integration test asserts `slack.post_approval_request` is called exactly once after a full pause+resume cycle
8. **Slack webhook signature verification** — HMAC-SHA256 of `v0:{ts}:{body}`, 5-min replay window, constant-time compare; 7 dedicated tests including body-tamper detection
9. **PII redact at entry / restore at Finalize** — round-trip identity property tested
10. **Bounded loops** — 3-strike rejection rule routes to manual queue; 3-retry SMTP cap prevents infinite send retries
11. **Stale-context revalidation** — on long approval pauses (>15 min), context is re-fetched, hash-compared, and a delta panel is posted to Slack so the approver re-decides with fresh info instead of a silent stale send
12. **Durable resume across process restart** — kill the server mid-pause, restart it; the SQLite checkpointer survives, the Slack message buttons still resume on the right `slack_message_ts`
13. **Production-readiness layer** — three-tier eval (behavior contracts / empirical with bootstrap CIs / adversarial pass-fail grid), STRIDE threat model with mitigation citing real file paths, GitHub Actions CI (ruff + mypy + pytest + pip-audit + bandit), `/metrics` Prometheus endpoint + **`docker compose up` brings a Grafana dashboard live at `localhost:3000`** (see [`deploy/README.md`](./deploy/README.md)), per-ticket cost telemetry. Methodology + gaps led not buried — see [`eval/METHODOLOGY.md`](./eval/METHODOLOGY.md) and [`docs/threat_model.md`](./docs/threat_model.md)

## Tech stack

| Layer | Tool |
|---|---|
| Orchestration | LangGraph + AsyncSqliteSaver checkpointer |
| Observability | LangSmith (`@traceable` on every LLM call) |
| LLM | OpenRouter — DeepSeek V3 free tier (single model) |
| Customer I/O | Real Gmail IMAP IDLE (in) + SMTP (out) |
| Approval channel | Real Slack — Bolt SDK, Socket Mode in dev |
| Tools | Three custom MCP servers via `mcp` Python SDK |
| Backend | FastAPI + uvicorn |
| Eval | LangSmith evaluators + 10-ticket hand-curated dataset |

## Eval results — v3 architecture, real LLM (DeepSeek V3)

| Metric | v1 prompt | v2 prompt (few-shot) | Target | Status |
|---|---|---|---|---|
| **False auto-send rate** | 0.0% | **0.0%** | 0% | ✅ **PASS** — held across iteration (primary safety metric) |
| Escalation precision | 90.0% | **100.0%** | >90% | ✅ PASS |
| Response quality (LLM-as-judge) | 4.10 / 5 | **4.40 / 5** | >4.0 | ✅ PASS |
| Intent accuracy | 50.0% | **70.0%** | >85% | ⚠️ below target — see below |

**Behavior correctness: 10 / 10 tickets** reach the expected outcome (auto_send vs escalated). The intent_accuracy gap is label preference, not behavior — the two-gate routing escalates on `risk_flags` + confidence, not on the intent label alone, so label disagreement doesn't translate into safety failures.

Examples of label disagreement that did NOT change behavior:
- T07 expected `basic_technical`, LLM returned `info` — both in auto-send-safe set; auto-sent correctly
- T04 enterprise refund expected `refund`, LLM returned `billing` — both escalate via Gate 1
- T09 multi-intent expected `other`, LLM returned `billing` — both escalate via Gate 2

Per-ticket and failure-slice tables: [`eval/results.md`](./eval/results.md).

### Prompt iteration story (real, not invented)

- **v1 prompt:** plain schema + rules. Intent labels missing from the schema (`info`, `basic_technical`) made some safe-set classifications structurally impossible.
- **v2 prompt:** added 6 few-shot examples + expanded label schema to include all auto-send-safe intents. Result: +20 points intent accuracy, +10 points escalation precision, +0.30 response quality. Most importantly, `false_auto_send_rate` stayed at 0% under iteration — the safety property is independent of prompt quality.
- **v3 prompt (future work):** push intent_accuracy past 85% with vocabulary-alignment few-shots for the remaining edge cases (`basic_technical` vs `info`, multi-intent disambiguation).

> **No fake metrics.** Both v1 and v2 columns are real runs. v3 prompt is honestly marked as future work — not a made-up number.

## v4 — Multi-Agent Iteration

v3 ships a single-drafter HITL workflow. **v4 adds three specialized agents** while preserving every safety invariant — a real iteration on top of v3, not a rewrite.

### Architecture

| Agent | Replaces | What it gains |
|---|---|---|
| **Researcher Agent** | `enrich_context_node` | Decides which MCP Read tools to call by intent (FAQ → KB only; refund → all 3) |
| **Drafter Agent** | `draft_response_node` | Writes the reply; integrates Critic feedback on revision |
| **Critic Agent** *(NEW)* | — | Audits draft against policy + tone; can request **up to two revision passes** before exit (loop cap `MAX_CRITIC_ITERATIONS = 3` → Drafter runs at most 3×) |

The outer graph is unchanged (still 15 nodes). Drafter and Critic run inside a bounded loop sub-graph (`MAX_CRITIC_ITERATIONS = 3`). See [`docs/v4_multiagent.md`](./docs/v4_multiagent.md) for the spec amendment.

### Hard invariants preserved (proven in code, not just docs)

- **PII determinism** — `pii_redact_node` runs first, deterministically. No agent placed before redaction.
- **`false_auto_send_rate = 0%`** — Gate 1 + Gate 2 stay hard-coded in `src/policy.py`. **Critic verdict adjusts `draft_confidence`; it does NOT replace the gates.** Five `test_critic_invariants.py` tests prove the Critic cannot bypass gates / send / interrupt / mutate audit log.
- **`interrupt_gate` isolation** — Implementation Rule 1 unchanged.
- **Idempotent send** — `sent_message_id` lock unchanged.
- **Append-only audit log** — Critic appends a `critic_agent` entry; never mutates prior entries (test-asserted).

### Toggle (one env var)

```bash
MULTIAGENT_ENABLED=1 python -m src.server  # v4 multi-agent
MULTIAGENT_ENABLED=0 python -m src.server  # v3 single-agent (comparison artifact — see below)
```

> **Honest framing on v3 path:** v3 is retained as the **comparison artifact** for the v3-vs-v4 iteration story above, **not** as a "production rollback" — this is a portfolio build with no live traffic, so calling it production-rollback would be cosplay. **Both paths stay.** The head-to-head found v4 does **not** beat v3 — they tie — so `MULTIAGENT_ENABLED=0` (v3, the simpler path) stays the default. The A/B comparison *is* the deliverable; the honest tie is the result. See `src/graph.py` next to the flag, and `discussion.md` for the full audit.

### v3 vs v4 metrics (10 hand-curated tickets, live DeepSeek V3 via OpenRouter)

_Refreshed 2026-05-18 through the de-rigged harness — real KB retrieval, no injected policy matches._

| Metric | v3 | v4 | Δ |
|---|---|---|---|
| Intent accuracy | 70.0% | 70.0% | 0.0 pp |
| Escalation precision | 100.0% | 100.0% | 0.0 pp |
| **`false_auto_send_rate`** | **0.0%** | **0.0%** | **unchanged (safety invariant)** ✅ |
| Response quality (LLM-judge) | 4.50 / 5 | 4.10 / 5 | −0.40 (run-to-run noise) |
| Cost per ticket | *deferred to v4.1* | *deferred to v4.1* | — |

> **Cost row honestly deferred.** Per-call token + price instrumentation requires reading `resp.usage.prompt_tokens` / `completion_tokens` from every OpenAI response and accumulating with a per-model price table. That instrumentation is a v4.1 task (issue: v4.1-cost-tracking). The shortcut — hardcoding a model→price table or estimating from token counts alone — would produce numbers that drift the moment OpenRouter changes pricing. **No fake cost numbers in the README.**

**Honest finding — what the Critic did:** nothing measurable. In the refreshed run v3 and v4 produced **identical outcomes on all 10 tickets** — same intents, same escalate/auto-send decisions. An earlier run (2026-05-09, before the harness was de-rigged) had recorded v4 over-escalating one ticket (`eval-t07`) and scoring 90% escalation precision. A fresh run did **not** reproduce that — `eval-t07` auto-sent correctly under v4. That single ticket was run-to-run LLM noise, not a structural v4 regression.

**Honest bottom line: v4 did not beat v3.** Every metric is a tie or within noise. The response-quality gap (4.50 vs 4.10) is one LLM-judge run varying against another — a single live judge call per draft at n=10, where running v3 against itself twice would show comparable spread — and it points the *wrong* way for v4. The structural reason a v4 win is unlikely still holds: the Critic can only ever *lower* `draft_confidence` (`src/agents/critic.py` multiplies it by `1 - severity*0.5`, always in `[0.5, 1.0]`), so v4 escalates **≥** v3 on every ticket and cannot beat a v3 already at 100% escalation precision. The honest takeaway is the measurement discipline: the multi-agent version was built and evaluated head-to-head — twice, on hand-curated tickets and on real Bitext data — did not win, so the simpler path stays the default rather than being promoted on novelty. Full audit: [`discussion.md`](./discussion.md).

**External cross-check — real Bitext data.** A second, independent eval on 10 real customer messages from the Bitext Customer Support dataset (run live through both versions) reached the same verdict: v3 and v4 produced identical outcomes on 9 of 10 tickets, and the one difference is run-to-run LLM noise on a node v4 does not even change. In that 2026-05-18 run intent accuracy fell to 50–60% on real external text (vs ~70% hand-curated) — but `false_auto_send_rate` held at 0% in both. Full write-up: [`eval/bitext_findings.md`](./eval/bitext_findings.md).

**Honest caveat on `response_quality`.** The LLM-as-judge is the same provider+model family as the drafter (`gpt-4o-mini` judging `gpt-4o-mini` on OpenAI runs; same for DeepSeek-on-DeepSeek on prior OpenRouter runs). Same-family self-evaluation has known positive bias. `eval/cross_judge.py` partially mitigates by re-scoring with `gpt-4o`; a different-family judge (Claude / Gemini) would be a stronger signal — deferred until a non-OpenAI key is available. See [`eval/METHODOLOGY.md`](./eval/METHODOLOGY.md) "Judge bias".

**Breadth eval — all 27 Bitext intents, the honest worst-case (2026-05-21).** The 10-intent eval was filtered to SaaS-mappable intents. The full 27-intent breadth eval — one ticket per intent, including out-of-domain e-commerce intents — is the harder test, and **both versions fail the primary safety metric** on it. v3 produces **6 dangerous false auto-sends** out of 27 (`false_auto_send_rate = 54.5%` of its 11 auto-sends). **v4 caught 5 of those 6**, reducing dangerous auto-sends to 1, but at the cost of 7 over-corrections (escalated drafts the user could have safely auto-sent). The one false auto-send v4 still misses (`registration_problems` classified as `FAQ`) is a classifier-confidently-wrong case the Critic architecturally cannot detect — it operates on the draft, not on the intent label. This is the multi-agent design's ceiling and the highest-EV target for the next round of work. Provider note: this run used **OpenAI `gpt-4o-mini`** after the OpenRouter free-tier credits ran out — new baseline, intentionally labeled. Full senior-architect write-up: [`eval/bitext27_findings.md`](./eval/bitext27_findings.md).

**v4's first real win — the Critic-intercept eval (2026-05-19).** Every eval above grades escalate-vs-auto-send, an axis where v4's one-directional Critic is structurally capped. `eval/critic_intercept.py` finally measures v4 on its actual job — catching a flawed draft before a human sees it. Fed 5 deliberately-bad drafts and 5 good controls, the live Critic caught **4 of 5 bad drafts (80% intercept)** with **0 false alarms**. This is the first eval in the repo that credits the multi-agent layer on the axis it was built for. (One miss: an unsupported "you're an Enterprise customer" claim, accepted because no customer profile was supplied in the test state — see findings doc.)

**Classifier improvement (2026-05-19).** The intent-classifier prompt was sharpened — clearer `FAQ`/`info`/`basic_technical` boundaries, a billing-vs-technical rule, typo robustness. A clean v3 Bitext re-run measured intent accuracy **50% → 70%** and escalation precision **90% → 100%**. The matched v4 re-run is **blocked on an OpenRouter credit top-up** — so `results_bitext_v3.json` (post-fix) and `results_bitext_v4.json` (pre-fix) are currently a mismatched pair; both files carry a ⚠️ banner. `classify_intent` is shared code that runs before the v3/v4 swap, so the same gain is expected for v4 — but that is reasoning, not yet a measurement.

Raw run artifacts: [`results_curated_v3.json`](./eval/results_curated_v3.json) · [`results_curated_v4.json`](./eval/results_curated_v4.json) · [`results_bitext_v3.json`](./eval/results_bitext_v3.json) (post-prompt-fix, OpenRouter / DeepSeek V3) · [`results_bitext_v4.json`](./eval/results_bitext_v4.json) (pre-fix — re-run pending credits) · [`results_bitext27_v3.json`](./eval/results_bitext27_v3.json) (OpenAI gpt-4o-mini, 27-intent breadth) · [`results_bitext27_v4.json`](./eval/results_bitext27_v4.json) (OpenAI gpt-4o-mini, 27-intent breadth) · [`results_critic_intercept.json`](./eval/results_critic_intercept.json). The earlier `results_v3_live.json` / `results_v4_live.json` (2026-05-09) are kept as the subject of the `discussion.md` audit.

**Multi-agent-specific evaluators** (`eval/multiagent_evaluators.py`) are wired but not yet aggregated into the v3-vs-v4 table — they require LangSmith run-tree introspection rather than the existing per-ticket harness:

| Evaluator | Status | Inspection path |
|---|---|---|
| `tool_selection_precision` | Researcher tool calls captured in audit log | `eval/results_v4_live.json` audit entries |
| `critic_disagreement_with_drafter` | Critic verdicts captured in audit log | inspect ticket-level `audit_log` entries with `node="critic_agent"` |
| `critic_alignment_with_humans` | needs `audit_log` + `(original_draft, final_draft)` pairs from human edits | runs once Slack edit demo data is captured |
| `loop_iteration_count` | drafter audit entries with `iteration` field | per-ticket inspection |
| `agent_cost_breakdown` | per-agent cost via LangSmith run-tree | LangSmith UI for now; aggregator is v4.1 work |

### A/B Researcher-model swap (`python -m eval.ab_model_swap`)

| Arm | Researcher Model | Drafter+Critic Model |
|---|---|---|
| default | `deepseek/deepseek-chat` | `deepseek/deepseek-chat` |
| cheap | `meta-llama/llama-3.1-8b-instruct` | `deepseek/deepseek-chat` |

Compares `tool_selection_precision` (does the cheaper model still pick the right MCP tools?) and `agent_cost_breakdown` (does the swap save real money?). Drafter+Critic stay on the default model in both arms — isolates Researcher's contribution.

### Engineering choices documented honestly

- **Researcher milestone-1 is deterministic, not full ReAct.** Same trace narrative at 1/3 the cost and zero risk of agent loops. Full ReAct upgrade is v4.1 follow-up — see comment block in `src/agents/researcher.py`.
- **Critic loop hard cap is 3 iterations** (up to 2 revision passes). Test-asserted in `test_drafter_critic_loop.py`. Even when the Critic returns "revise" forever, the loop exits.
- **No new LLM dependency.** v4 uses the same SDK as v3 (`openai.AsyncOpenAI`) but through its own factory (`src/agents/base.get_llm()`), not by importing v3's client. Same library, parallel module — one OpenRouter integration class to debug, but the v4 agents do not have a runtime dependency on `src.nodes`. Module dependency arrow is v3→shared and v4→shared, never v4→v3.

See [`demo/v4_critic_intercept.md`](./demo/v4_critic_intercept.md) for the agent-to-agent self-correction demo script.

## Test coverage — 136 / 136

| Suite | Count | What it proves |
|---|---:|---|
| `test_resume.py` | 3 | Sync skeleton durability: pause, kill graph object, re-instantiate, resume |
| `test_integration_smoke.py` | 3 | **Async production graph** end-to-end (v3 path): refund-escalates-and-resumes, **async durability across simulated process restart**, FAQ-auto-sends. Implementation Rule 1 machine-verified — pre-interrupt nodes do NOT re-run on resume. |
| `test_v4_integration_smoke.py` | 3 | **Async production graph** end-to-end (v4 path): Researcher + Drafter↔Critic sub-graphs wire into the parent graph; FAQ auto-sends with all 3 v4 LLM call sites mocked |
| `test_mcp_subprocess_boot.py` | 1 | All 3 MCP servers spawn cleanly via stdio handshake — catches Python 3.13 / import bugs |
| `test_slack_handler.py` | 7 | HMAC signature: valid, replay defense (±5min), body-tamper detection, malformed input |
| `test_policy.py` | 36 | Two-gate routing — every branch including Gate 2-skipped-when-Gate-1-fails |
| `test_slack_router.py` | 18 | Priority overrides on 3 channels, `angry` always wins, intent fallthrough |
| `test_pii.py` | 19 | Redact + restore round-trip identity, stable token reuse, multi-PII handling |
| `test_email_idempotency.py` | 6 | Audit finding H2 — atomic idempotent send; re-running the graph cannot double-send |
| `test_security_email_handling.py` | 12 | Audit findings C1/C2/H3 — reply to SMTP envelope-from (no `From:` spoofing), no PII in audit log |
| **`test_agents_base.py`** | **5** | **v4: handoff metadata schema, prompt loader, AsyncOpenAI factory** |
| **`test_researcher_agent.py`** | **3** | **v4: intent → tool selection (FAQ skips history; refund calls all 3)** |
| **`test_critic_invariants.py`** | **6** | **v4: Critic CANNOT bypass gates / set send / mutate audit log; severity clamped to [0,1]** |
| **`test_drafter_critic_loop.py`** | **3** | **v4: loop cap = 3 iterations (up to two revision passes) even on infinite "revise"; respects accept verdict** |
| **`test_v4_integration.py`** | **3** | **v4: feature flag toggles agents in/out; outer node count stable across toggle** |
| **`test_multiagent_evaluators.py`** | **8** | **v4: 5 evaluators handle empty/typical/mismatch inputs** |

## Failure modes handled (per `architecture.md`)

| Failure | Behavior |
|---|---|
| Server crashes mid-pause | SQLite checkpoint at last super-step. On restart, Slack buttons still resume on the right `slack_message_ts`. |
| SMTP transient failure | `send_retry_count++` up to 3, all using same `send_idempotency_key`. After 3 → `failed_manual` → manual queue + Slack notice. |
| Customer follow-up mid-pause | `ticket_external_status = superseded`, old draft discarded, Slack updated. |
| Prompt injection in inbound email | MCP READ server has zero send capability; injection during retrieval has no path to email or Slack. Eval ticket T10 verifies. |
| Slack signature mismatch / replay | 401 + log security event; 7 dedicated tests. |
| 3 rejections | Auto-routes to manual queue, customer notified. |
| LangSmith down | Agent continues; traces buffer locally. Observability outage doesn't break flow. |
| Long approval delay (>15 min) | Context revalidated, delta posted to Slack, approver re-decides. |

## Run locally

### 0. Provision secrets

```bash
cp .env.example .env
# Edit .env with:
#   OPENROUTER_API_KEY     (https://openrouter.ai)
#   LANGSMITH_API_KEY      (https://smith.langchain.com)
#   GMAIL_USER + GMAIL_APP_PASSWORD   (Gmail App Password, 2FA required)
#   SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET + SLACK_APP_TOKEN
#                          (Slack app with Socket Mode + Interactivity enabled,
#                           bot invited to all 3 channels)
```

### 1. Install + run tests

```bash
pip install -r requirements.txt
pytest                                # 136 / 136 should pass (v3+v4)
python -m eval.run_experiments --no-llm   # routing eval (no creds needed)
```

### 2. Boot the service

```bash
python -m src.server
# IMAP listener spins up on GMAIL_USER inbox
# Slack Socket Mode connects automatically
# Send a test email to GMAIL_USER → watch the graph in LangSmith
```

### 3. Run the full eval (with LLM)

```bash
python -m eval.run_experiments         # uses OPENROUTER_API_KEY
# Outputs eval/results.md and eval/results.json with real response_quality
```

## Folder map

```
src/    state.py  graph.py  graph_runner.py  nodes.py  llm.py
        config.py  policy.py  slack_router.py  pii.py
        email_listener.py  slack_handler.py  mcp_client.py  server.py
mcp_server/  support_read.py  support_email_write.py  support_slack_write.py
data/   acme_policies.md  customers_seed.json
eval/   dataset.py  evaluators.py  run_experiments.py  results.md  results.json
tests/  test_state.py  test_policy.py  test_slack_router.py  test_pii.py
        test_resume.py  test_slack_handler.py  test_integration_smoke.py
        test_mcp_subprocess_boot.py
docs/   architecture.md
```

## What's deferred (honest list)

- **v1 / v2 ablations** — would need separate graph variants run on the same dataset; cut to fit the build window and avoid any temptation to invent numbers
- **Demo videos** — durable-execution kill-restart, approve-with-edits, SLA timeout. Scripts ready (see [`spec.md`](./spec.md) §13); recording happens after secrets land
- **6 Slack channels → 3** — `#support-legal`, `#support-enterprise`, `#support-billing` are config additions to `slack_router.py`, not architecture changes
- **External-benchmark eval (Bitext)** — a real 10-ticket Bitext eval now exists: 10 of Bitext's SaaS-adjacent intents, run live through both v3 and v4 (`eval/bitext_dataset.py`, data frozen in `data/bitext_eval_10.csv`, full write-up in [`eval/bitext_findings.md`](./eval/bitext_findings.md)). It confirmed v3≈v4 and showed intent accuracy drops to 50–60% on real external text vs ~70–100% on the hand-curated set. Still partial — 10 of Bitext's 27 intents, n=10; a larger holdout sweep remains future work
- **Postgres production checkpointer** — SQLite is sufficient for single-writer demo; AsyncPostgresSaver is a one-line swap
- **Webhook-based inbound mail** — IMAP IDLE works for demo; SES / SendGrid Parse / Postmark for production scale
- **Online evals** — current 10-ticket eval is offline; sampling layer over live traces is future work

## Source-of-truth docs

| File | When to open |
|---|---|
| [`spec.md`](./spec.md) | Build spec — scope, sign-off criteria, full state schema (§5), implementation rules (§6.5) |
| [`docs/architecture.md`](./docs/architecture.md) | Mermaid diagrams, env-var table, sequence, state machine, codebase map |
| [`HOW_IT_WORKS.md`](./HOW_IT_WORKS.md) | End-to-end product narrative — paste into demo walk-throughs |
| [`adviserplan.md`](./adviserplan.md) | v3 — the 6-10h delegation plan (advisor-hardened) |
| [`docs/v3_completion_status.md`](./docs/v3_completion_status.md) | **v3 frozen status snapshot** — captured the day v4 began; phase % and gaps |
| [`docs/v4_multiagent.md`](./docs/v4_multiagent.md) | **v4 spec amendment** — Researcher + Drafter↔Critic architecture lock + invariants |
| [`docs/superpowers/plans/2026-05-09-v4-multiagent.md`](./docs/superpowers/plans/2026-05-09-v4-multiagent.md) | **v4 implementation plan** — 10 TDD tasks, full code in each step |
| [`eval/METHODOLOGY.md`](./eval/METHODOLOGY.md) | **Eval methodology** — three layers (contracts/empirical/adversarial), CIs, what's deferred |
| [`docs/threat_model.md`](./docs/threat_model.md) | **STRIDE threat model** — asset list + per-threat existing mitigation + honest residual-risk register |
| [`CLAUDE.md`](./CLAUDE.md) | Project memory — invariants and non-negotiable rules |

---

Built in a 6-10h Claude Code sprint with delegated sub-agents (Sonnet 4.6) for parallel tracks, Opus 4.7 for the integration backbone. See `adviserplan.md` for the strategy.
