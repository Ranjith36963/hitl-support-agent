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
| Customer I/O (inbound + outbound) | **Real email — Gmail IMAP + SMTP** |
| Internal approval channel | **Real Slack** with multi-channel routing (Bolt SDK + Block Kit) |
| Edit fallback UI | Minimal HTML/JS (only opened from the Slack "Edit" button) |
| Tool integration | MCP (custom servers, split read/write) |
| Policy corpus | Fictional **ACME SaaS Co** policy docs (`data/acme_policies.md`) |
| Eval dataset | Bitext Customer Support |
| Build agent | Claude Code |

### What's real vs mocked

| Layer | Real | Mocked |
|---|---|---|
| Email I/O | Gmail IMAP (in) + SMTP (out) | — |
| Approval channel | Slack workspace + multi-channel routing | — |
| LangGraph + LangSmith | Full implementation | — |
| MCP servers | Custom read + write servers | — |
| Eval data | Bitext dataset | — |
| Customer database / CRM | — | `data/customers_seed.json` (Salesforce-shape) |
| Policy corpus | — | `data/acme_policies.md` (fictional ACME SaaS Co) |
| Ticketing system | — | Direct email → LangGraph (no Zendesk) |

The README explicitly documents this split so reviewers see strategic mocking — production swap is an MCP config change, not a rewrite.

---

## 4. Architecture

> Full Mermaid renderings live in `docs/architecture.md` (simple 30-second view + detailed flow + sequence + state machine). End-to-end product narrative lives in `HOW_IT_WORKS.md`. This section is the spec-level summary.

### System layers

| # | Layer | Responsibility |
|---|---|---|
| 1 | Ingestion | IMAP in / SMTP out (Gmail) |
| 2 | Orchestration | LangGraph + SQLite checkpointer (durable execution) |
| 3 | Intelligence | LLM calls — Classify, Draft, Summarize Changes |
| 4 | Policy | Two-gate routing + channel selection + ACME KB retrieval |
| 5 | HITL | Slack notification, interrupt, action handler, edit modal |
| 6 | Execution | Finalize, idempotent send, audit log |
| 7 | Observability | LangSmith tracing, cost tracking, failure-slice tags |

### Graph flow (v3 — corrected ordering: Slack post BEFORE interrupt)

```
support@yourcompany.com  (real Gmail inbox)
    ↓ IMAP IDLE (preferred) or 30s poll fallback
[Email Listener] → builds ticket {ticket_id, email_thread_id, from, subject, body}
    ↓
[PII Redact] ← middleware (emails, CCs, phones → tokens)
    ↓
[Classify Intent] → intent + confidence + sentiment + risk_flags + risk_level
    ↓
[Enrich Context] ── calls ──> [MCP READ Server]
    │                            • get_crm_profile (Salesforce-shape mock)
    │                            • get_customer_history
    │                            • get_kb_article (ACME policies, sentence quotes)
    ↓
[Draft Response] → draft + draft_confidence
    ↓
[Policy Risk Check]  ◇  ── risk detected ──────────────────┐
    ↓ no risk                                              ↓
[Confidence Check]   ◇  ── below 0.85 ─────────────────────┤
    ↓ above threshold                                       ↓
    ↓                                              [Channel Router]  (priority overrides)
    ↓                                              1. legal/compliance → #support-legal
    ↓                                              2. enterprise + risk → #support-enterprise
    ↓                                              3. angry sentiment   → #support-complaints
    ↓                                              4. by intent         → #support-{refunds,
    ↓                                                                     technical, billing}
    ↓                                                       ↓
    ↓                                              [Slack Notification]  ── posts via ──>
    ↓                                              [MCP SLACK WRITE Server]
    ↓                                                  Block Kit message saved with
    ↓                                                  slack_message_ts in state
    ↓                                                       ↓
    ↓                                              [Interrupt Gate]  (dedicated node)
    ↓                                                  Only calls interrupt().
    ↓                                                  No side effects.
    ↓                                                  Checkpoint persists at super-step.
    ↓                                                       ↓
    ↓                                              ─── PAUSE ───
    ↓                                                       ↓
    ↓                                              FastAPI webhook receives
    ↓                                              Slack button click.
    ↓                                              Verifies HMAC-SHA256 signature
    ↓                                              and timestamp <5min.
    ↓                                              Issues Command(resume=action).
    ↓                                                       ↓
    ↓                                              ┌────────┼────────┐
    ↓                                              ↓        ↓        ↓
    ↓                                          reject    approve   edit (Slack views.open
    ↓                                          + reason             modal; on save → resume)
    ↓                                              ↓        ↓        ↓
    ↓                                       [Reject Check ◇]         ↓
    ↓                                          ↓ count<3 → loop to Draft
    ↓                                          ↓             (carries rejection_reason
    ↓                                          ↓              as redraft context)
    ↓                                          ↓ count≥3 → [Manual Queue]
    ↓                                                       ↓
    ↓                                              [Elapsed > 15min? ◇]   (config-driven)
    ↓                                                       ↓ yes
    ↓                                              [Revalidate context_hash ◇]
    ↓                                                       ↓ hash changed
    ↓                                              [Summarize Changes]
    ↓                                                       ↓ update_message: posts
    ↓                                                       ↓ delta on same Slack msg
    ↓                                                       ↓ → re-interrupts to wait
    ↓                                                       ↓ hash unchanged / re-confirmed
    └───────────────────────────────────────────────────────┴──→ [Finalize Action]
                                                                  (PII restore + payload
                                                                   + In-Reply-To AND References
                                                                   + Subject: Re: ...)
                                                                       ↓
                                                              [Send Email]
                                                                  (Gmail SMTP via MCP Email
                                                                   Write Server.
                                                                   App-layer idempotency:
                                                                   skip if sent_message_id
                                                                   already in state.)
                                                                       ↓
                                                              update_message:
                                                              "📤 Reply sent to <customer>"
                                                                       ↓
                                                              [Append-only audit log +
                                                               LangSmith trace closes]
                                                                       ↓
                                                                     [End]
                                                                       ↓
                                              support@yourcompany.com  ──[real SMTP]──> customer inbox
                                                                       (threaded under original)
```

**Critical ordering note:** Slack Notification runs BEFORE Interrupt Gate. Once `interrupt()` fires, execution pauses — nothing in that node runs after. Posting to Slack must happen first (its own node), then the Interrupt Gate (dedicated node that does only the pause). Reversing this order is a flow-correctness bug that pauses forever with no Slack message ever posted.

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

    # Customer-tier-aware routing inputs
    customer_tier: str                   # Free / SMB / Enterprise (from CRM)
    risk_level: str                      # none / financial / legal / compliance
    policy_matches: list                 # ["ACME 4.2.1", "ACME 7.1"] — KB sections that triggered escalation

    # Approval
    requires_approval: bool
    approval_status: str                 # pending/approved/edited/rejected/expired/cancelled/superseded
    approver_id: str                     # Slack user id of approver
    approval_timestamp: str
    sla_deadline: datetime

    # I/O channels (real email + real Slack)
    email_thread_id: str                 # original customer email Message-ID; used for In-Reply-To threading
    slack_channel: str                   # which channel the approval went to (e.g. "#support-complaints")
    slack_message_ts: str                # Slack message timestamp; used to update / resume on the right msg

    # Idempotency on send
    send_idempotency_key: str            # set once at entry; used by Send to deduplicate retries
    sent_message_id: Optional[str]       # populated after MCP returns; presence == "already sent"

    # Execution
    send_status: str                     # pending/in_flight/sent/failed_retryable/failed_manual

    # Loop guards (prevent infinite retries / rejections)
    human_rejection_count: int           # increments on each rejection; >= 3 routes to manual_queue
    rejection_reason: Optional[str]      # free-text from Slack reject modal; carried into next Draft as context
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

### Email Listener (entry point)

- Background task on `support@yourcompany.com` (Gmail). **Two modes:**
  - **Preferred:** IMAP IDLE — Gmail pushes a notification within ~1s of new mail. Implementation: `imaplib`'s IDLE keepalive loop, or `aioimaplib` for async.
  - **Fallback:** poll every ~30s if IDLE connection drops or is unsupported. The 30s figure is a fallback ceiling, not a target.
- **Production swap:** webhook-based (SendGrid Inbound Parse / Postmark / SES) for sub-second latency at scale. Swap is an MCP server change. Note in README.
- Each new email → builds a `ticket` object: `{ticket_id, email_thread_id, from, subject, body, received_at}`
- Pushes ticket into the LangGraph entry node
- `email_thread_id` (RFC-822 Message-ID) saved in state so the eventual reply threads correctly in the customer's inbox

### PII Redact (middleware)

- Runs before any LLM call
- Redacts: emails, credit card numbers, phone numbers
- Replaces with tokens: `[EMAIL_1]`, `[CC_1]`
- Restoration happens in **Finalize Action** before send

### Classify Intent

- Input: customer message (with PII redacted)
- Output: intent label + `intent_confidence` + sentiment + `risk_flags` + `risk_level`
- LLM call via OpenRouter
- Intents: refund, technical, billing, complaint, FAQ, other
- `risk_level` bucket: none / financial / legal / compliance (drives channel routing later)

### Enrich Context (formerly Retrieve Context)

- Calls MCP **READ** server only — three tools, in parallel where possible:
  - `get_crm_profile(customer_email)` → mock Salesforce-shape: `{customer_tier, contract_value, renewal_date, billing_status}`
  - `get_customer_history(customer_email)` → past 90 days of tickets / interactions
  - `get_kb_article(query)` → relevant chunks from `data/acme_policies.md` with full sentence quotes (used as the justification quote in Slack)
- Writes `customer_tier` to state (drives channel routing later)
- Stores `context_hash` (hash of all retrieved data) for the stale-check during long approval waits

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

### Channel Router (new — runs immediately after Interrupt Gate)

Picks the Slack channel using **priority-ordered overrides**. Higher priority wins when multiple match.

| Priority | Condition | Channel |
|---|---|---|
| 1 (highest) | `risk_flags` contains `legal` or `compliance` | `#support-legal` |
| 2 | `customer_tier == Enterprise` AND any `risk_flags` | `#support-enterprise` |
| 3 | `sentiment == angry` | `#support-complaints` |
| 4 (default) | route by `intent` | `#support-refunds` / `#support-technical` / `#support-billing` |

Writes `slack_channel` to state. This deterministic routing logic lives in `src/slack_router.py` and is unit-tested.

### Slack Notification (replaces single-channel "Approval UI")

- Calls MCP **SLACK WRITE** server's `post_approval_request` tool
- Posts a Block Kit message to the channel chosen by the router. Message contains:
  - Ticket summary (customer email + 1-line subject)
  - Customer card: `customer_tier`, contract value, history snapshot
  - **"Why I paused"** panel: risk flags, confidence scores, `policy_matches`
  - **KB justification quote** pulled from `retrieved_context` (verbatim ACME policy sentence)
  - Draft reply (expandable section)
  - Three action buttons: **Approve** · **Edit** · **Reject**
- Saves `slack_message_ts` to state — used to update the message in place ("✅ Approved by @sarah") and to resume on the correct message after a server restart
- LangGraph `interrupt()` is called *here, in this node, alone* — no other side effect (Implementation Rule 1)

### Slack Action Handler (FastAPI webhook)

- Receives Slack button clicks at `/slack/events` (Bolt SDK)
- **Signature verification (security boundary, not optional):**
  1. Read `X-Slack-Request-Timestamp` header
  2. Read raw request body (do not parse first — must hash bytes that arrived)
  3. Compute `HMAC-SHA256(signing_secret, "v0:" + timestamp + ":" + body)`
  4. Compare to `X-Slack-Signature` header (constant-time compare)
  5. Reject with 401 if mismatch OR if timestamp is older than 5 minutes (replay-attack defense)
- Routes after verification:
  - **Approve** → `Command(resume="approve")` resumes graph
  - **Edit** → opens a Slack modal via `views.open` (keeps human in Slack — preferred over redirecting to `ui/edit.html`); on modal submit, `final_draft` updates and `Command(resume="edit")` resumes
  - **Reject** → opens a brief modal with optional "Why?" text field, captures `rejection_reason` to state, increments `human_rejection_count`, then `Command(resume="reject")`
- After each action, calls `update_message` on the Slack Write Server to update the original message in place: `✅ Approved by @user · 22 sec` / `✏️ Edited & approved by @user · 47 sec` / `↩️ Rejected by @user — redrafting (2/3)`

### Reject Check

- Conditional edge on `human_rejection_count`
- If `count < 3` → loop back to Draft Response. The Draft node reads `rejection_reason` from state and incorporates it as additional context (e.g., *"The previous draft was rejected because: '<reason>'. Address that concern in the new draft."*). Increments `human_rejection_count`. The new draft is posted as a Slack thread reply on the *same* original message (preserves audit history visible to the team).
- If `count >= 3` → Manual Queue (terminal). Slack message gets a final `update_message`: *"🚦 3 rejections — manual queue."*

### Elapsed Check

- Conditional edge on `(now - approval_request_time) > 15 min`
- **15-minute threshold is a tunable engineering decision, not a magic number.** It balances revalidation cost (extra MCP calls + LLM call) against staleness risk (customer state changing during the wait). Sub-15min: state rarely changes meaningfully. Over 15min: meaningful chance of CRM updates (subscription change, new ticket, billing event). Threshold is config-driven (`REVALIDATE_THRESHOLD_MIN` env var) and tunable per tenant.
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

### Send Email (real Gmail SMTP)

- Calls MCP **EMAIL WRITE** server's `send_email` tool — uses Gmail SMTP from `support@yourcompany.com`
- **Threading requires three things together** (Gmail uses all of them; missing any one breaks threading intermittently):
  1. `In-Reply-To: <original_message_id>`
  2. `References: <original_message_id>` (and any prior thread IDs concatenated)
  3. `Subject: Re: <original subject>` (must start with `Re: ` to match)
- **App-layer idempotency** — SMTP itself does not deduplicate. Before each call:
  1. Check if `sent_message_id` is already populated in state.
  2. If yes → skip the send, return cached `sent_message_id`.
  3. If no → call SMTP, save returned `sent_message_id`.
  The `send_idempotency_key` is the lookup; the state field is the lock.
- `send_status`: pending → in_flight → sent
- Transient failures retry up to 3 times; after that → `failed_manual` → Manual Queue
- After successful send, calls `update_message` on the Slack approval message: *"📤 Reply sent to <customer> · 14:05:30"*

### Log Audit

- Append-only entry to audit log
- Records: timestamp, node, decision, model, cost, tokens, trace_url
- Saves both `original_draft` and `final_draft` when human edited
- Includes the KB justification used for escalation (when applicable)

### Manual Queue (terminal)

- Receives tickets from: rejection-count exceeded, SLA expired, send retries exhausted
- Posts a final message to the Slack channel: *"🚦 Routed to manual queue — needs human ownership."*
- Customer is notified via auto-reply email that their ticket is being handled by a human
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

Build **THREE** custom MCP servers — split by capability for least-privilege isolation:

### MCP READ Server (`support_read.py`)
- `get_crm_profile(customer_email)` → mock Salesforce-shape: tier, contract value, renewal, billing, history
- `get_customer_history(customer_email)` → past 90-day interactions
- `get_kb_article(query)` → ACME policy chunks with verbatim sentence quotes

Connected to: **Enrich Context** node and **Revalidate Context** node only. Cannot send anything.

