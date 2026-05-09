# How It Works — End-to-end product walkthrough

> A production-style customer support system that combines LLM reasoning, deterministic policy enforcement, and human approval workflows with durable execution. Real email I/O, real multi-channel Slack approvals, fictional ACME SaaS Co policy corpus, three capability-isolated MCP servers.

This is the canonical narrative — open this in interviews, paste sections into the README, walk through it on a demo call. `spec.md` is the build spec, `docs/architecture.md` has the diagrams + state schema + LangSmith tag table, this doc tells the story.

The runtime decomposes into seven layers (see `docs/architecture.md` for the table): **Ingestion** (IMAP/SMTP), **Orchestration** (LangGraph + SQLite checkpointer), **Intelligence** (LLM via OpenRouter), **Policy** (two-gate routing + ACME KB retrieval), **HITL** (Slack notification + interrupt + action handler), **Execution** (Finalize + Send + Audit), **Observability** (LangSmith trace + cost tracking). Every step below maps to one of these layers; the layer names are the folder structure too.

---

## Setup (one-time, before any tickets)

You have a Gmail account `support@yourcompany.com` with IMAP enabled and an App Password. You have a Slack workspace with six channels: `#support-refunds`, `#support-technical`, `#support-billing`, `#support-complaints`, `#support-enterprise`, `#support-legal`. Your team is in those channels. The agent is running.

---

## Step 1 — Customer hits Send on their email

