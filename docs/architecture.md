# HITL Customer Support Agent — Architecture

## End-to-end flow (the 30-second view)

```mermaid
flowchart TD
    S1[1. Customer sends a ticket]:::blue
    S2[2. Agent reads & classifies]:::blue
    S3[3. Looks up customer info]:::blue
    S4[4. Drafts a reply]:::blue
    S5{5. Safe to send?}:::yellow
    S6[6. Pauses for human<br/>Approve / Edit / Reject]:::orange
    S7[7. Sends the reply]:::blue
    S8[8. Logs everything]:::green

    S1 --> S2 --> S3 --> S4 --> S5
    S5 -->|YES| S7
    S5 -->|NO refund/angry/uncertain| S6
    S6 --> S7
    S7 --> S8

    classDef blue fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef yellow fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef orange fill:#ffd8a8,stroke:#d97706,stroke-width:2px,color:#000
    classDef green fill:#c3fae8,stroke:#15803d,stroke-width:2px,color:#000
```

> Step 5 collapses two distinct checks (policy risk + confidence) into one box for readability. The detailed flow below shows them split.

## Detailed flow

```mermaid
flowchart TD
    Start([Ticket In]):::input --> PII[PII Redact<br/>middleware]:::middleware
    PII --> Classify[Classify Intent<br/>intent + sentiment + risk_flags]:::node
    Classify --> Retrieve[Retrieve Context]:::node
    Retrieve --> Draft[Draft Response]:::node
    Draft --> Policy{Policy Risk Check<br/>refund / angry / policy-sensitive?}:::decision

    Policy -->|risk detected| Interrupt[Interrupt Gate<br/>execution pauses<br/>state already persisted across<br/>all prior steps by checkpointer]:::hitl
    Policy -->|no risk| Confidence{Confidence Check<br/>intent_confidence and<br/>draft_confidence both >= 0.85?}:::decision
    Confidence -->|below threshold| Interrupt
    Confidence -->|above threshold| Finalize

    Interrupt --> UI[Approval UI<br/>shows risk breakdown +<br/>Approve / Edit / Reject]:::ui

    UI -->|reject| ManualQueue[Manual Queue<br/>routed to human agent,<br/>customer notified]:::terminal
    UI -->|approve or edit| Elapsed{Approval delay > 15min?}:::decision

    Elapsed -->|no| Finalize
    Elapsed -->|yes| Revalidate{Revalidate Context<br/>compare context_hash}:::decision
    Revalidate -->|hash unchanged| Finalize
    Revalidate -->|hash changed| Draft

    Finalize[Finalize Action<br/>PII restore +<br/>compose final payload]:::node
    Finalize --> Send[Send Response<br/>idempotent on send_idempotency_key]:::node

    Send --> Audit[Append-only audit log +<br/>trace metadata]:::terminal
    Audit --> End([End]):::terminal

    Retrieve -. calls .-> MCP[(Custom MCP Server<br/>get_customer_history<br/>get_kb_article<br/>send_email)]:::mcp
    Send -. calls .-> MCP

    classDef input fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef middleware fill:#d0bfff,stroke:#8b5cf6,stroke-width:2px,color:#000
    classDef node fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef decision fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef hitl fill:#ffc9c9,stroke:#dc2626,stroke-width:2px,color:#000
    classDef ui fill:#ffd8a8,stroke:#d97706,stroke-width:2px,color:#000
    classDef mcp fill:#b2f2bb,stroke:#15803d,stroke-width:2px,color:#000
    classDef terminal fill:#c3fae8,stroke:#15803d,stroke-width:2px,color:#000
```

### Key design points

- **Policy and confidence are separate gates.** A high-confidence refund still escalates. Order matters: policy first, confidence second — fast-fail on the cheaper check.
- **Revalidation is gated by elapsed time.** Fast approvals skip the re-fetch entirely. Stale-context redraft only happens when the hash actually changed.
- **Finalize is split from Send.** Finalize composes (PII restore + payload assembly); Send executes the irreversible side effect. Send is idempotent on `send_idempotency_key` so retries cannot double-send.
- **State persistence is implicit, not localized to one node.** LangGraph's checkpointer persists every step across the thread (`thread_id = ticket_id`). The Interrupt Gate just pauses execution — it doesn't trigger the checkpoint.
- **Reject routes to a manual queue, not a black hole.** Customer always gets handled, just by a person instead of the agent.

## Color legend

| Color | Meaning |
|---|---|
| Blue | Standard graph nodes |
| Purple | Middleware (PII redact / restore) |
| Yellow diamond | Decision / router |
| Red | HITL pause boundary |
| Orange | Human-facing UI |
| Green (panel) | MCP tool server |
| Green (terminal) | Audit log / manual queue / done |

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
   ```
3. **Detected intent + confidence**
4. **Draft response** — editable text area
5. **Three actions:** Approve · Edit & Approve · Reject

### Why this matters

Most HITL UIs show the draft and ask "approve?" The "why I paused" panel is the difference between a human reading the whole thread to understand context and a human glancing at five fields and deciding instantly.

## Sequence — single ticket, HITL path

```mermaid
sequenceDiagram
    autonumber
    participant C as Customer
    participant G as LangGraph<br/>(thread_id = ticket_id)
    participant L as LLM
    participant M as MCP Server
    participant DB as Checkpointer<br/>(SQLite)
    participant H as Human Approver

    Note over G,DB: Every step below is checkpointed and traced to LangSmith

    C->>G: Submit ticket
    G->>G: PII redact
    G->>L: Classify intent
    L-->>G: intent + confidence + sentiment + risk_flags
    G->>M: get_customer_history / get_kb_article
    M-->>G: context + context_hash
    G->>L: Draft response
    L-->>G: draft + draft_confidence

    G->>G: Policy risk check
    Note over G: Refund detected → escalate (skip confidence check)
    G->>H: Approval request (FastAPI endpoint)

    Note over G,H: --- agent paused ---<br/>server can be killed and restarted here<br/>state recovered from DB on next call

    H->>G: Approve (or Edit + Approve)

    alt elapsed > 15 min
        G->>M: Re-fetch context
        M-->>G: fresh context_hash
        alt hash changed
            G->>L: Re-draft with fresh context
        end
    end

    G->>G: Finalize action (PII restore + compose payload)
    G->>M: send_email(final_draft, send_idempotency_key)
    M-->>G: sent_message_id
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

    approved --> send_in_flight: Revalidate passes
    edited --> send_in_flight: Revalidate passes

    note right of edited
        Both original_draft and final_draft
        saved to append-only audit log
    end note

    send_in_flight --> sent: MCP send_email succeeds
    send_in_flight --> failed_retryable: Transient error
    failed_retryable --> send_in_flight: Retry on idempotency key

    rejected --> manual_queue: Routed to human agent
    expired --> manual_queue: Auto-escalate to backup queue
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
```

## LangSmith tagging

Every trace is tagged with the following so the LangSmith UI supports failure-slice analysis:

| Tag | Values |
|---|---|
| `graph_version` | `v1` / `v2` / `v3` |
| `intent` | `refund` / `billing` / `technical` / `complaint` / `FAQ` / `other` |
| `outcome` | `auto_send` / `escalated` |
| `human_edited` | `true` / `false` |
| `final_state` | `sent` / `rejected` / `expired` / `cancelled` / `superseded` |
| `risk_flags` | comma-joined list (e.g., `refund,angry`) |
| `confidence_bucket` | `lt_0.5` / `0.5-0.85` / `gte_0.85` |

These tags drive the README's failure-slice table — break down accuracy by `intent + risk_flags` and you see exactly where v1 → v2 → v3 improvements landed.

## How this maps to the codebase

| Diagram region | Files |
|---|---|
| PII redact + restore middleware | `src/pii.py` |
| Classify, Retrieve, Draft, Finalize, Audit nodes | `src/nodes.py` |
| Policy + Confidence routing | `src/policy.py` |
| Interrupt + checkpointer wiring | `src/graph.py` |
| Approval UI + endpoints | `src/server.py` + `ui/approve.html` |
| MCP server | `mcp_server/support_tools.py` + `src/mcp_client.py` |
| LangSmith tracing decorators | `src/llm.py` |
| Restart / resume integration test | `tests/test_resume.py` |

## Future work

- **Human edits as a golden dataset** — accumulated `(original_draft, final_draft)` pairs from the audit log become a high-signal fine-tuning or prompt-tuning corpus for the drafter. Run weekly: compute `edit_distance` per intent, surface drift, feed back into prompt iteration. This is the v4 narrative.
- **Postgres for production** — SQLite checkpointer is single-writer; Postgres supports concurrent agents and cross-region replicas.
- **Slack approval channel** — extend the Approval UI to a Slack interactive message for on-call workflows.
- **Online evals** — currently 50-ticket offline; add a sampling layer that scores live traces in LangSmith so quality regressions are caught in production, not in retrospect.
- **Multi-model routing** — cheap classifier model (Haiku-tier) for Gate 1 + expensive drafter (Sonnet-tier) for Step 4. Cuts cost by ~5x on auto-send tickets.
