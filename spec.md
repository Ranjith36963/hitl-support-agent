# HITL Customer Support Agent — Build Spec

Portfolio project for AI Engineer roles in 2027.

Built with LangGraph + LangSmith.

---

## 1. Project Overview

**What it does:**

Reads customer support tickets. Classifies intent. Drafts replies. Pauses before sending if approval needed. Human approves via web UI. Agent resumes and sends.

**Why it matters in 2027:**

- Enterprises deploy HITL agents, not full autonomy
- Companies want safe, auditable, durable agents
- Pattern maps 1:1 to real production needs

**Build window:** 24 hours.

---

## 2. Why This Project Wins in 2027

Hiring signals it hits:

- Durable execution (survives server restart)
- Human-in-the-loop approval gates
- LangSmith tracing on every step
- Real evals on real data
- Audit trail for every decision
- MCP integration (free differentiator)
- Cost + latency tracking
- v1 → v2 → v3 measurable improvement

Skips weak signals:
- Generic chatbot
- Toy demo
- "Works on my machine"
- No metrics

---

## 3. Tech Stack

| Layer | Tool |
|---|---|
| Orchestration | LangGraph |
| Observability + Evals | LangSmith |
| LLM | OpenRouter (DeepSeek V3 free) |
| State persistence | SQLite (Postgres mode documented) |
| Backend | FastAPI |
| Approval UI | Simple HTML/JS |
| Tool integration | MCP (custom server) |
| Dataset | Bitext Customer Support |
| Build agent | Claude Code |

---

## 4. Architecture

> Full Mermaid renderings live in `docs/architecture.md` (simple 30-second view + detailed flow + sequence + state machine). This section is the spec-level summary.

### Graph flow

```
[Ticket In]
    ↓
[PII Redact] ← middleware
    ↓
[Classify Intent] → intent + confidence + sentiment + risk_flags
    ↓
[Retrieve Context] ── calls ──> [MCP READ Server]
    ↓
[Draft Response] → draft + draft_confidence
    ↓
[Policy Risk Check]  ◇  ── risk detected ──┐
    ↓ no risk                              ↓
[Confidence Check]   ◇  ── below 0.85 ─→ [Interrupt Gate]
    ↓ above threshold                      ↓
    ↓                                  [Approval UI] ── reject ─→ [Reject Check ◇]
    ↓                                      ↓ approve/edit         ↓ count<3 → loop to Draft
    ↓                                  [Elapsed > 15min? ◇]       ↓ count≥3 → [Manual Queue]
    ↓                                      ↓ yes
    ↓                                  [Revalidate context_hash ◇]
    ↓                                      ↓ hash changed
    ↓                                  [Summarize Changes] → delta panel → back to Approval UI
    ↓                                      ↓ hash unchanged
    └──────────────────────────────────────┴──→ [Finalize Action] (PII restore + payload)
                                                       ↓
                                          [Send Response] ── calls ──> [MCP WRITE Server]
                                                       ↓ idempotent on send_idempotency_key
                                          [Append-only audit log + trace metadata]
                                                       ↓
                                                     [End]
```

### Routing rules — two sequential gates, not one fuzzy router

**Gate 1 — Policy Risk Check.** Escalate if **any** true:
- Refund or money mention
- Angry sentiment
- Edge-case intent
- Explicit policy match (cancellation, billing dispute, account recovery, legal)

**Gate 2 — Confidence Check** (only runs if Gate 1 passes). Escalate if `intent_confidence < 0.85` OR `draft_confidence < 0.85`.

**Auto-send** only when both gates pass AND `intent in {FAQ, info, basic_technical}`.

**Primary safety metric:** `false_auto_send_rate = 0%`

---

## 5. State Schema

```python
class AgentState(TypedDict):
    # Identity
    ticket_id: str
    thread_id: str                       # equals ticket_id (stable resume pointer)
    graph_version: str                   # v1/v2/v3

    # Input
    customer_message: str
    customer_history: list
    context_hash: str                    # hash of retrieved_context for stale-check

    # Classification
    intent: str
    intent_confidence: float
    sentiment: str
    risk_flags: list

    # Drafting
    original_draft: str
    final_draft: str
    draft_confidence: float

    # Approval
    requires_approval: bool
    approval_status: str                 # pending/approved/edited/rejected/expired/cancelled/superseded
    approver_id: str
    approval_timestamp: str
    sla_deadline: datetime

    # Idempotency on send
    send_idempotency_key: str            # set once at entry; used by Send to deduplicate retries
    sent_message_id: Optional[str]       # populated after MCP returns; presence == "already sent"

    # Execution
    send_status: str                     # pending/in_flight/sent/failed_retryable/failed_manual

    # Loop guards (prevent infinite retries / rejections)
    human_rejection_count: int           # increments on each rejection; >= 3 routes to manual_queue
    send_retry_count: int                # increments on each transient send failure; >= 3 routes to failed_manual

    # Long-pause edge cases
    ticket_external_status: str          # open / closed_by_customer / superseded

    # Terminal
    final_state: str                     # sent/rejected/expired/cancelled/superseded/failed_manual

    # Audit + observability
    audit_log: list                      # append-only
    cost_breakdown: dict                 # {classify: 0.0001, draft: 0.0008, ...}
    total_tokens: int
    total_cost_usd: float
    trace_url: str
```

---

## 6. Nodes Detailed

### PII Redact (middleware)

- Runs before any LLM call
- Redacts: emails, credit card numbers, phone numbers
- Replaces with tokens: `[EMAIL_1]`, `[CC_1]`
- Restoration happens in **Finalize Action** before send

### Classify Intent

- Input: customer message
- Output: intent label + confidence + sentiment + risk_flags
- LLM call via OpenRouter
- Intents: refund, technical, billing, complaint, FAQ, other

### Retrieve Context

- Calls MCP **READ** server only (`get_customer_history`, `get_kb_article`)
- Returns: past tickets, relevant policy docs (with KB sentence quotes for justification)
- Stores `context_hash` for stale-check later

### Draft Response

- Input: ticket + classified intent + retrieved context
- Output: draft response + `draft_confidence`
- LLM call with policy grounding
- Draft saved as `original_draft`

### Policy Risk Check (Gate 1)

- Conditional edge — fast deterministic check
- Escalates if any: refund/money, angry, edge-case intent, policy match
- If risk → Interrupt Gate (skip Gate 2)
- If no risk → Confidence Check

### Confidence Check (Gate 2)

- Only runs if Gate 1 passes
- Escalates if `intent_confidence < 0.85` OR `draft_confidence < 0.85`
- Above threshold → Finalize Action (auto-send path)
- Below threshold → Interrupt Gate

### Interrupt Gate

- Uses LangGraph `interrupt()` — **dedicated node, no side effects** (see §6.5)
- LangGraph checkpointer persists state at super-step boundaries; this node just pauses execution
- Resumes via `Command(resume=...)` from FastAPI approval endpoint

### Approval UI

- Renders the approval form (see §7 for layout)
- Three actions: Approve / Edit & Approve / Reject
- On reject → Reject Check (count-bounded; see below)
- On approve/edit → Elapsed Check

### Reject Check

- Conditional edge on `human_rejection_count`
- If `count < 3` → loop back to Draft Response (increments count, re-drafts with rejection feedback)
- If `count >= 3` → Manual Queue (terminal)

### Elapsed Check

- Conditional edge on `(now - approval_request_time) > 15 min`
- If no → Finalize Action (skip revalidation)
- If yes → Revalidate Context

### Revalidate Context

- Re-fetches customer context via MCP READ server
- Compares fresh hash to stored `context_hash`
- If unchanged → Finalize Action
- If changed → Summarize Changes

### Summarize Changes

- LLM call: compute delta between original context and fresh context
- Produces structured delta (e.g., `account_status: Active → Pending`)
- Routes back to Approval UI with the delta panel above the draft
- Human re-decides Approve/Edit/Reject with full information

### Finalize Action

- **Pure compose step — no irreversible effects yet**
- Restores PII tokens (`[EMAIL_1]` → real email)
- Assembles final payload (recipient, subject, body, idempotency key)
- Hands off to Send Response

### Send Response

- Calls MCP **WRITE** server only (`send_email`)
- Idempotent on `send_idempotency_key` — re-running the graph cannot double-send
- `send_status`: pending → in_flight → sent
- Transient failures retry up to 3 times; after that → `failed_manual` → Manual Queue

### Log Audit

- Append-only entry to audit log
- Records: timestamp, node, decision, model, cost, tokens, trace_url
- Saves both `original_draft` and `final_draft` when human edited
- Includes the KB justification used for escalation (when applicable)

### Manual Queue (terminal)

- Receives tickets from: rejection-count exceeded, SLA expired, send retries exhausted
- Customer is notified that their ticket is being handled by a human
- Audit entry logged before terminal exit

---

## 6.5 Implementation Rules (LangGraph-specific)

These two rules prevent the most common bugs when implementing HITL on LangGraph. **Not optional — getting either wrong silently breaks the agent.**

### Rule 1 — `interrupt()` lives in its own dedicated node

The `interrupt()` call must be alone in its node. No DB writes, MCP calls, audit log entries, or any other side effect can share the node.

**Why:** When LangGraph resumes after `interrupt()`, the node containing the call restarts from the beginning. Any code before the interrupt runs again on every resume — side effects in that node duplicate.

**Pattern:** Put side effects in *downstream* nodes that run exactly once per resume. The interrupt node does only the pause.

### Rule 2 — Never wrap `interrupt()` in `try/except`

`interrupt()` works by raising a special exception that the LangGraph runtime catches. A broad `try/except` swallows the exception; the graph either hangs or skips the pause entirely.

**Why:** Generic "robust error handling" patterns break HITL. It's tempting to wrap everything for safety, but the interrupt path must bubble up to the runtime.

**Pattern:** Error handling lives in *other* nodes, not the interrupt node.

---

## 7. HITL Design

The UI is built so a human can decide in ~10 seconds, not 2 minutes.

### Approval UI layout

1. **Customer message + thread history**
2. **Why I paused** — risk breakdown panel:
   ```
   refund_detected:  true
   sentiment:        angry (0.91)
   intent_conf:      0.72  (below 0.85 threshold)
   draft_conf:       0.81  (below 0.85 threshold)
   policy_match:     refund_over_$100

   Justification (from KB):
   "Refunds above $100 require manager approval per Policy 4.2.1"
   ```
   The justification quote is pulled from `retrieved_context` — the exact KB sentence the agent matched against. This is what makes the surface explainable rather than "trust me."
3. **Detected intent + confidence**
4. **Draft response** (editable text area)
5. **Three actions:** Approve · Edit & Approve · Reject

### Stale-context delta panel (only if context changed during pause)

When `Revalidate` detects `context_hash` changed, the UI re-renders with an extra panel above the draft:

```
Context changed since this draft was created (2h 14min ago):
  - account_status:  Active  →  Pending
  - open_tickets:    1       →  3
```

Human re-decides with the new information.

### Long wait handling

- State persisted by SQLite checkpointer at every super-step boundary
- `sla_deadline` stored in state (default 24h)
- If SLA expires with no human response → Manual Queue

### Bounded rejections

- `human_rejection_count` increments on each Reject click
- If `count < 3` → re-draft with rejection feedback, return to Approval UI
- If `count >= 3` → Manual Queue (prevents infinite redraft loops)

### Approve-with-edits

- Human edits draft in UI before approving
- Both `original_draft` and `final_draft` saved to append-only audit log
- Audit log shows both versions for review and as future fine-tuning data

---

## 8. MCP Integration

Build **TWO** custom MCP servers — split read vs write for least-privilege isolation:

### MCP READ Server (`support_read.py`)
- `get_customer_history(customer_id)` → past tickets
- `get_kb_article(query)` → policy/KB docs (returns full sentence quotes for justification)

Connected to: **Retrieve Context** node and **Revalidate Context** node only.

### MCP WRITE Server (`support_write.py`)
- `send_email(to, subject, body, idempotency_key)` → mock send

Connected to: **Send Response** node only.

### Why split read vs write

- **Prompt-injection defense.** If a malicious customer message smuggles instructions through the retrieved context, the agent at retrieval time has no path to `send_email`. A jailbreak during Retrieve cannot exfiltrate or send anything.
- **Audit clarity.** Read calls are noisy and frequent; write calls are rare and high-stakes. Separating them makes the write log trivially auditable.
- **2027 hiring signal.** Least-privilege at the tool layer is a real production pattern that most portfolio HITL projects skip.

### Why custom MCP servers (vs hardcoded SDK calls)

- Shows you can BUILD MCP, not just consume
- 17% of agent jobs already require MCP (April 2026 data); trending up for 2027
- Splitting into two servers shows MCP composition, not just single-server usage

README line:
> "Tools integrated via Model Context Protocol (MCP) — split into read and write servers for least-privilege isolation against prompt injection. Production pattern, not hardcoded SDK calls."

---

## 9. LangSmith Evals

### Setup

- Tracing enabled via env var
- Public trace links in README
- Every trace tagged with the full set below for failure-slice analysis:

| Tag | Values |
|---|---|
| `graph_version` | `v1` / `v2` / `v3` |
| `intent` | `refund` / `billing` / `technical` / `complaint` / `FAQ` / `other` |
| `outcome` | `auto_send` / `escalated` |
| `human_edited` | `true` / `false` |
| `final_state` | `sent` / `rejected` / `expired` / `cancelled` / `superseded` / `failed_manual` |
| `risk_flags` | comma-joined list (e.g., `refund,angry`) |
| `confidence_bucket` | `lt_0.5` / `0.5-0.85` / `gte_0.85` |

### Required evaluators

1. **Intent accuracy** — exact match against labels
2. **Response quality** — LLM-as-judge with rubric
3. **Escalation precision** — did it route to human correctly?
4. **False auto-send rate** — sent without approval when it shouldn't have (SAFETY METRIC)
5. **Failure slice analysis** — break down by angry / refund / multi-intent

### Dataset

- Source: Bitext Customer Support dataset
- 50 test cases for 24h build (sample)
- Mix of intents, sentiments, risk levels
- Hand-labeled expected approval status

### Target metrics (v3)

| Metric | Target |
|---|---|
| Intent accuracy | >85% |
| Response quality | >4.0/5 |
| Escalation precision | >90% |
| False auto-send rate | 0% |
| Avg latency | <3s |
| Cost per ticket | <$0.01 |

### Iteration story (v1 → v3)

| Version | Escalation | Accuracy | False AutoSend | Cost |
|---|---|---|---|---|
| v1 | TBD | TBD | TBD | TBD |
| v2 | TBD | TBD | TBD | TBD |
| v3 | TBD | TBD | TBD | TBD |

Numbers filled in AFTER actual eval runs. No fake metrics.

---

## 10. Data

**Source:** Bitext Customer Support Dataset
- Public, free
- 27 intents, real tickets
- Used for both training and eval

**Sample size for 24h build:** 50 tickets

**Splits:**
- 40 for development testing
- 10 held-out for final eval

---

## 11. Folder Structure

