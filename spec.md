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

### Graph flow

```
[Ticket In]
    ↓
[PII Redact] ← middleware
    ↓
[1. Classify Intent] → intent + confidence
    ↓
[2. Retrieve Context] ← MCP filesystem
    ↓
[3. Draft Response] → draft + confidence
    ↓
[Decision Router]
    ↓ needs approval?
[INTERRUPT — wait for human]
    ↓
[Revalidate Context] ← check if stale
    ↓
[4. Send Response] ← MCP email, idempotent
    ↓
[5. Log Audit] ← append-only
    ↓
[End]
```

### Routing rules

Auto-send if ALL true:
- Confidence > 0.85
- Not refund-related
- No angry sentiment
- Intent in safe list (FAQ, info, basic technical)

Human approval if ANY true:
- Refund/money mentioned
- Angry sentiment
- Confidence < 0.85
- Edge case intent
- Policy-sensitive

---

## 5. State Schema

```python
class AgentState(TypedDict):
    # Identity
    ticket_id: str
    thread_id: str
    idempotency_key: str
    graph_version: str

    # Input
    customer_message: str
    customer_history: list
    context_hash: str

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
    approval_status: str  # pending/approved/edited/rejected/expired
    approver_id: str
    approval_timestamp: str
    sla_deadline: str

    # Execution
    send_status: str  # pending/in_flight/sent/failed

    # Terminal
    final_state: str  # sent/expired/failed_retryable/failed_manual

    # Audit
    audit_log: list  # append-only
    cost_breakdown: dict
    trace_url: str
```

---

## 6. Nodes Detailed

### PII Redact (middleware)

- Runs before any LLM call
- Redacts: emails, credit card numbers, phone numbers
- Replaces with tokens: `[EMAIL_1]`, `[CC_1]`
- Restores in final response

### 1. Classify Intent

- Input: customer message
- Output: intent label + confidence score
- LLM call via OpenRouter
- Intents: refund, technical, billing, complaint, FAQ, other
- Also extracts: sentiment, risk flags

### 2. Retrieve Context

- Calls MCP server: `get_customer_history`, `get_kb_article`
- Returns: past tickets, relevant policy docs
- Stores context hash for stale-check later

### 3. Draft Response

- Input: ticket + classified intent + retrieved context
- Output: draft response + confidence score
- LLM call with policy grounding
- Draft saved as `original_draft`

### Decision Router

- Conditional edge based on routing rules above
- Routes to: auto-send OR interrupt

### Interrupt Gate

- Uses LangGraph `interrupt_before`
- Saves checkpoint
- Waits for external approval signal
- Resumes via `Command(resume=...)`

### Revalidate Context

- Runs ONLY if approval took >15 minutes
- Re-fetches customer context
- Compares to original `context_hash`
- If changed → re-route to drafter
- If same → proceed to send

### 4. Send Response

- Idempotent — checks `send_status` before action
- Calls MCP server: `send_email`
- Updates `send_status`: pending → in_flight → sent
- Retries on transient failures

### 5. Log Audit

- Append-only entry
- Records: timestamp, node, decision, model, cost, trace_url
- Includes both `original_draft` and `final_draft` if edited

---

## 7. HITL Design

### Approval UI shows

- Customer message + thread history
- Detected intent + confidence
- Risk flags + which rule triggered
- Draft response (editable)
- Policy excerpt that triggered escalation
- Three actions: Approve / Edit & Approve / Reject

### Long wait handling

- State persisted in SQLite checkpointer
- SLA deadline stored in state
- Default SLA: 24 hours

### If approval never comes

- Low risk → auto-close, log event
- Medium risk → escalate to backup queue
- High risk → hold indefinitely, manual review only

### Approve-with-edits

- Human edits draft in UI
- Both `original_draft` and `final_draft` saved
- Audit log shows both versions

---

## 8. MCP Integration

Build ONE custom MCP server. Exposes 3 tools:

- `get_customer_history(customer_id)` → past tickets
- `get_kb_article(query)` → policy/KB docs
- `send_email(to, subject, body)` → mock send

Why custom MCP server:
- Shows you can BUILD MCP, not just consume
- Bigger differentiator than using existing MCP
- 17% of agent jobs already require MCP (April 2026 data)
- Trending up for 2027

README line:
> "Tools integrated via Model Context Protocol (MCP) — production pattern, not hardcoded SDK calls. Custom MCP server included in repo."

---

## 9. LangSmith Evals

### Setup

- Tracing enabled via env var
- Every run tagged with `graph_version: v1/v2/v3`
- Public trace links in README

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
│   └── support_tools.py        # Custom MCP server
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

1. **Custom MCP server** (not just consuming)
2. **False auto-send rate** as primary safety metric
3. **Approve-with-edits** flow with version history
4. **Stale state revalidation** after long approval wait
5. **PII masking middleware** before LLM calls
6. **Public LangSmith trace links** in README
7. **Failure slice analysis** (not just overall accuracy)
8. **Kill-server-resume** demo recorded

Most projects skip all 8. Hit even 5 and you're top 5%.

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

- All 5 nodes work end-to-end
- Custom MCP server runs and is called by graph
- Approval UI works
- Kill-server-resume demo recorded
- Approve-with-edits demo recorded
- 5 evaluators run on 50-ticket dataset
- v1 → v2 → v3 metrics table populated with REAL numbers
- README complete with all sections
- Public LangSmith trace links in README
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
