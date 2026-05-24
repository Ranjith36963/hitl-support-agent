# HITL Customer Support Agent — Architecture

> A production-style customer support system that combines LLM reasoning, deterministic policy enforcement, and human approval workflows with durable execution. Real email I/O, real multi-channel Slack approvals, fictional ACME SaaS Co policy corpus, three capability-isolated MCP servers.

## System layers

The runtime decomposes into seven layers. Each has a single responsibility; every other doc and the codebase folder structure mirror this split.

| # | Layer | Responsibility | Where it lives |
|---|---|---|---|
| 1 | **Ingestion** | Pull customer email in, push reply email out | `src/email_listener.py` (IMAP) + MCP Email Write (SMTP) |
| 2 | **Orchestration** | Sequence nodes, persist state, recover from crashes | `src/graph.py` (LangGraph + SQLite checkpointer) |
| 3 | **Intelligence** | LLM calls — Classify, Draft, Summarize Changes | `src/llm.py` + `src/nodes.py` (OpenRouter / DeepSeek) |
| 4 | **Policy** | Two-gate routing + channel selection + KB retrieval | `src/policy.py` + `src/slack_router.py` + ACME KB via MCP Read |
| 5 | **HITL** | Slack notification, interrupt, action handler, edit modal | `src/server.py` + `src/slack_handler.py` + MCP Slack Write |
| 6 | **Execution** | Finalize payload, idempotent send, audit log | `src/nodes.py` (Finalize / Send Email / Audit) |
| 7 | **Observability** | Tracing, cost tracking, failure-slice tags | LangSmith decorators in `src/llm.py` |

A swap at any layer is a config change, not a rewrite. Production deployment replaces Gmail/IMAP with SES, mock CRM with Salesforce, ACME corpus with the company's actual policy docs — graph logic doesn't change.

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
    Inbox --> Listener[Email Listener<br/>IMAP IDLE preferred<br/>poll ~30s as fallback]:::node
    Listener --> PII[PII Redact<br/>middleware]:::middleware
    PII --> Classify[Classify Intent<br/>intent + sentiment + risk_flags + risk_level]:::node
    Classify --> Enrich[Enrich Context<br/>CRM profile + history + ACME KB]:::node
    Enrich --> Draft[Draft Response]:::node
    Draft --> Policy{Policy Risk Check<br/>refund / angry / ACME policy match?}:::decision

    Policy -->|risk detected| Router{Channel Router<br/>shipped: 2 priorities<br/>1.angry > 2.intent}:::decision
    Policy -->|no risk| Confidence{Confidence Check<br/>both confidences >= 0.85?}:::decision
    Confidence -->|below threshold| Router
    Confidence -->|above threshold| Finalize

    Router -->|angry| ChCmp[#support-complaints]:::slack
    Router -->|intent=refund| ChRef[#support-refunds]:::slack
    Router -->|other intents| ChTech[#support-technical<br/>catch-all]:::slack

    ChCmp --> SlackPost
    ChRef --> SlackPost
    ChTech --> SlackPost
    SlackPost[Slack Notification<br/>posts Block Kit message<br/>saves slack_message_ts<br/>NO interrupt yet]:::ui

    SlackPost --> Interrupt[Interrupt Gate<br/>dedicated node — only interrupt&#40;&#41;<br/>checkpointer persists at super-step<br/>resumes via webhook]:::hitl

    Interrupt -->|webhook signature verified<br/>Command resume| Action{Action?}:::decision
    Action -->|reject + reason| RejectCheck{rejection_count >= 3?}:::decision
    Action -->|approve or edit| Elapsed{Approval delay > 15min?}:::decision

    RejectCheck -->|yes| ManualQueue[Manual Queue<br/>posts final status to channel,<br/>customer notified by email]:::terminal
    RejectCheck -->|no, count++<br/>carry rejection_reason| Draft

    Elapsed -->|no| Finalize
    Elapsed -->|yes| Revalidate{Revalidate Context<br/>compare context_hash}:::decision
    Revalidate -->|hash unchanged| Finalize
    Revalidate -->|hash changed| Summarize[Summarize Changes<br/>compute delta]:::node
    Summarize -->|update_message:<br/>posts delta on same msg<br/>graph re-interrupts to wait| Interrupt

    Finalize[Finalize Action<br/>PII restore + compose payload<br/>+ In-Reply-To AND References headers<br/>+ Subject: Re: ... for threading]:::node
    Finalize --> SendEmail[Send Email<br/>Gmail SMTP<br/>app-layer idempotency:<br/>skip if sent_message_id present]:::node

    SendEmail -. SMTP .-> CustInbox([Customer inbox<br/>reply threaded under original]):::email
    SendEmail --> Audit[Append-only audit log +<br/>LangSmith trace closes]:::terminal
    Audit --> End([End]):::terminal

    Enrich -. read .-> MCPRead[(MCP Read Server<br/>get_crm_profile<br/>get_customer_history<br/>get_kb_article)]:::mcpread
    SendEmail -. write .-> MCPEmail[(MCP Email Write<br/>send_email via Gmail SMTP)]:::mcpemail
    SlackPost -. write .-> MCPSlack[(MCP Slack Write<br/>post_approval_request<br/>update_message<br/>views.open for Edit modal)]:::mcpslack
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

- **Slack post happens BEFORE interrupt, never after.** Once `interrupt()` fires, execution pauses — nothing else in that node runs. The graph order is: `Channel Router → Slack Notification (posts message, saves ts) → Interrupt Gate (dedicated node, just calls interrupt())`. The webhook resumes the graph at the Interrupt Gate, then routes by action. Reversing this order is a flow-correctness bug that pauses forever with no Slack message ever sent.
- **Real email is the customer-facing I/O.** Gmail IMAP IDLE (sub-second push) preferred; 30s polling as fallback. Gmail SMTP delivers replies threaded under the original — requires `In-Reply-To` AND `References` headers AND a matching `Subject: Re: ...` (Gmail uses all three). Production swap → SendGrid/SES is an MCP server change, no graph logic change.
- **Real Slack is the internal team I/O.** No web dashboard humans need to remember to check. Approvals live in the channel they already work in. Edit opens a Slack modal via `views.open` (not a redirect to a separate web UI) — keeps the human in Slack throughout.
- **Slack webhook signatures are verified, not trusted.** The FastAPI handler computes HMAC-SHA256 of `v0:{X-Slack-Request-Timestamp}:{raw body}` with the signing secret, compares to `X-Slack-Signature`, and rejects if mismatch or timestamp older than 5 minutes (replay defense). Without this, anyone who finds the webhook URL can fake an Approve.
- **Channel routing is priority-ordered, not fuzzy.** Shipped build: `angry > by-intent` over 3 channels (`#support-complaints`, `#support-refunds`, `#support-technical` catch-all). The spec adds `legal/compliance > Enterprise+risk` above those — deferred per `adviserplan.md` scope, config-only to add. `slack_router.py`'s docstring is the source of truth; tests cover the shipped behaviour.
- **Policy and confidence are separate gates.** A high-confidence refund still escalates. Order matters: policy first, confidence second — fast-fail on the cheaper check.
- **Policies are real data, not hardcoded thresholds.** `data/acme_policies.md` is a fictional but rigorous policy corpus for ACME SaaS Co. The Policy Risk Check retrieves matching policy chunks via MCP and quotes them verbatim in the Slack approval. Production swap → company's actual policy docs in the same MCP shape.
- **Revalidation threshold (15 min) is a tunable engineering decision, not a magic number.** Sub-15min: customer state rarely changes meaningfully. Over 15min: meaningful chance of CRM updates (subscription change, new ticket, billing event). Threshold is config-driven and tunable per tenant. When `context_hash` changed during a long pause, `Summarize Changes` posts a delta `update_message` on the same Slack thread (not a silent redraft) so the approver re-decides with full info.
- **Finalize is split from Send.** Finalize composes (PII restore + payload assembly + threading headers); Send executes the irreversible SMTP call.
- **Send idempotency is application-layer, not protocol-layer.** SMTP itself does not deduplicate. Before each call, the Send node checks `sent_message_id` in state — if populated, skip. The `send_idempotency_key` is the lookup, the state field is the lock. This is what "idempotent send" actually means in code.
- **State persistence is implicit, not localized to one node.** LangGraph's checkpointer persists state across the thread (`thread_id = ticket_id`). Full checkpoints save at super-step boundaries, with task-level writes preserved during super-steps for fault recovery. After a server restart, `slack_message_ts` lets resume target the exact Slack message the human sees.
- **Reject paths capture the reason and bound the loop.** Reject button opens a small modal: *"Why? (optional)"*. The reason is stored as `rejection_reason` in state. Below 3 rejections → redraft uses the original ticket *plus the rejection reason* as additional context, posts the new draft as a Slack thread reply on the same message (preserves audit history). At 3 → Manual Queue with a final notice in the channel.
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

## Failure modes — what happens when things go wrong

Every failure has an explicit handling path. None are silent.

| Failure | What the system does |
|---|---|
| Server crashes mid-pause | LangGraph SQLite checkpoint persists at the last super-step. On restart, the Slack message buttons still work — webhook resumes at the Interrupt Gate using `slack_message_ts`. State recovered exactly. |
| SMTP transient failure | `send_retry_count++`. Up to 3 retries, all using the same `send_idempotency_key` (app-layer check on `sent_message_id` in state). After 3 → `failed_manual` → Manual Queue + Slack notice. |
| No human responds in 1h | Agent re-pings the channel: *"⏰ Still pending — backup channel paged."* If still no response by `sla_deadline` (24h) → auto-escalate to Manual Queue. |
| Customer sends follow-up email mid-pause | `ticket_external_status` flips to `superseded`. Old draft discarded. Slack message updates: *"⚠️ Customer replied — superseded, see ticket-XXXX."* Follow-up enters as new ticket. |
| Customer cancels ticket externally | `ticket_external_status` flips to `cancelled`. Slack message updates: *"🚫 Customer cancelled — closing."* No send. |
| Prompt injection in customer email | Read MCP server has no `send_email` and no `post_slack`. Even if a jailbreak fires during retrieval, the agent has no path to either I/O channel until the explicit Send / Slack Write nodes. Capability separation = bounded blast radius. |
| Slack webhook signature mismatch | FastAPI handler returns 401, no resume happens. Logged as security event. |
| Slack timestamp older than 5min | Replay attack defense. Rejected with 401. |
| Human rejects 3 times | Auto-routes to Manual Queue. Slack message: *"🚦 3 rejections — manual queue."* Customer notified by email. |
| LangSmith down | Agent continues. Traces buffer locally, replay when LangSmith returns. Observability outage does not break user flow. |
| LLM rate-limited or timing out | Single retry with backoff. Second failure → escalate to human (treat as low confidence). |
| Hash unchanged but human delays >24h | SLA expires anyway. Manual Queue. Time-based override of staleness check. |

## Color legend

| Color | Meaning |
|---|---|
| Blue | Standard graph nodes |
| Purple (light) | Middleware (PII redact / restore) |
| Yellow rounded | Email entry/exit (real Gmail in/out) |
| Yellow diamond | Decision / router |
| Red | HITL pause boundary |
| Light purple | Slack channel — shipped build uses 3: `#support-complaints` / `#support-refunds` / `#support-technical`. Spec's `#support-legal`, `#support-enterprise`, `#support-billing` are config-only additions deferred per `adviserplan.md`. |
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
    G->>G: Channel Router (priority overrides) → #support-refunds
    G->>MW: post_approval_request (Block Kit, channel selected)
    Note over MW: Slack Write Server (capability-isolated)
    MW-->>G: slack_message_ts saved to state
    G->>G: Interrupt Gate (dedicated node) — calls interrupt(), pauses

    Note over G,H: --- agent paused ---<br/>server can be killed and restarted here<br/>state recovered from DB on next call

    H->>G: Click Approve in Slack (button)
    Note over G: FastAPI handler verifies HMAC-SHA256 signature<br/>(timestamp <5min check)
    G->>G: Command(resume=approve) → graph wakes at Interrupt Gate

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

Beyond the base schema in `spec.md §5`, these fields are required for production-quality observability and correctness:

```python
# Routing inputs (drive the Channel Router and gates)
customer_tier: str                   # Free / SMB / Enterprise (from CRM)
risk_level: str                      # none / financial / legal / compliance
policy_matches: list                 # ["ACME 4.2.1", "ACME 7.1"] — KB sections that triggered escalation

# Real I/O channels (email + Slack)
email_thread_id: str                 # original customer email RFC-822 Message-ID; used for In-Reply-To + References threading
slack_channel: str                   # which channel the approval went to (e.g. "#support-complaints")
slack_message_ts: str                # Slack message timestamp; used to update / resume on the right msg

# Idempotency on send (app-layer, not SMTP-layer)
send_idempotency_key: str            # set once at entry; lookup key for the dedupe check
sent_message_id: Optional[str]       # populated after SMTP returns; presence == "already sent" — Send node skips if set

# Cost / token tracking
cost_breakdown: dict                 # {classify: 0.0001, draft: 0.0008, ...}
total_tokens: int
total_cost_usd: float

# Long-pause edge cases
ticket_external_status: str          # open / closed_by_customer / superseded
sla_deadline: datetime               # SLA expiry; once passed without a human decision → manual_queue

# Loop guards
human_rejection_count: int           # increments on each rejection; >= 3 routes to manual_queue
rejection_reason: Optional[str]      # free-text from Slack reject modal; carried into next Draft as context
send_retry_count: int                # increments on each transient send failure; >= 3 routes to failed_manual
```

### Tunable thresholds (env vars)

| Env var | Default | What it controls |
|---|---|---|
| `REVALIDATE_THRESHOLD_MIN` | 15 | Minutes after which an approval triggers context revalidation before send |
| `MAX_HUMAN_REJECTIONS` | 3 | Reject count that flips redraft loop to Manual Queue |
| `MAX_SEND_RETRIES` | 3 | Transient-failure retry cap before `failed_manual` |
| `SLA_DEADLINE_HOURS` | 24 | Hours of human silence before SLA expires to Manual Queue |
| `IMAP_POLL_INTERVAL_SEC` | 30 | Polling fallback when IMAP IDLE is unavailable |
| `HOST` | `127.0.0.1` | FastAPI bind address. Loopback by default (bandit B104 safer default for dev). **Production / container deploys must set `HOST=0.0.0.0` explicitly** to accept external traffic. See `docs/threat_model.md` row A5. |
| `PORT` | `8000` | FastAPI bind port. |

## LangSmith tagging

**Status: scoped, not yet emitted.** `src/llm.py:_ls_metadata` returns the intended tag dict, but no caller passes it to `@traceable` yet (every traceable currently sets only `name=` and `run_type=`). The 2026-05-24 audit caught the gap. Wiring is a one-commit follow-up: add `tags=_ls_metadata(state)` to each `@traceable` callsite and re-publish the README's failure-slice claim with real LangSmith screenshots backing it.

The intended tag set when wired:

| Tag | Values |
|---|---|
| `graph_version` | `v3` / `v4` (`MULTIAGENT_ENABLED` flag, stamped at `graph_runner.startup()`) |
| `intent` | `refund` / `billing` / `technical` / `complaint` / `FAQ` / `info` / `basic_technical` / `other` |
| `outcome` | `auto_send` / `escalated` |
| `human_edited` | `true` / `false` |
| `final_state` | `sent` / `rejected` / `expired` / `cancelled` / `superseded` / `failed_manual` |
| `risk_flags` | comma-joined list (e.g., `refund,angry`) |
| `confidence_bucket` | `lt_0.5` / `0.5-0.85` / `gte_0.85` |

Once these tags are emitted, the failure-slice table in the README — break down accuracy by `intent + risk_flags` — becomes reproducible from LangSmith. Until then, the README's slice numbers are sourced from the eval result JSONs only.

## Eval data

The eval suite runs against **10 hand-curated tickets** — one per code path (`eval/dataset.py`), each exercising a distinct graph branch. A real 10-ticket Bitext eval has also been run (`eval/bitext_findings.md`, 10 of Bitext's 27 intents); a larger external sweep is future work. Five evaluators run via LangSmith:
1. Intent accuracy (exact-match against labels)
2. Response quality (LLM-as-judge with rubric)
3. Escalation precision (did the two-gate router send to human correctly?)
4. **False auto-send rate** — primary safety metric, target 0%
5. Failure slice analysis — accuracy broken down by `intent × risk_flags × confidence_bucket`

## How this maps to the codebase

| Diagram region | Files |
|---|---|
| Email Listener (IMAP IDLE / poll) | `src/email_listener.py` |
| PII redact + restore middleware | `src/pii.py` |
| Classify, Enrich Context, Draft, Summarize Changes, Finalize, Audit nodes | `src/nodes.py` |
| Policy + Confidence routing, rejection-count guard | `src/policy.py` |
| Channel Router with priority overrides (shipped: angry > intent over 3 channels; legal/Enterprise tiers deferred per `adviserplan.md`) | `src/slack_router.py` |
| Interrupt + checkpointer wiring | `src/graph.py` |
| FastAPI server + Slack webhook signature verification + edit modal | `src/server.py` + `src/slack_handler.py` (Slack `views.open` modal — `ui/edit.html` web fallback was scoped but not built) |
| MCP **Read** server (CRM + KB) | `mcp_server/support_read.py` |
| MCP **Email Write** server (Gmail SMTP, idempotent) | `mcp_server/support_email_write.py` |
| MCP **Slack Write** server (post / update / views.open) | `mcp_server/support_slack_write.py` |
| MCP client (routes calls to correct capability-isolated server) | `src/mcp_client.py` |
| LLM client + LangSmith tracing decorators | `src/llm.py` |
| ACME SaaS Co fictional policy corpus | `data/acme_policies.md` |
| Mock customer DB (Salesforce-shape) | `data/customers_seed.json` |
| Hand-curated eval dataset (10 tickets, one per code path) | `eval/dataset.py` |
| Restart / resume integration test | `tests/test_resume.py` |

## Future work

- **Human edits as a golden dataset** — accumulated `(original_draft, final_draft)` pairs from the audit log become a high-signal fine-tuning or prompt-tuning corpus for the drafter. Run weekly: compute `edit_distance` per intent, surface drift, feed back into prompt iteration. This is the v4 narrative.
- **Postgres for production** — SQLite checkpointer is single-writer; Postgres supports concurrent agents and cross-region replicas.
- **Webhook-based inbound mail** — replace IMAP IDLE listener with SES / SendGrid Inbound Parse / Postmark for sub-second latency at scale without keepalive overhead.
- **Online evals** — currently 50-ticket offline; add a sampling layer that scores live traces in LangSmith so quality regressions are caught in production, not in retrospect.
- **Multi-model routing** — cheap classifier model (Haiku-tier) for Gate 1 + expensive drafter (Sonnet-tier) for Step 4. Likely meaningful cost reduction on auto-send tickets; quantify after build.
- **Admin debug view** — expose `graph.get_state()` / `graph.get_state_history()` to inspect any thread's full execution history during incident response.
- **Real CRM / ticketing integration** — swap mock `support_read.py` for Salesforce/HubSpot API; swap email-direct entry for Zendesk Conversations API. MCP server replacement, no graph logic changes.

## v4 Multi-Agent Layer

v4 promotes two reasoning-heavy v3 nodes into specialized agents — **Researcher** (replaces `enrich_context_node`) and **Drafter ⇄ Critic** (replaces `draft_response_node`) — coordinated by the unchanged outer graph. The 15-node parent topology, every safety gate, the channel router, the interrupt/resume protocol, and append-only audit semantics are all identical to v3. v4 is opt-in via `MULTIAGENT_ENABLED=1`; with the flag off, v3 runs untouched. See `docs/v4_multiagent.md` for the full spec, hard invariants, and evaluator targets.

### v4 sub-graph wiring

```mermaid
flowchart TD
    subgraph Researcher["Researcher sub-graph (replaces enrich_context_node)"]
        RS([START]):::terminal
        RNode[research<br/>deterministic intent → MCP Read tools<br/>FAQ → KB only<br/>refund/billing/complaint → KB+CRM+history<br/>technical → KB+CRM]:::node
        RE([END]):::terminal
        RS --> RNode --> RE
    end

    subgraph Drafter["Drafter ⇄ Critic sub-graph (replaces draft_response_node)"]
        DS([START]):::terminal
        DNode[drafter<br/>writes / rewrites draft<br/>picks up critic feedback if iter > 0]:::node
        CNode[critic<br/>LLM judge<br/>verdict + severity + feedback<br/>draft_confidence *= 1 - severity*0.5]:::node
        Route{revise AND<br/>iter &lt; MAX_CRITIC_ITERATIONS - 1?}:::decision
        Loop[drafter_loop<br/>iteration++<br/>MAX = 2 hard cap]:::node
        DE([END]):::terminal
        DS --> DNode --> CNode --> Route
        Route -->|yes| Loop --> DNode
        Route -->|no| DE
    end

    classDef node fill:#a5d8ff,stroke:#2563eb,stroke-width:2px,color:#000
    classDef decision fill:#fff3bf,stroke:#f59e0b,stroke-width:2px,color:#000
    classDef terminal fill:#c3fae8,stroke:#15803d,stroke-width:2px,color:#000
```

Both sub-graphs are compiled LangGraph `StateGraph`s over the same `AgentState` and slot into the parent at the existing v3 node positions. Output shapes match v3 exactly so downstream nodes (`route_after_draft`, `channel_router`, `interrupt_gate`, etc.) are unchanged. The Critic is a NEW agent with no v3 equivalent — it lives only inside the Drafter sub-graph and never emits routing decisions; it can only adjust `draft_confidence` downward (multiplicative by `1 - severity * 0.5`).

### v4 codebase map

Extends the v3 "How this maps to the codebase" table above. v4 agents share infrastructure via `src/agents/base.py`; they do not import from `src/nodes.py` (clean module dependency — v4 → shared, never v4 → v3).

| Diagram region / contract | Files |
|---|---|
| Shared agent infra — `get_llm()` (AsyncOpenAI to OpenRouter, same SDK as v3), `get_model_id(override_env)`, `load_prompt()`, `build_handoff_metadata()`, `get_client()` (delegates to `graph_runner.get_mcp_router`) | `src/agents/base.py` |
| Researcher sub-graph — single-node compiled sub-graph; deterministic intent → MCP Read tool selection (full ReAct deferred to v4.1) | `src/agents/researcher.py` |
| Drafter sub-graph — `drafter ⇄ critic` loop with `MAX_CRITIC_ITERATIONS = 3` hard cap; output shape preserves v3 contract (`original_draft`, `final_draft`, `draft_confidence`) | `src/agents/drafter.py` |
| Critic agent — LLM judge with escalate-on-uncertainty fallback (malformed JSON → `verdict=revise`, `severity=0.5`); writes only allow-listed fields; severity clamped to `[0, 1]` | `src/agents/critic.py` |
| System prompts (markdown, version-controlled) | `data/prompts/researcher_system.md` · `data/prompts/drafter_system.md` · `data/prompts/critic_system.md` |
| Critic safety contract — 6 tests prove allow-list, severity clamp, monotonic confidence decrease, malformed-JSON fallback | `tests/test_critic_invariants.py` |
| Drafter↔Critic loop hard cap — 3 tests prove the loop terminates at 3 iterations even when the Critic returns `revise` indefinitely | `tests/test_drafter_critic_loop.py` |
| End-to-end smoke — 3 tests covering FAQ auto-send path, revise-then-accept loop, and Critic-triggered escalate path | `tests/test_v4_integration_smoke.py` |
| 5 v4 evaluators — tool selection precision, critic disagreement rate, alignment with human edits, loop iteration distribution, per-agent cost breakdown | `eval/multiagent_evaluators.py` |
| A/B Researcher-model swap experiment runner (DeepSeek vs Haiku-tier) | `eval/ab_model_swap.py` |

### Hard invariants preserved

The full list lives in `docs/v4_multiagent.md` — "What stays UNCHANGED" table. Cross-link, don't restate. Highlights:

- `pii_redact_node` still runs first; no agent ever sees raw PII.
- `route_after_draft` Gates 1 + 2 are still hard thresholds in `src/policy.py`. Critic adjusts `draft_confidence` as input to Gate 2; it does NOT replace either gate.
- `interrupt_gate` is still alone in its own node, no `try/except`, Slack post strictly before.
- `send_email_node` app-layer idempotency on `sent_message_id` unchanged.
- `audit_log` remains append-only; both `original_draft` and `final_draft` saved on edit; every agent appends a handoff-metadata entry per the schema in `docs/v4_multiagent.md`.
- MCP capability isolation unchanged — agents reach MCP only through `get_client()` → Read / Email Write / Slack Write boundary still holds.

### What's deferred to v4.1

- **Cost tracking** — per-agent token/cost rollup into `cost_breakdown`. The hooks exist (handoff metadata captures `agent_name`); aggregation across agent runs is not wired.
- **LangSmith UI metadata propagation** — handoff metadata is appended to each agent's `audit_log` entry today (inspectable via `jq '.per_ticket[].audit_log[].metadata'`), but is not auto-propagated into LangSmith run-tree metadata for one-click `agent_name` filtering. Per-agent slicing in the UI still requires inspecting individual run input/output JSON.
- **Multi-agent evaluator aggregation** — the 5 evaluators in `eval/multiagent_evaluators.py` run per-ticket; cross-run dashboards and the v3-vs-v4 comparison table are still manual.
- **Removal of the v3 path** — `MULTIAGENT_ENABLED=0` keeps the v3 single-agent graph alive as a reversible fallback. Dropping the flag (and the v3 enrich/draft node bodies) waits until v4 has logged enough production traffic to justify it.
