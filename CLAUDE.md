# HITL Customer Support Agent — Project Memory

> Full spec lives in `spec.md`. This file is the always-loaded context for every Claude Code session — kept short on purpose.

## What we're building

A Human-in-the-Loop customer-support agent. Reads tickets → classifies intent → drafts reply → **pauses for human approval when risky** → resumes after approval → sends → audit-logs everything. Built with **LangGraph + LangSmith**. 24-hour build window. Portfolio piece for AI Engineer roles in 2027.

## Tech stack (do not substitute without asking)

| Layer | Tool | Notes |
|---|---|---|
| Orchestration | **LangGraph** | Use `interrupt_before` for HITL gate, SQLite checkpointer for durability |
| Observability + evals | **LangSmith** | Tag every run with `graph_version: v1/v2/v3` |
| LLM | **OpenRouter** (DeepSeek V3 free) | Single model, no router |
| State persistence | **SQLite** | Postgres documented as prod path, not built |
| Backend | **FastAPI** | Approval endpoints + serves UI |
| Tools | **Custom MCP server** | 3 tools — `get_customer_history`, `get_kb_article`, `send_email` |
| Approval UI | Plain HTML/JS | No framework |
| Dataset | **Bitext Customer Support** | 50-ticket sample (40 dev / 10 holdout) |

## Graph nodes (in order)

1. **PII Redact** (middleware) — emails, CCs, phones → `[EMAIL_1]` tokens, restore at end
2. **Classify Intent** — intent + confidence + sentiment + risk_flags
3. **Retrieve Context** — via custom MCP; store `context_hash` for stale-check
4. **Draft Response** — saved as `original_draft`
5. **Decision Router** — auto-send vs interrupt
6. **Interrupt Gate** — `interrupt_before`, checkpoint, wait for `Command(resume=...)`
7. **Revalidate Context** — only if approval took >15 min; compare `context_hash`
8. **Send Response** — idempotent on `send_status`, via MCP `send_email`
9. **Log Audit** — append-only; both drafts saved if edited

## Routing rules (lift directly into `policy.py`)

**Auto-send** if ALL: `confidence > 0.85` AND not refund AND not angry AND intent in `{FAQ, info, basic_technical}`.

**Human approval** if ANY: refund/money mention, angry sentiment, `confidence < 0.85`, edge-case intent, policy-sensitive.

**False auto-send rate is the primary safety metric. Target = 0%.**

## State schema essentials

`AgentState` is a `TypedDict` with these required keys: `ticket_id`, `thread_id`, `idempotency_key`, `graph_version`, `customer_message`, `context_hash`, `intent`, `intent_confidence`, `sentiment`, `risk_flags`, `original_draft`, `final_draft`, `requires_approval`, `approval_status` (pending/approved/edited/rejected/expired), `send_status` (pending/in_flight/sent/failed), `final_state` (sent/expired/failed_retryable/failed_manual), `audit_log` (append-only), `cost_breakdown`, `trace_url`. See `spec.md §5` for the full block.

## Folder layout (target)

```
src/        graph.py  nodes.py  state.py  llm.py  policy.py  pii.py  mcp_client.py  server.py
mcp_server/ support_tools.py
eval/       dataset.py  evaluators.py  run_experiments.py
ui/         approve.html
data/       bitext_sample.csv
tests/      test_resume.py
```

## Build conventions

- **Type hints on every function.** Pydantic for I/O models.
- **Async-first** for FastAPI handlers and MCP client calls.
- **No bare `except`.** Catch specific exceptions; log via LangSmith trace.
- **All secrets via env vars.** `.env.example` committed, `.env` git-ignored. Never hardcode `OPENROUTER_API_KEY`, `LANGSMITH_API_KEY`, or GitHub PAT.
- **Idempotency on `send_email`** — re-running the graph must never double-send.
- **Append-only audit log** — never mutate existing entries; log both `original_draft` and `final_draft` when human edits.
- **Live LangGraph/LangSmith APIs change fast** — when writing graph or tracing code, query Context7 first; do not rely on training-data syntax.

## Red flags — do not ship with these

- ❌ Hardcoded API keys
- ❌ "Works on my one test case" — no eval dataset
- ❌ No restart/resume demo
- ❌ Approve/reject only (must support edit-and-approve)
- ❌ No audit log
- ❌ No idempotency on send
- ❌ Fake metrics in v1→v2→v3 table — fill in only after real eval runs

## 24-hour timeline (high-level)

H0–2 setup · H2–6 core graph · H6–9 MCP + PII · H9–13 HITL flow · H13–16 LangSmith evals · H16–19 demos · H19–22 polish · H22–24 ship.

## Three demo recordings required

1. **Durable execution** — kill server mid-interrupt, restart, approve, resumes from exact state.
2. **Approve-with-edits** — refund ticket; human edits draft; audit log shows both versions.
3. **SLA timeout** — 24h no response → auto-escalate to backup queue.

## MCPs configured for this repo

- **Context7** — for live LangGraph/LangSmith docs (already connected globally; query before writing graph code)
- **Sequential Thinking** — for graph design / multi-step planning
- **GitHub** — *needs PAT swapped in before first use* (currently registered with `YOUR_GITHUB_PAT_HERE`)

## Links

- Spec: `spec.md` (full source of truth)
- Repo: https://github.com/Ranjith36963/hitl-support-agent
- Bitext dataset: https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset
