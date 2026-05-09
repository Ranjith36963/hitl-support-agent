# HITL Customer Support Agent — Project Memory

> Always-loaded. Kept tight. Three docs are source of truth — point at them, don't duplicate.

## Three source docs

| Doc | When to open it |
|---|---|
| `spec.md` | Build spec — scope, timeline, sign-off, full state schema (§5), implementation rules (§6.5) |
| `docs/architecture.md` | Diagrams, env-var table, sequence, state machine, codebase map, LangSmith tag table |
| `HOW_IT_WORKS.md` | End-to-end product narrative (paste into README, walk through in interviews) |

## Honesty rule (non-negotiable)

- **Never work in desperation.** If something is broken, blocked, or unclear — stop and say so plainly. Do not paper over a failing test, mock around a broken integration, or skip a step to look productive.
- **Never sugarcoat.** Tell bad news in the same plain language as good news. A failing test stays failing until it's actually fixed; a half-working feature is described as half-working, not as "shipped."

## What we're building

Agent-first, human-on-demand customer support agent. **Real Gmail** in/out (IMAP IDLE / SMTP), **real Slack** with 6 channels routed by priority, durable LangGraph workflow, **three capability-isolated MCP servers**. Mock CRM + fictional **ACME SaaS Co** policy corpus. 24h portfolio build, agent owns the workflow, humans get pulled in only when needed.

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
| Eval data | Bitext Customer Support (50-ticket sample, 40 dev / 10 holdout) |

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
data/   bitext_sample.csv  customers_seed.json  acme_policies.md
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

1. **Durable execution** — kill server mid-interrupt → restart → Slack approve → real email arrives.
2. **Approve-with-edits** — Slack modal edit; audit log shows both drafts.
3. **SLA timeout** — 24h no Slack response → auto-escalate to `manual_queue` + Slack notice.

## Red flags — do not ship with these

Hardcoded keys · Single test case (no Bitext eval) · No restart-resume demo · Approve/Reject only (no Edit) · No audit log · No idempotency on send · Fake v1→v2→v3 metrics · **Slack post AFTER interrupt** (graph hangs forever) · Read-MCP exposing send_email (capability bleed).

## MCPs configured (Claude Code dev environment)

- **Context7** — live LangGraph/LangSmith docs; query before writing graph code.
- **Sequential Thinking** — multi-step planning.
- **GitHub** — *needs PAT swapped in* (currently placeholder).

## Links

- Repo: https://github.com/Ranjith36963/hitl-support-agent
- Bitext: https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset
