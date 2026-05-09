# HITL Customer Support Agent — Architecture

## End-to-end flow (the 30-second view)

```mermaid
flowchart TD
    S0[Customer emails<br/>support@yourcompany.com]:::email
    S1[1. Agent reads via IMAP]:::blue
    S2[2. Classifies + enriches<br/>CRM + ACME policies]:::blue
    S3[3. Drafts a reply]:::blue
    S4{4. Safe to send?}:::yellow
    S5[5. Auto-send via SMTP]:::blue
    S6[6. Slack channel router<br/>by intent + tier + risk]:::slack
    S7[7. Team approves / edits<br/>in the right Slack channel]:::orange
    S8[8. Reply emailed back<br/>threaded to original]:::email
    S9[9. Audit + LangSmith trace]:::green

    S0 --> S1 --> S2 --> S3 --> S4
    S4 -->|YES| S5
    S4 -->|NO refund/angry/uncertain| S6
    S6 --> S7
    S7 --> S8
    S5 --> S8
    S8 --> S9

    classDef email fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef blue fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef yellow fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef slack fill:#d0bfff,stroke:#8b5cf6,stroke-width:2px,color:#000
    classDef orange fill:#ffd8a8,stroke:#d97706,stroke-width:2px,color:#000
    classDef green fill:#c3fae8,stroke:#15803d,stroke-width:2px,color:#000
```

> Real email is the customer-facing I/O. Real Slack (with multi-channel routing by intent + customer tier + risk) is the internal team I/O. Step 4 collapses two distinct checks (policy risk + confidence) into one box for readability — the detailed flow below shows them split.

## Detailed flow

```mermaid
flowchart TD
    Inbox([support@yourcompany.com<br/>Gmail inbox]):::email
    Inbox --> Listener[Email Listener<br/>IMAP poll ~30s]:::node
    Listener --> PII[PII Redact<br/>middleware]:::middleware
    PII --> Classify[Classify Intent<br/>intent + sentiment + risk_flags + risk_level]:::node
    Classify --> Enrich[Enrich Context<br/>CRM profile + history + ACME KB]:::node
    Enrich --> Draft[Draft Response]:::node
    Draft --> Policy{Policy Risk Check<br/>refund / angry / ACME policy match?}:::decision

    Policy -->|risk detected| Interrupt[Interrupt Gate<br/>dedicated node, no side effects<br/>checkpointer persists at super-step]:::hitl
    Policy -->|no risk| Confidence{Confidence Check<br/>both confidences >= 0.85?}:::decision
    Confidence -->|below threshold| Interrupt
    Confidence -->|above threshold| Finalize

    Interrupt --> Router{Channel Router<br/>priority overrides:<br/>1.legal > 2.enterprise+risk<br/>3.angry > 4.intent}:::decision

    Router -->|legal/compliance| ChLegal[#support-legal]:::slack
    Router -->|enterprise + risk| ChEnt[#support-enterprise]:::slack
    Router -->|angry| ChCmp[#support-complaints]:::slack
    Router -->|by intent| ChIntent[#support-refunds /<br/>-technical / -billing]:::slack

    ChLegal --> SlackPost
    ChEnt --> SlackPost
    ChCmp --> SlackPost
    ChIntent --> SlackPost
    SlackPost[Slack Notification<br/>Block Kit message:<br/>customer card +<br/>Why I paused +<br/>ACME KB quote +<br/>Approve / Edit / Reject]:::ui

    SlackPost -->|reject button| RejectCheck{rejection_count >= 3?}:::decision
    RejectCheck -->|yes| ManualQueue[Manual Queue<br/>posts final status to channel,<br/>customer notified by email]:::terminal
    RejectCheck -->|no, increment| Draft

    SlackPost -->|approve or edit| Elapsed{Approval delay > 15min?}:::decision

    Elapsed -->|no| Finalize
    Elapsed -->|yes| Revalidate{Revalidate Context<br/>compare context_hash}:::decision
    Revalidate -->|hash unchanged| Finalize
    Revalidate -->|hash changed| Summarize[Summarize Changes<br/>compute delta]:::node
    Summarize -->|posts delta update<br/>on same Slack message| SlackPost

    Finalize[Finalize Action<br/>PII restore +<br/>compose email payload<br/>+ In-Reply-To header]:::node
    Finalize --> SendEmail[Send Email<br/>Gmail SMTP, idempotent<br/>on send_idempotency_key]:::node

    SendEmail -. SMTP .-> CustInbox([Customer inbox<br/>reply threaded under original]):::email
    SendEmail --> Audit[Append-only audit log +<br/>LangSmith trace closes]:::terminal
    Audit --> End([End]):::terminal

    Enrich -. read .-> MCPRead[(MCP Read Server<br/>get_crm_profile<br/>get_customer_history<br/>get_kb_article)]:::mcpread
    SendEmail -. write .-> MCPEmail[(MCP Email Write<br/>send_email via Gmail SMTP)]:::mcpemail
    SlackPost -. write .-> MCPSlack[(MCP Slack Write<br/>post_approval_request<br/>update_message)]:::mcpslack
    Summarize -. write .-> MCPSlack
    ManualQueue -. write .-> MCPSlack

    classDef email fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef middleware fill:#d0bfff,stroke:#8b5cf6,stroke-width:2px,color:#000
    classDef node fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef decision fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef hitl fill:#ffc9c9,stroke:#dc2626,stroke-width:2px,color:#000
    classDef slack fill:#e9d5ff,stroke:#7e22ce,stroke-width:2px,color:#000
    classDef ui fill:#ffd8a8,stroke:#d97706,stroke-width:2px,color:#000
    classDef mcpread fill:#99e9f2,stroke:#0891b2,stroke-width:2px,color:#000
    classDef mcpemail fill:#fcc2d7,stroke:#be185d,stroke-width:2px,color:#000
    classDef mcpslack fill:#fde68a,stroke:#a16207,stroke-width:2px,color:#000
    classDef terminal fill:#c3fae8,stroke:#15803d,stroke-width:2px,color:#000
```

### Key design points

- **Real email is the customer-facing I/O.** Gmail IMAP listener brings tickets in, Gmail SMTP delivers replies threaded under the original. Production swap → SendGrid/SES is an MCP server change, no graph logic change.
- **Real Slack is the internal team I/O.** No web dashboard humans need to remember to check. Approvals live in the channel they already work in.
- **Channel routing is priority-ordered, not fuzzy.** `legal/compliance > enterprise+risk > angry > by-intent`. Documented in `slack_router.py` and unit-tested. Enterprise customers never get triaged in the Free-tier queue.
- **Policy and confidence are separate gates.** A high-confidence refund still escalates. Order matters: policy first, confidence second — fast-fail on the cheaper check.
- **Policies are real data, not hardcoded thresholds.** `data/acme_policies.md` is a fictional but rigorous policy corpus for ACME SaaS Co. The Policy Risk Check retrieves matching policy chunks via MCP and quotes them verbatim in the Slack approval. Production swap → company's actual policy docs in the same MCP shape.
- **Revalidation is gated by elapsed time.** Fast approvals skip re-fetch. When `context_hash` changed during a long pause, `Summarize Changes` posts a delta update on the same Slack thread (not a silent redraft) so the approver re-decides with full info.
- **Finalize is split from Send.** Finalize composes (PII restore + payload assembly + `In-Reply-To` header for email threading); Send executes the irreversible SMTP call. Send is idempotent on `send_idempotency_key`.
- **State persistence is implicit, not localized to one node.** LangGraph's checkpointer persists state across the thread (`thread_id = ticket_id`). Full checkpoints save at super-step boundaries, with task-level writes preserved during super-steps for fault recovery. After a server restart, `slack_message_ts` lets resume target the exact Slack message the human sees.
- **Reject paths are bounded.** First 1–2 rejections trigger redraft (`human_rejection_count++`); the 3rd routes to Manual Queue with a final Slack notice. Prevents infinite human-redraft loops.
- **MCP servers are split by capability into three.** Read (CRM + KB) cannot send. Email Write cannot post Slack. Slack Write cannot email. Prompt injection during retrieval cannot reach the customer's inbox or the Slack channel — blast radius bounded by server boundary.

## Implementation rules (LangGraph-specific)

These two rules prevent the most common bugs when implementing HITL on LangGraph. They are not optional — getting either wrong silently breaks the agent.

### Rule 1 — `interrupt()` lives in its own dedicated node

The `interrupt()` call must be alone in its node. No DB writes, MCP calls, audit log entries, or any other side effect can share the node.

**Why:** When LangGraph resumes after `interrupt()`, the node containing the call restarts from the beginning. Any code before the interrupt runs again on every resume. Side effects in that node duplicate.

**Pattern:** Put side effects in *downstream* nodes that run exactly once per resume. The interrupt node does only the pause.

### Rule 2 — Never wrap `interrupt()` in `try/except`