### MCP EMAIL WRITE Server (`support_email_write.py`)
- `send_email(to, subject, body, in_reply_to, idempotency_key)` → real Gmail SMTP send

Connected to: **Send Email** node only. The only path to the customer's inbox.

### MCP SLACK WRITE Server (`support_slack_write.py`)
- `post_approval_request(channel, blocks)` → posts Block Kit message; returns `slack_message_ts`
- `update_message(channel, ts, blocks)` → updates the same message in place (for "Approved by @x", delta panels, "Sent ✓")

Connected to: **Slack Notification** node, **Summarize Changes** (delta update), **Send Email** (post-send confirmation), **Manual Queue** (final status).

### Why split capabilities into three servers

- **Prompt-injection defense.** A jailbreak during Enrich Context (Read server) has no path to either email or Slack. The agent literally cannot exfiltrate or send anything until the explicitly-named write nodes.
- **Audit clarity.** Read calls are frequent and noisy. Email writes are rare and high-stakes (real customer impact). Slack writes are rare but internal-only. Separating them makes the email-send log trivially auditable.
- **Capability decay safety.** A bug or hijack of the Slack write server cannot send a customer email. A bug in the email server cannot post in Slack. Blast radius is bounded by server boundary.
- **2027 hiring signal.** Least-privilege at the tool layer with capability separation is a real production pattern. Most portfolio HITL projects expose all tools through one server.

### Why custom MCP servers (vs hardcoded SDK calls)

- Shows you can BUILD MCP, not just consume
- 17% of agent jobs already require MCP (April 2026 data); trending up for 2027
- Composition into three servers shows real MCP architecture, not toy usage

README line:
> "Tools integrated via Model Context Protocol with capability separation: Read (CRM + KB) cannot write; Email Write cannot post Slack; Slack Write cannot email. Least-privilege isolation against prompt injection. Production pattern, not hardcoded SDK calls."

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
├── .env.example                # GMAIL_USER, GMAIL_APP_PASSWORD, SLACK_BOT_TOKEN,
│                               # SLACK_SIGNING_SECRET, OPENROUTER_API_KEY, LANGSMITH_API_KEY
├── data/
│   ├── bitext_sample.csv       # 50 eval tickets (40 dev / 10 holdout)
│   ├── customers_seed.json     # mock CRM (Salesforce-shape) — customer profiles
│   └── acme_policies.md        # fictional ACME SaaS Co policy corpus (refunds, cancellations, escalations)
├── mcp_server/
│   ├── support_read.py         # READ:  get_crm_profile, get_customer_history, get_kb_article
│   ├── support_email_write.py  # WRITE: send_email (Gmail SMTP, idempotent)
│   └── support_slack_write.py  # WRITE: post_approval_request, update_message (Block Kit)
├── src/
│   ├── state.py                # AgentState TypedDict
│   ├── graph.py                # LangGraph workflow + checkpointer
│   ├── nodes.py                # Node functions (Classify, Enrich, Draft, Finalize, Audit, ...)
│   ├── llm.py                  # OpenRouter client + LangSmith @traceable wrappers
│   ├── policy.py               # Two-gate routing (Policy + Confidence)
│   ├── slack_router.py         # Channel router with priority overrides
│   ├── pii.py                  # PII redact + restore (used by Finalize)
│   ├── email_listener.py       # IMAP poller → ticket creator
│   ├── slack_handler.py        # FastAPI webhook for Slack button actions
│   ├── mcp_client.py           # MCP client(s) routing to read / email-write / slack-write
│   └── server.py               # FastAPI app: Slack webhooks + edit modal + health
├── eval/
│   ├── dataset.py              # LangSmith dataset upload (Bitext)
│   ├── evaluators.py           # 5 evaluators (intent, response, escalation, false-auto-send, slice)
│   └── run_experiments.py      # v1 → v2 → v3 runs
├── ui/
│   └── edit.html               # minimal edit modal (only opened from Slack "Edit" button)
├── tests/
│   ├── test_state.py           # state schema invariants
│   ├── test_policy.py          # routing rules + slack_router priority overrides
│   ├── test_pii.py             # redact + restore round-trip
│   └── test_resume.py          # kill-server restart test
└── demo/
    └── demo_script.md          # 2-min video script (real email → Slack → real email)
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

1. **Real email I/O** — actual Gmail IMAP listener (in) + SMTP send (out), threaded replies via `In-Reply-To`. Not mocked.
2. **Real Slack with multi-channel routing** — `#support-refunds` / `-technical` / `-billing` / `-complaints` / `-enterprise` / `-legal`
3. **Priority-ordered channel routing** (legal > enterprise+risk > angry > intent) — documented and unit-tested in `slack_router.py`
4. **Three custom MCP servers with capability separation** — Read / Email Write / Slack Write, no single server has both read and send powers
5. **Two-gate routing** — separate Policy Risk and Confidence checks, not one fuzzy router
6. **ACME SaaS Co fictional policy corpus** (`data/acme_policies.md`) — shows policy-framework scaffolding skill, not just enforcement; RAG-retrieved at runtime
7. **KB justification quote** in Slack approval — explainable AI, not "trust me"
8. **Customer-tier-aware routing** — Enterprise customers get a senior channel, not the same queue as Free tier
9. **False auto-send rate** as primary safety metric
10. **Approve / Edit / Reject in Slack** — humans never leave their existing tool; Edit opens a Slack modal (or minimal web fallback)
11. **Approve-with-edits** flow with version history (both drafts in audit log)
12. **Stale state revalidation** with delta panel posted as Slack thread update — human re-decides on context change, not silent redraft
13. **Bounded rejection loop** (3-strike rule routes to Manual Queue)
14. **Idempotent send** with explicit `send_idempotency_key` — survives retries and resumes
15. **PII masking middleware** with explicit Finalize-Action restoration
16. **Public LangSmith trace links** with full tag set for failure-slice analysis
17. **Kill-server-resume** demo recorded — Slack message resumes on the right `slack_message_ts`
18. **Implementation Rules** documented (`interrupt()` in dedicated node, no try/except)

Most projects skip nearly all of these. Hit 10+ and you're top 1%.

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

- End-to-end loop works on a real email: `support@yourcompany.com` receives a real test email → agent processes → either auto-sends a real reply OR posts to the right Slack channel → human approves in Slack → real reply lands in customer's inbox threaded under the original
- All graph nodes work end-to-end (Email Listener → PII Redact → Classify → Enrich → Draft → Policy Gate → Confidence Gate → Interrupt → Channel Router → Slack Notification → Reject Check → Elapsed Check → Revalidate → Summarize Changes → Finalize → Send Email → Audit)
- **All three** MCP servers run and are called by the correct nodes (Read / Email Write / Slack Write)
- Slack channel router correctly routes by priority (legal > enterprise+risk > angry > intent) — verified by `tests/test_policy.py`
- Slack message shows the "Why I paused" panel with verbatim ACME KB justification quote
- Approve / Edit / Reject buttons all work via Slack interactions
- Stale-context delta posted as Slack message update when `context_hash` changes during pause
- Implementation Rules followed (`interrupt()` in dedicated node, no try/except wrapping)
- Bounded rejection loop verified (3-strike → Manual Queue posts final status to Slack)
- Idempotent send verified (re-running graph cannot double-send)
- Kill-server-resume demo recorded (server killed mid-pause → Slack message buttons still resume on restart via `slack_message_ts`)
- Approve-with-edits demo recorded (Slack modal edit; both drafts in audit log)
- SLA timeout demo recorded (auto-escalate to Manual Queue + Slack notice)
- 5 evaluators run on 50-ticket Bitext dataset
- v1 → v2 → v3 metrics table populated with REAL numbers
- README complete with all sections including the explicit "real vs mocked" table
- Public LangSmith trace links with full tag set in README
- 2-min demo video uploaded showing real email → Slack approval → real email reply
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