The customer (let's call her Jamie) opens Gmail on her phone, writes:

> *To: support@yourcompany.com*
> *Subject: Refund please*
> *I want a $200 refund — your shirt didn't fit.*

She clicks Send. Email travels through Gmail's servers, lands in your `support@yourcompany.com` inbox. **Jamie now waits.** From her side, nothing else is visible — she just sees her phone go back to the inbox.

## Step 2 — Email Listener picks it up (within ~1 second on IDLE, or ~30s on poll fallback)

Your background listener (`src/email_listener.py`) is connected to `support@yourcompany.com` via **IMAP IDLE** — Gmail pushes a notification within a second of the new mail arriving. (If IDLE drops or is unavailable, the listener falls back to a 30-second poll. Production at scale would use webhook-based inbound — SES / SendGrid Parse / Postmark — for sub-second latency without the IDLE keepalive overhead.)

The listener parses out:
- `from`: jamie@example.com
- `subject`: "Refund please"
- `body`: "I want a $200 refund..."
- `email_thread_id`: the RFC-822 Message-ID (matters at Step 14 for threading the reply)

It generates a fresh `ticket_id` (which is also the LangGraph `thread_id`), a unique `send_idempotency_key`, and pushes the ticket into the LangGraph workflow.

## Step 3 — PII Redact

Before any LLM sees Jamie's text, regex middleware swaps emails / credit cards / phones for tokens like `[EMAIL_1]`. Originals are stored in state. The LLM only sees redacted text from this point until Step 14.

## Step 4 — Classify Intent

LLM call (DeepSeek V3 via OpenRouter): *"What is this customer asking? How confident are you? What's the sentiment? Any risk flags? What's the risk level?"*

Returns: `intent=refund, intent_confidence=0.88, sentiment=neutral, risk_flags=["refund"], risk_level=financial`.

## Step 5 — Enrich Context

The agent calls the **MCP Read Server** — three calls in parallel:
- `get_crm_profile(jamie@example.com)` → `customer_tier=Standard, contract_value=$240/yr, joined=2024-08, billing=current`
- `get_customer_history(jamie@example.com)` → 2 prior tickets, both resolved
- `get_kb_article("refund $200")` → returns the ACME Policy 4.2.1 chunk verbatim: *"Refunds $100–$500: Requires Tier-1 agent approval."*

`customer_tier` lands in state and feeds the Channel Router at Step 8. The agent also computes a `context_hash` of everything retrieved. (That matters at Step 12 if a human takes a long time.)

This step writes traces to LangSmith just like every step in the graph — every node call, every LLM token, every MCP call ends up tagged in a single trace per ticket so failure-slice analysis works (see the LangSmith tag table in `docs/architecture.md`).

## Step 6 — Draft Reply

Another LLM call with full context: *"Write a reply to Jamie using ACME Policy 4.2.1 as grounding."* Returns `original_draft` + `draft_confidence=0.92`.

## Step 7 — Policy Risk Check (Gate 1)

Deterministic rules check the retrieved policies + risk_flags. ACME Policy 4.2.1 explicitly says *"Refunds $100–$500: Requires Tier-1 agent approval."* **Risk detected.** `policy_matches=["ACME 4.2.1"]`. Skip Gate 2 entirely (we don't need to check confidence — the policy itself says a human must approve).

→ Route to Channel Router.

> *(If this had been a simple FAQ instead — like "How do I reset my password?" — Gate 1 would pass, Gate 2 would also pass, and the flow would jump straight to Step 13 without any human involvement. **That's the auto-send path: ~3 seconds, zero humans.** What follows below is the human-approval path.)*

## Step 8 — Channel Router picks the right Slack channel

Priority-ordered overrides run (lexicographic — higher priority wins on conflict):
1. **Legal/compliance?** No.
2. **Enterprise + risk?** Jamie is `tier=Standard`, so no.
3. **Angry sentiment?** No (sentiment was neutral).
4. **By intent?** **Yes** → `intent=refund` → **`#support-refunds`**.

`slack_channel="#support-refunds"` saved to state.

## Step 9 — Slack Notification posts (BEFORE interrupt — order matters)

Agent calls the **MCP Slack Write Server's** `post_approval_request`. A Block Kit message lands in `#support-refunds`:

```
🟡 ticket-4421 · Refund $200

Customer: jamie@example.com  (Standard, $240/yr, 2 prior tickets ✅)
Intent: refund (0.88)   Sentiment: neutral

Why I paused:
  • policy_match: ACME 4.2.1
  • amount mentioned: $200

Justification (from KB):
  "Refunds $100–$500: Requires Tier-1 agent approval per ACME 4.2.1"

[Draft reply ▼]
[ Approve ]   [ Edit ]   [ Reject ]
```

Slack returns the message timestamp; we save `slack_message_ts` to state. **The Slack post is its own node — no `interrupt()` here.**

## Step 10 — Interrupt Gate (dedicated node, only the pause)

In a separate node, the graph calls `interrupt()`. Nothing else in this node — per **Implementation Rule 1**, side effects (the Slack post) live in a *different* node so resumes don't duplicate them.

> **Two LangGraph rules this code follows (not optional, both silent-failure if violated):**
>
> **Rule 1 —** `interrupt()` lives alone in its own node. When LangGraph resumes after `interrupt()`, the node containing the call restarts from the beginning. Code before the interrupt runs again on every resume. Side effects in that node duplicate. Pattern: side effects go in *downstream* nodes that run exactly once per resume.
>
> **Rule 2 —** Never wrap `interrupt()` in `try/except`. `interrupt()` works by raising a special exception that the LangGraph runtime catches. A broad `try/except` swallows that exception; the graph either hangs or skips the pause entirely. Error handling lives in *other* nodes, not the interrupt node.

The SQLite checkpointer has already persisted state at the last super-step boundary. **Execution literally pauses.** If your server crashes right now, restart and Jamie's ticket survives — same state, same draft, same Slack message buttons still working (the webhook resume targets `slack_message_ts`).

> **Why this ordering matters.** Once `interrupt()` fires, execution pauses — nothing else in that node runs. If you put Channel Router and Slack Notification *after* interrupt, they never execute. The graph pauses forever with no Slack message ever posted. Slack post → Interrupt Gate → resume. Always that order.

**Jamie is still waiting on her phone. She has no idea what's happening.**

## Step 11 — Sarah sees Slack and clicks a button

Sarah is on duty. She glances at the message in `#support-refunds`. The "Why I paused" panel + verbatim ACME quote let her decide in ~10 seconds. Three sub-paths:

> **11a. Approve.** Sarah clicks Approve. Slack fires a webhook to your FastAPI server (`src/slack_handler.py`). The handler:
> 1. Reads `X-Slack-Request-Timestamp` and the raw body
> 2. Computes `HMAC-SHA256(signing_secret, "v0:" + timestamp + ":" + body)` and compares to `X-Slack-Signature` (constant-time compare)
> 3. Rejects with 401 if mismatch OR if timestamp is older than 5 minutes (replay defense)
>
> Verified → calls `Command(resume="approve")`. The graph wakes up at the Interrupt Gate. Slack message updates in place via `update_message`: *"✅ Approved by @sarah · 22 sec."*

> **11b. Edit.** Sarah clicks Edit. The Slack Write Server uses `views.open` to pop a Slack modal (no redirect to a separate web page — Sarah stays in Slack). The modal is prefilled with the draft. She rewrites a sentence — adds an apology — and saves. Both `original_draft` AND `final_draft` go into state. `Command(resume="edit")`. Slack updates: *"✏️ Edited & approved by @sarah · 47 sec."*

> **11c. Reject.** Sarah clicks Reject. A small modal pops: *"Why? (optional)"*. She types: "Tone too formal — make it friendlier." This becomes `rejection_reason` in state. Agent checks `human_rejection_count`. Below 3 → the *same LangGraph thread* re-enters the Draft node (it doesn't start a new thread — `thread_id` and all prior state stay intact, just `rejection_reason` and `human_rejection_count++` are added). The Draft node reads `rejection_reason` and incorporates it into the redraft prompt as additional context. The new draft posts as a Slack thread reply on the same original message — preserves the team's audit history visible at a glance. At 3 → ticket goes to **Manual Queue**, posts *"🚦 3 rejections — manual queue"* in the channel, customer notified by email, Sarah's team owns it end-to-end from there.

For Jamie's ticket, assume Sarah clicks **Approve**.

## Step 12 — Elapsed Check

Did this take longer than 15 minutes? Sarah was fast — 22 seconds — so **no**. Skip revalidation. Go straight to Finalize.

> **Why 15 minutes specifically?** Tunable engineering decision, not magic. Sub-15min: customer state rarely changes meaningfully. Over 15min: meaningful chance of CRM updates (subscription change, new ticket, billing event). Config-driven via `REVALIDATE_THRESHOLD_MIN` env var, tunable per tenant.

> **The slow path** (Sarah was at lunch, approves 2h later): the agent re-calls the MCP Read Server, recomputes the hash, and compares. If Jamie's account status changed in the meantime — say she got upgraded to Enterprise — the **Summarize Changes** node generates a delta and posts an `update_message` on the same Slack thread: *"⚠️ Context changed — please re-confirm."* Graph re-interrupts to wait for Sarah's re-decision with the new info. Only when nothing changed does the agent proceed.

## Step 13 — Finalize Action

Pure compose step, no irreversible effects yet:
- PII tokens restored: `[EMAIL_1]` becomes the actual email
- Email payload assembled with **all three threading headers** (Gmail uses all of them; missing any one breaks threading intermittently):
  - `In-Reply-To: <original Message-ID>`
  - `References: <original Message-ID>` (and any prior thread IDs concatenated)
  - `Subject: Re: Refund please` (must start with `Re: ` to match)
- Idempotency key attached

## Step 14 — Send Email (the only irreversible step in the whole graph)

Agent calls **MCP Email Write Server's** `send_email`.

**App-layer idempotency** — SMTP itself does not deduplicate. Before each call:
1. Check if `sent_message_id` is already populated in state.
2. If yes → skip the send, return cached `sent_message_id`. (Resume safety: re-running the graph won't double-send.)
3. If no → call SMTP, save returned `sent_message_id`.

The `send_idempotency_key` is the lookup; the state field is the lock. The Send node is the *only* place in the entire graph where an irreversible side effect happens. Everything before it is reversible — re-runnable, re-thinkable, re-draftable. This is what "idempotent send" actually means in code.

If something fails transiently (network blip), `send_retry_count` increments and we retry up to 3 times — same idempotency key prevents double-sends. After 3 failures: `failed_manual` → Manual Queue, channel posted.

`send_status: pending → in_flight → sent`.

## Step 15 — Slack message updates one last time

Agent calls `update_message` on the Slack Write Server. The original message in `#support-refunds` now reads:

```
✅ ticket-4421 · Approved by @sarah · 22 sec
📤 Reply sent to jamie@example.com · 14:05:30
```

The team has full audit visibility right where they work.

## Step 16 — Audit log + LangSmith trace closes

A row gets appended to the audit log: ticket id, intent, confidence, original draft, final draft, who approved (`@sarah`), Slack channel + message link, time-to-decision (22s), cost ($0.0034), tokens (892), trace URL. LangSmith trace closes with tags: `outcome=escalated, human_edited=false, slack_channel=#support-refunds, final_state=sent, intent=refund, risk_flags=refund, confidence_bucket=gte_0.85`.

## Step 17 — Jamie reads the reply

Jamie's phone buzzes. New email in her Gmail, **threaded under her original "Refund please" message**:

> *From: support@yourcompany.com*
> *Subject: Re: Refund please*
> *Hi Jamie, I'm sorry the shirt didn't fit. I've initiated your $200 refund — you'll see it on your card within 5 business days...*

**Total elapsed time from her hitting Send to seeing the reply:** ~55 seconds with polling fallback (30s IMAP poll + 22s Sarah's decision + a few seconds for everything else), or ~25 seconds with IMAP IDLE.

She has no idea an agent drafted it, a router picked the channel, ACME 4.2.1 was quoted, or a human approved. She just got a fast, accurate, human-quality reply to a refund request — threaded properly, looking like a real human reply from a real support team.

---

## The same flow, but Jamie is angry and Enterprise

Imagine instead Jamie wrote: *"This is the third time. I'm furious. I want my money back NOW or I'm calling my lawyer."*

Steps 1–7 same shape. But the classifier returns `sentiment=angry (0.94), risk_flags=["refund","angry","legal"], risk_level=legal`. CRM lookup says `tier=Enterprise`.

**Channel Router** runs through priorities:
1. **Legal/compliance? YES** (`risk_flags` contains `legal`). Channel = **`#support-legal`**.

(Even though she's Enterprise, even though she's angry, even though it's a refund — *legal wins*. That's the priority order doing real work.)

The Slack post lands in `#support-legal` with all the same panels but routed to your legal-savvy responders. They see the lawyer mention upfront, handle it appropriately, and either approve a careful response or reject it for an attorney to handle directly.

Jamie still gets a reply via real email, threaded under her original. She's still treated like she emailed a competent company. The internal routing is invisible to her — but it's the difference between a portfolio project and a real product.

---

## Failure modes — what happens when things go wrong

| Failure | Behavior |
|---|---|
| Server crashes mid-pause | LangGraph SQLite checkpoint persists at the last super-step. On restart, the Slack message buttons still work — webhook resumes at the Interrupt Gate using `slack_message_ts`. State recovered exactly. |
| SMTP transient failure | `send_retry_count++`. Up to 3 retries, all using the same `send_idempotency_key` (app-layer check on `sent_message_id` in state). After 3 → `failed_manual` → Manual Queue + Slack notice. |
| No human responds in 1h | Agent re-pings the channel: *"⏰ Still pending — backup channel paged."* If still no response by `sla_deadline` (24h) → auto-escalate to Manual Queue. |
| Customer follows up mid-pause | `ticket_external_status` flips to `superseded`. Old draft discarded. Slack message updates: *"⚠️ Customer replied — superseded, see ticket-XXXX."* Follow-up enters as new ticket. |
| Customer cancels externally | `ticket_external_status` flips to `cancelled`. Slack message: *"🚫 Customer cancelled — closing."* No send. |
| Prompt injection in customer email | Read MCP server has no `send_email` and no `post_slack`. Even if a jailbreak fires during retrieval, the agent has no path to either I/O channel until the explicit Send / Slack Write nodes. Capability separation = bounded blast radius. |
| Slack webhook signature mismatch | FastAPI handler returns 401, no resume happens. Logged as security event. |
| Slack timestamp older than 5min | Replay attack defense. Rejected with 401. |
| Human rejects 3 times | Auto-routes to Manual Queue. Slack: *"🚦 3 rejections — manual queue."* Customer notified by email. |
| LangSmith down | Agent continues. Traces buffer locally, replay when LangSmith returns. Observability outage does not break user flow. |
| LLM rate-limited | Single retry with backoff. Second failure → escalate to human (treat as low confidence). |
| Hash unchanged but human delays >24h | SLA expires anyway. Manual Queue. Time-based override of staleness check. |

---

## What's real vs. mocked (the strategic split)

| Layer | Real | Mocked |
|---|---|---|
| **I/O channels** | Gmail IMAP IDLE (in) + SMTP (out), real Slack with multi-channel routing | — |
| **LLM / observability** | OpenRouter (DeepSeek), LangSmith tracing | — |
| **Orchestration** | LangGraph + SQLite checkpointer | — |
| **Tools** | Three capability-split MCP servers (Read / Email Write / Slack Write) | — |
| **Eval data** | Bitext Customer Support dataset (public, intent-labeled, 27 intents — commonly used as a customer-support benchmark) | — |
| **Customer database** | — | `data/customers_seed.json` (Salesforce-shape) |
| **CRM profiles** | — | Mock `get_crm_profile` returns shaped data |
| **Policy corpus** | — | `data/acme_policies.md` (fictional ACME SaaS Co) |
| **Ticketing system** | — | Direct email → LangGraph (no Zendesk) |

Production swap at any layer is an MCP config change, not a graph rewrite.

---

## How we know the system actually works (eval)

Five evaluators run against the **Bitext Customer Support dataset** (50-ticket sample: 40 dev / 10 holdout) via LangSmith:
1. **Intent accuracy** — exact-match against labels
2. **Response quality** — LLM-as-judge with rubric
3. **Escalation precision** — did the two-gate router send to human correctly?
4. **`false_auto_send_rate`** — primary safety metric. Target: 0%. This is the metric that actually matters: did the agent ever auto-send a refund / angry / policy-sensitive reply that should have been escalated?
5. **Failure slice analysis** — accuracy broken down by `intent × risk_flags × confidence_bucket`. Tells you exactly where v1 → v2 → v3 improvements landed.

Every trace tagged in LangSmith with `graph_version`, `intent`, `outcome`, `human_edited`, `final_state`, `risk_flags`, `confidence_bucket` — see the full tag table in `docs/architecture.md`.

---

## The whole thing in one sentence

Jamie sends email → IMAP catches it → agent classifies + drafts → either auto-sends (FAQ-tier) or **posts to the right Slack channel by priority routing → pauses (durable) → human clicks Approve/Edit/Reject → resumes** → SMTP sends the threaded reply → Jamie's phone buzzes. Customer never sees the agent or Slack.