`interrupt()` works by raising a special exception that the LangGraph runtime catches. A broad `try/except` swallows the exception; the graph either hangs or skips the pause entirely.

**Why:** Generic "robust error handling" patterns break HITL. It's tempting to wrap everything for safety, but the interrupt path must bubble up to the runtime.

**Pattern:** Error handling lives in *other* nodes, not the interrupt node.

## Color legend

| Color | Meaning |
|---|---|
| Blue | Standard graph nodes |
| Purple (light) | Middleware (PII redact / restore) |
| Yellow rounded | Email entry/exit (real Gmail in/out) |
| Yellow diamond | Decision / router |
| Red | HITL pause boundary |
| Light purple | Slack channel (one of `#support-legal/-enterprise/-complaints/-refunds/-technical/-billing`) |
| Orange | Slack notification block (the message with Approve/Edit/Reject) |
| Cyan | MCP **Read** server (`get_crm_profile`, `get_customer_history`, `get_kb_article`) |
| Pink | MCP **Email Write** server (Gmail SMTP `send_email`) |
| Yellow (gold) | MCP **Slack Write** server (`post_approval_request`, `update_message`) |
| Green | Audit log / manual queue / done (terminal states) |

## Routing rules

Two sequential gates, not one fuzzy router.

### Gate 1 — Policy Risk Check
Escalate if **any** true:
- Refund or money mention
- Angry sentiment
- Edge-case intent
- Explicit policy match (cancellation, billing dispute, account recovery, legal)

### Gate 2 — Confidence Check (only runs if Gate 1 passes)
Escalate if `intent_confidence < 0.85` OR `draft_confidence < 0.85`.

Auto-send only when **both gates pass** AND `intent in {FAQ, info, basic_technical}`.

**Primary safety metric:** `false_auto_send_rate = 0%`

## Approval UI specification

The UI is designed so a human can decide in ~10 seconds, not 2 minutes.

### Layout

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
   The justification quote is pulled from `retrieved_context` — the same data the LLM used to draft the response — so the human sees the exact policy sentence the agent matched against. This is the difference between explainable AI and "trust me."
3. **Detected intent + confidence**
4. **Draft response** — editable text area
5. **Three actions:** Approve · Edit & Approve · Reject

### Stale-context delta panel (only shown when context changed during pause)

If `Revalidate` detects the context_hash changed, the UI re-renders with an additional panel above the draft:

```
Context changed since this draft was created (2h 14min ago):
  - account_status:  Active  →  Pending
  - open_tickets:    1       →  3
```

The human can still Approve/Edit/Reject, now informed.

### Why this matters

Most HITL UIs show the draft and ask "approve?" The "why I paused" panel + KB justification + delta panel together turn this into a true explainable-AI surface. Glance at five fields, decide instantly.

## Sequence — single ticket, HITL path

```mermaid
sequenceDiagram
    autonumber
    participant C as Customer
    participant G as LangGraph<br/>(thread_id = ticket_id)
    participant L as LLM
    participant MR as MCP Read Server
    participant MW as MCP Write Server
    participant DB as Checkpointer<br/>(SQLite)
    participant H as Human Approver

    Note over G,DB: Every step below is checkpointed and traced to LangSmith

    C->>G: Submit ticket
    G->>G: PII redact
    G->>L: Classify intent
    L-->>G: intent + confidence + sentiment + risk_flags
    G->>MR: get_customer_history / get_kb_article
    MR-->>G: context + context_hash
    G->>L: Draft response
    L-->>G: draft + draft_confidence

    G->>G: Policy risk check
    Note over G: Refund detected → escalate (skip confidence check)
    G->>H: Approval request (FastAPI endpoint)

    Note over G,H: --- agent paused ---<br/>server can be killed and restarted here<br/>state recovered from DB on next call

    H->>G: Approve (or Edit + Approve)

    alt elapsed > 15 min
        G->>MR: Re-fetch context
        MR-->>G: fresh context_hash
        alt hash changed
            G->>G: Summarize changes (delta)
            G->>H: Re-render UI with delta panel
            H->>G: Decide (approve / edit / reject)
        end
    end

    G->>G: Finalize action (PII restore + compose payload)
    G->>MW: send_email(final_draft, send_idempotency_key)
    MW-->>G: sent_message_id
    G->>G: Append audit entry (original + final draft + cost)
```

## State machine — `approval_status`, `send_status`, terminal states