```
support-agent/
├── README.md
├── requirements.txt
├── .env.example
├── data/
│   └── bitext_sample.csv
├── mcp_server/
│   ├── support_read.py         # MCP READ server: get_customer_history, get_kb_article
│   └── support_write.py        # MCP WRITE server: send_email (least-privilege isolation)
├── src/
│   ├── graph.py                # LangGraph workflow
│   ├── nodes.py                # Node functions
│   ├── state.py                # State schema
│   ├── llm.py                  # OpenRouter client
│   ├── policy.py               # Approval rules
│   ├── pii.py                  # PII redaction
│   ├── mcp_client.py           # MCP integration
│   └── server.py               # FastAPI + UI
├── eval/
│   ├── dataset.py              # LangSmith dataset upload
│   ├── evaluators.py           # Custom evals
│   └── run_experiments.py      # v1/v2/v3 runs
├── ui/
│   └── approve.html            # Approval interface
├── tests/
│   └── test_resume.py          # Restart test
└── demo/
    └── demo_script.md          # 2-min video script
```

---

## 12. 24-Hour Timeline

**Hours 0-2: Setup**
- OpenRouter account + API key
- LangSmith account + API key
- Project folder, deps installed
- Bitext dataset downloaded (50 sample)
- Claude Code initialized

**Hours 2-6: Core graph**
- State schema
- 5 main nodes coded
- LangGraph compiled
- SQLite checkpointer wired
- Run 1 ticket end-to-end

**Hours 6-9: MCP + middleware**
- Custom MCP server built
- 3 tools exposed
- PII redaction middleware
- Wired into graph

**Hours 9-13: HITL flow**
- `interrupt_before` on send node
- FastAPI endpoint for approval
- Approval UI (HTML)
- Approve-with-edits flow
- Resume after approval works

**Hours 13-16: LangSmith**
- Tracing enabled
- 50-ticket dataset uploaded
- 5 evaluators built
- Eval suite runs

**Hours 16-19: Killer demos**
- Kill server → resume test
- Stale state revalidation
- SLA timeout flow
- Audit log review

**Hours 19-22: Polish**
- Error handling pass
- README writing
- Architecture diagram (Mermaid)
- 10 edge cases tested

**Hours 22-24: Ship**
- Record 2-min demo video
- Public LangSmith trace links
- GitHub push
- Final README pass

---

## 13. Demo Moments (Record All 3)

### Demo 1 — Durable execution

1. Trigger ticket
2. Show LangSmith trace
3. Agent pauses at interrupt
4. **KILL THE SERVER**
5. Wait 5 seconds
6. Restart server
7. Approval UI still shows pending
8. Click approve
9. Agent resumes from exact state
10. Send completes

### Demo 2 — Approve-with-edits

1. Refund ticket comes in
2. Agent drafts response
3. Pauses for approval
4. Human edits draft in UI
5. Click "Edit & Approve"
6. Agent sends edited version
7. Show audit log: both versions saved

### Demo 3 — SLA timeout

1. Ticket triggers approval
2. No human responds for 24h (simulated)
3. SLA fires
4. Auto-escalate to backup queue
5. Audit log shows expiration

---

## 14. README Structure

```markdown
# HITL Customer Support Agent

[2-min demo video link]
[Live LangSmith trace links]

## What it does
3 sentences max.

## Why HITL matters in 2027
1 paragraph on safety + enterprise pattern.

## Architecture
Mermaid diagram of graph.
State schema summary.

## Tech stack
List with reasoning.

## Eval results
Metrics table v1 → v2 → v3.
Failure slice breakdown.
Link to LangSmith experiment.

## Durable execution demo
GIF of kill-server-resume.

## MCP integration
Why custom MCP server.
3 tools exposed.

## Failure modes I handled
List 5-7 specific failures + fixes.

## What I'd build next
Postgres for production.
Slack approval channel.
Online evals on live traffic.

## Run locally
3 commands.
```

---

## 15. Failure Modes (Document Honestly)

To document in README:

- "Confidence scores were poorly calibrated in v1 — added few-shot examples in v2"
- "PII redaction caught emails but missed phone formats — added regex pass"
- "Interrupt resume failed when approval took >1h until I added context revalidation"
- "SQLite locked under concurrent writes — would use Postgres in production"
- "LLM-judge was inconsistent — added rubric + 3-run averaging"
- "Refund tickets had high false-negative on escalation until I added explicit policy check"

Honest engineering = senior signal.

---

## 16. Red Flags to Avoid

Never ship with these:

- ❌ "It works on my test case" only
- ❌ No real eval dataset
- ❌ No metrics in README
- ❌ Hardcoded API keys
- ❌ No restart demo
- ❌ Approve/reject only (no edit flow)
- ❌ No audit log
- ❌ No idempotency on send
- ❌ Buzzwords without depth
- ❌ Overcomplicated graph with no reason

---

## 17. Differentiators

Things most portfolios won't have:

1. **Custom MCP servers, split read vs write** (least-privilege defense against prompt injection)
2. **Two-gate routing** — separate Policy Risk and Confidence checks, not one fuzzy router
3. **False auto-send rate** as primary safety metric
4. **Approve-with-edits** flow with version history (both drafts in audit log)
5. **Stale state revalidation** with delta panel — human re-decides on context change, not silent redraft
6. **Bounded rejection loop** (3-strike rule routes to Manual Queue)
7. **Idempotent send** with explicit `send_idempotency_key` — survives retries and resumes
8. **PII masking middleware** with explicit Finalize-Action restoration
9. **KB justification quote** in Approval UI — explainable AI, not "trust me"
10. **Public LangSmith trace links** with full tag set for failure-slice analysis
11. **Kill-server-resume** demo recorded
12. **Implementation Rules** documented (interrupt() in dedicated node, no try/except)

Most projects skip nearly all of these. Hit 7+ and you're top 1–3%.

---

## 18. What This Spec Does NOT Include

Cut for 24h scope:

- ❌ Postgres deployment (SQLite for demo, Postgres mentioned as prod path)
- ❌ Slack approval bot (web UI only)
- ❌ Multi-model routing (single model)
- ❌ Multi-agent critique (single drafter)
- ❌ Live deployment (local demo)
- ❌ Online evals (offline only on 50 tickets)
- ❌ RBAC / auth (skip)
- ❌ Shadow mode backtesting

Document these as "future work" in README.

---

## 19. Sign-off Criteria

Project is "done" when:

- All graph nodes work end-to-end (PII Redact → Classify → Retrieve → Draft → Policy Gate → Confidence Gate → Interrupt → UI → Reject Check → Elapsed Check → Revalidate → Summarize Changes → Finalize → Send → Audit)
- **Both** MCP servers (read + write) run and are called by the correct nodes
- Approval UI shows the "Why I paused" panel with KB justification quote
- Stale-context delta panel renders when `context_hash` changes during pause
- Implementation Rules followed (interrupt() in dedicated node, no try/except wrapping)
- Bounded rejection loop verified (3-strike → Manual Queue)
- Idempotent send verified (re-running graph cannot double-send)
- Kill-server-resume demo recorded
- Approve-with-edits demo recorded (both drafts in audit log)
- SLA timeout demo recorded (auto-escalate to Manual Queue)
- 5 evaluators run on 50-ticket dataset
- v1 → v2 → v3 metrics table populated with REAL numbers
- README complete with all sections
- Public LangSmith trace links with full tag set in README
- 2-min demo video uploaded
- GitHub repo public
- 5+ specific failure modes documented honestly

---

## 20. Source of Truth

Facts used in this spec:

- LangChain Job Market 2026 report: MCP in 16.9% of agentic listings
- LangChain 2026 State of Agent Engineering: observability is table stakes
- LangGraph docs: durable execution, interrupts, checkpointers
- LangSmith docs: evaluators, datasets, tracing
- Bitext Customer Support Dataset: real public data

No fabricated metrics. No invented features. Numbers in eval tables filled in only after real runs.

---

End of spec.
