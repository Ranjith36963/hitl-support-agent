# HITL Customer Support Agent — Project Memory

> Always-loaded. Kept tight. Three docs are source of truth — point at them, don't duplicate.

## Project state — v4 multi-agent shipped behind feature flag

- **v3** (single-agent) and **v4** (multi-agent: Researcher + Drafter↔Critic) both live in the codebase.
- Toggle: `MULTIAGENT_ENABLED=1` enables v4 (**default since 2026-05-23**). `=0` keeps v3 path for direct comparison.
- **Both paths retained intentionally** — the v3↔v4 head-to-head IS the deliverable. On the 10-ticket curated + 10-ticket Bitext eval sets v3 and v4 tied; on the 27-intent breadth eval and 25-ticket adversarial set **v4 caught 5/6 dangerous false auto-sends v3 missed** and 3 more classifier_trap cases — that's what drove the default flip. Trade-off: v4 ~2× cost/ticket and over-escalates some simple FAQs. Full audit in `eval/bitext27_findings.md` + `discussion.md`.
- Live LLM eval results: both modes hold `false_auto_send_rate = 0%` on the 10-ticket curated + 10-ticket Bitext sets. Both FAIL safety on bitext27 (v3=54.5%, v4=50% of auto-sends wrong — small denominator; absolute count fell 6 → 1).
- Tests: 143/143 passing in both flag modes. CI green on every PR.

## Source docs

| Doc | When to open it |
|---|---|
| `spec.md` | Build spec — scope, sign-off, full state schema (§5), implementation rules (§6.5), v4 amendment (§21) |
| `docs/architecture.md` | Diagrams, env-var table, sequence, state machine, codebase map, LangSmith tag table — includes v4 sub-graph section |
| `HOW_IT_WORKS.md` | End-to-end product narrative — Jamie example (v3) + "How v4 changes this story" appended |
| `docs/v4_multiagent.md` | v4 spec amendment — architecture lock + hard invariants |
| `docs/superpowers/plans/2026-05-09-v4-multiagent.md` | v4 TDD implementation plan (10 tasks, full code) |
| `docs/v3_completion_status.md` | Frozen v3 status snapshot taken the day v4 began |
| `adviserplan.md` | v3 build plan (advisor-hardened 6-10h delegation plan) |

## Honesty rule (non-negotiable)

- **Never work in desperation.** If something is broken, blocked, or unclear — stop and say so plainly. Do not paper over a failing test, mock around a broken integration, or skip a step to look productive.
- **Never sugarcoat.** Tell bad news in the same plain language as good news. A failing test stays failing until it's actually fixed; a half-working feature is described as half-working, not as "shipped."

## Communication style (non-negotiable)

- **Bullet points only.** No paragraphs. Lead with status: what's done · what's broken · what's blocked · what's next.
- **Plain language.** Skip unexplained jargon and command syntax inside explanations unless the user asks. Keep grammar correct, cut filler.

## What we're building