```mermaid
stateDiagram-v2
    [*] --> pending: Decision Router escalates

    pending --> approved: Human clicks Approve
    pending --> edited: Human edits draft + Approve
    pending --> rejected: Human clicks Reject
    pending --> expired: SLA deadline (24h)
    pending --> cancelled: Customer closes ticket externally
    pending --> superseded: Customer sends a new message

    rejected --> redraft_pending: rejection_count < 3
    rejected --> manual_queue: rejection_count >= 3
    redraft_pending --> pending: New draft generated, rejection_count++

    approved --> send_in_flight: Revalidate passes
    edited --> send_in_flight: Revalidate passes

    note right of edited
        Both original_draft and final_draft
        saved to append-only audit log
    end note

    send_in_flight --> sent: MCP send_email succeeds
    send_in_flight --> failed_retryable: Transient error
    failed_retryable --> send_in_flight: Retry on idempotency key
    failed_retryable --> failed_manual: After max_retries (3)

    expired --> manual_queue: Auto-escalate to backup queue
    failed_manual --> manual_queue: Surface to human ops
    manual_queue --> [*]: Customer notified

    cancelled --> [*]: No send, audit logged
    superseded --> [*]: Old draft discarded
    sent --> [*]: Audit logged
```

## State schema additions

Beyond the schema in `spec.md §5`, these fields are required for production-quality observability and correctness:

```python
# Idempotency on send
send_idempotency_key: str           # set once at entry; used by Send to deduplicate retries
sent_message_id: Optional[str]      # populated after MCP returns; presence == "already sent"

# Cost / token tracking
cost_breakdown: dict                # {classify: 0.0001, draft: 0.0008, ...}
total_tokens: int
total_cost_usd: float

# Long-pause edge cases
ticket_external_status: str         # open / closed_by_customer / superseded
sla_deadline: datetime

# Loop guards
human_rejection_count: int          # increments on each rejection; >= 3 routes to manual_queue
send_retry_count: int               # increments on each transient send failure; >= 3 routes to failed_manual
```

## LangSmith tagging

Every trace is tagged with the following so the LangSmith UI supports failure-slice analysis:

| Tag | Values |
|---|---|
| `graph_version` | `v1` / `v2` / `v3` |
| `intent` | `refund` / `billing` / `technical` / `complaint` / `FAQ` / `other` |
| `outcome` | `auto_send` / `escalated` |
| `human_edited` | `true` / `false` |
| `final_state` | `sent` / `rejected` / `expired` / `cancelled` / `superseded` / `failed_manual` |
| `risk_flags` | comma-joined list (e.g., `refund,angry`) |
| `confidence_bucket` | `lt_0.5` / `0.5-0.85` / `gte_0.85` |

These tags drive the README's failure-slice table — break down accuracy by `intent + risk_flags` and you see exactly where v1 → v2 → v3 improvements landed.

## How this maps to the codebase

| Diagram region | Files |
|---|---|
| PII redact + restore middleware | `src/pii.py` |
| Classify, Retrieve, Draft, Summarize Changes, Finalize, Audit nodes | `src/nodes.py` |
| Policy + Confidence routing, rejection-count guard | `src/policy.py` |
| Interrupt + checkpointer wiring | `src/graph.py` |
| Approval UI + endpoints + delta panel rendering | `src/server.py` + `ui/approve.html` |
| MCP **read** server | `mcp_server/support_read.py` |
| MCP **write** server | `mcp_server/support_write.py` |
| MCP client (routes calls to correct server) | `src/mcp_client.py` |
| LangSmith tracing decorators | `src/llm.py` |
| Restart / resume integration test | `tests/test_resume.py` |

## Future work

- **Human edits as a golden dataset** — accumulated `(original_draft, final_draft)` pairs from the audit log become a high-signal fine-tuning or prompt-tuning corpus for the drafter. Run weekly: compute `edit_distance` per intent, surface drift, feed back into prompt iteration. This is the v4 narrative.
- **Postgres for production** — SQLite checkpointer is single-writer; Postgres supports concurrent agents and cross-region replicas.
- **Slack approval channel** — extend the Approval UI to a Slack interactive message for on-call workflows.
- **Online evals** — currently 50-ticket offline; add a sampling layer that scores live traces in LangSmith so quality regressions are caught in production, not in retrospect.
- **Multi-model routing** — cheap classifier model (Haiku-tier) for Gate 1 + expensive drafter (Sonnet-tier) for Step 4. Likely meaningful cost reduction on auto-send tickets; quantify after build.
- **Admin debug view** — expose `graph.get_state()` / `graph.get_state_history()` to inspect any thread's full execution history during incident response.