Agent-first, human-on-demand customer support agent. **Real Gmail** in/out (IMAP IDLE / SMTP), **real Slack** with 3 channels routed by priority (cut from spec's 6 — config-driven), durable LangGraph workflow, **three capability-isolated MCP servers**. Mock CRM + fictional **ACME SaaS Co** policy corpus. **v3 single-agent + v4 multi-agent (Researcher + Drafter↔Critic)** behind `MULTIAGENT_ENABLED` flag. Agent owns the workflow, humans get pulled in only when needed.

## Tech stack (do not substitute without asking)

| Layer | Tool |
|---|---|
| Orchestration | **LangGraph** + SQLite checkpointer (super-step boundary persistence) |
| LLM | OpenRouter (DeepSeek V3 free) — single model |
| Observability | **LangSmith** (every step traced; tag set in `architecture.md`) |
| Customer I/O | **Real Gmail** — IMAP IDLE in / SMTP out, threaded replies |
| Approval channel | **Real Slack** — Bolt SDK; Socket Mode in dev, webhook+HMAC in prod |
| Edit modal | Slack `views.open` (web fallback `ui/edit.html`) |
| Tools | **Three MCP servers** — Read / Email Write / Slack Write |
| Policy corpus | `data/acme_policies.md` (fictional, RAG-retrieved) |
| Backend | FastAPI |
| Eval data | 10 hand-curated tickets (`eval/dataset.py`) + 10 real Bitext tickets (`eval/bitext_dataset.py` — first batch, 10 of Bitext's 27 intents) |

## Graph node order — Slack post BEFORE interrupt!

`Email Listener` → `PII Redact` → `Classify Intent` → `Enrich Context` → `Draft Response` → `Policy Risk Check` (Gate 1) → `Confidence Check` (Gate 2) → `Channel Router` → **`Slack Notification`** → **`Interrupt Gate`** (pause) → resume → action {reject / approve / edit} → `Reject Check` (loop to Draft if <3) OR `Elapsed Check` → `Revalidate Context` → `Summarize Changes` (delta posted on same Slack msg) → `Finalize Action` (PII restore + threading headers) → `Send Email` → `Audit Log`.

Auto-send path skips from Confidence Check straight to Finalize.

## Two-gate routing (lift into `src/policy.py`)

- **Gate 1 — Policy Risk:** escalate if any of refund/money mention, angry sentiment, edge-case intent, ACME policy match.
- **Gate 2 — Confidence** (only if Gate 1 passes): escalate if `intent_confidence < 0.85` OR `draft_confidence < 0.85`.
- Auto-send only when both pass AND `intent in {FAQ, info, basic_technical}`.
- **Primary safety metric:** `false_auto_send_rate = 0%`.

## Channel router priority (in `src/slack_router.py`) — higher wins on conflict

1. `risk_flags` contains `legal` or `compliance` → `#support-legal`
2. `customer_tier == Enterprise` AND any `risk_flags` → `#support-enterprise`
3. `sentiment == angry` → `#support-complaints`
4. by `intent` → `#support-{refunds, technical, billing}`

## Implementation rules (NON-NEGOTIABLE — silent failures otherwise)

1. **`interrupt()` lives in its own dedicated node.** No DB / MCP / audit / log calls in that node. On resume the node restarts from the top; side effects duplicate. Slack post is a *separate* node *before* the interrupt node.
2. **Never wrap `interrupt()` in `try/except`.** It raises a special exception the LangGraph runtime catches. Wrapping breaks the pause.

## Critical invariants

- **App-layer idempotent send.** `Send Email` checks `sent_message_id` in state before SMTP. SMTP itself does not dedupe.
- **Three threading headers required.** `In-Reply-To` AND `References` AND `Subject: Re: ...` — missing any breaks Gmail threading.
- **Slack webhook signature** (when not on Socket Mode) — HMAC-SHA256 of `v0:{timestamp}:{body}`, 5-min replay window, constant-time compare.
- **MCP capability isolation.** Read cannot send. Email Write cannot Slack. Slack Write cannot email. Capability separation = bounded blast radius for prompt injection.
- **Append-only audit log.** Never mutate; both `original_draft` and `final_draft` saved when human edits.
- **PII redact at entry / restore in Finalize.** LLM never sees real PII.
- **Bounded loops.** `human_rejection_count >= 3` → `manual_queue`. `send_retry_count >= 3` → `failed_manual`.
- **`thread_id == ticket_id`** — stable LangGraph thread identifier; `slack_message_ts` lets webhook resume target the right Slack message.

## State schema essentials (full block in `spec.md §5`)

`AgentState` TypedDict — required keys include `ticket_id`/`thread_id`, `email_thread_id`, `customer_tier`, `risk_level`, `policy_matches`, `slack_channel`, `slack_message_ts`, `send_idempotency_key`, `sent_message_id`, `human_rejection_count`, `rejection_reason`, `send_retry_count`, `context_hash`, `original_draft`/`final_draft`, `approval_status`, `send_status`, `final_state`, cost/token fields, `audit_log` (append-only).

## Folder layout (v3 target)

```
src/    state.py  graph.py  nodes.py  llm.py  policy.py  slack_router.py
        pii.py    email_listener.py   slack_handler.py   mcp_client.py   server.py
mcp_server/  support_read.py  support_email_write.py  support_slack_write.py
data/   acme_policies.md  customers_seed.json  prompts/
eval/   dataset.py  evaluators.py  run_experiments.py
ui/     edit.html         (Slack Edit fallback — modal preferred)
tests/  test_state.py  test_policy.py  test_slack_router.py  test_pii.py  test_resume.py
```

## Env vars (see `.env.example`)

Tunables (defaults): `REVALIDATE_THRESHOLD_MIN=15` · `MAX_HUMAN_REJECTIONS=3` · `MAX_SEND_RETRIES=3` · `SLA_DEADLINE_HOURS=24` · `IMAP_POLL_INTERVAL_SEC=30`.
Secrets: `OPENROUTER_API_KEY` · `LANGSMITH_API_KEY` · `LANGSMITH_PROJECT` · `GMAIL_USER` · `GMAIL_APP_PASSWORD` · `SLACK_BOT_TOKEN` · `SLACK_SIGNING_SECRET` · `SLACK_APP_TOKEN` (Socket Mode).

## Build conventions

- **Type hints + Pydantic** on every function and I/O model.
- **Async-first** for FastAPI, MCP client, IMAP/SMTP.
- **No bare `except`** — specific exceptions, log via LangSmith trace.
- **Secrets only via env vars** — `.env.example` committed, `.env` gitignored.
- **LangGraph/LangSmith APIs change fast** — query Context7 before writing graph or tracing code; never rely on training-data syntax.

## Three demo recordings required

1. **Durable execution** — kill server mid-interrupt → restart → Slack approve → real email arrives. **Requires `PII_VAULT_DB_PATH` set** (opt-in persistent sidecar; default off preserves the 2026-05-09 C1/C2 in-memory-only PII hardening). `docker-compose.yml` opts in by default for the demo. Without the sidecar, resume cannot resolve the trustworthy recipient address and routes the ticket to `failed_manual` — bug found in the live smoke test 2026-05-24, fixed in `src/pii.py` + `src/config.py`; threat-model row A2 documents the trade-off.
2. **Approve-with-edits** — Slack modal edit; audit log shows both drafts.
3. **SLA timeout** — 24h no Slack response → auto-escalate to `manual_queue` + Slack notice.

## Red flags — do not ship with these

Hardcoded keys · Single test case (no real eval set) · No restart-resume demo · Approve/Reject only (no Edit) · No audit log · No idempotency on send · Fake v1→v2→v3 metrics · **Slack post AFTER interrupt** (graph hangs forever) · Read-MCP exposing send_email (capability bleed).

## MCPs configured (Claude Code dev environment)

- **Context7** — live LangGraph/LangSmith docs; query before writing graph code.
- **Sequential Thinking** — multi-step planning.
- **GitHub** — *needs PAT swapped in* (currently placeholder).

## Links

- Repo: https://github.com/Ranjith36963/hitl-support-agent
- Bitext (external benchmark — 10-intent first batch wired in; see `eval/bitext_findings.md`): https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset
