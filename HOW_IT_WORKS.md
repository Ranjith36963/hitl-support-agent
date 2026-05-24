# How It Works — End-to-end product walkthrough

> A production-style customer support system that combines LLM reasoning, deterministic policy enforcement, and human approval workflows with durable execution. Real email I/O, real multi-channel Slack approvals, fictional ACME SaaS Co policy corpus, three capability-isolated MCP servers.

This is the canonical narrative — open this in interviews, paste sections into the README, walk through it on a demo call. `spec.md` is the build spec, `docs/architecture.md` has the diagrams + state schema + LangSmith tag table, this doc tells the story.

The runtime decomposes into seven layers (see `docs/architecture.md` for the table): **Ingestion** (IMAP/SMTP), **Orchestration** (LangGraph + SQLite checkpointer), **Intelligence** (LLM via OpenRouter), **Policy** (two-gate routing + ACME KB retrieval), **HITL** (Slack notification + interrupt + action handler), **Execution** (Finalize + Send + Audit), **Observability** (LangSmith trace + cost tracking). Every step below maps to one of these layers; the layer names are the folder structure too.

---

## Setup (one-time, before any tickets)

You have a Gmail account `support@yourcompany.com` with IMAP enabled and an App Password. You have a Slack workspace with **three channels** for this build: `#support-refunds`, `#support-technical`, `#support-complaints`. Your team is in those channels. The agent is running.

(The spec describes six channels including `#support-billing`, `#support-enterprise`, `#support-legal` with a 4-priority router. That scope was cut to 3 channels for the 6-10h build — see the "Cut from spec" section at the bottom and `src/slack_router.py` docstring. Adding the deferred channels is a config-only change once you decide to.)

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

The shipped router has two priorities (first match wins):
1. **Angry sentiment?** No (sentiment was neutral) — would have gone to `#support-complaints` if yes.
2. **By intent?** **Yes** → `intent=refund` → **`#support-refunds`**.

`slack_channel="#support-refunds"` saved to state.

> *(The spec's full 4-priority chain — `legal/compliance > Enterprise+risk > angry > intent` — is deferred per `src/slack_router.py` docstring. In this build, anything that isn't `refund` or `angry` lands in `#support-technical` as the catch-all.)*

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

## The same flow, but Jamie is angry

Imagine instead Jamie wrote: *"This is the third time. I'm furious. I want my money back NOW or I'm calling my lawyer."*

Steps 1–7 same shape. The classifier returns `sentiment=angry (0.94), risk_flags=["refund","angry","money_mention"], risk_level=financial`.

**Channel Router** runs through priorities:
1. **Angry sentiment? YES.** Channel = **`#support-complaints`**.

(Even though it's a refund — *angry wins*. The angry-sentiment override is the priority order doing real work. In the shipped 3-channel build, `#support-complaints` is where escalations with hot emotional content land so your most experienced responders can de-escalate before substance.)

The Slack post lands in `#support-complaints` with all the same panels — risk_flags, sentiment, customer history, the draft, the policy quote — but in a channel watched by senior support staff. They see the lawyer mention upfront, handle it appropriately, and either approve a careful response or reject it back to the agent for a calmer redraft (the Critic on revision will lean conservative).

Jamie still gets a reply via real email, threaded under her original. She's still treated like she emailed a competent company. The internal routing is invisible to her — but it's the difference between a portfolio project and a real product.

> *(The spec's 4-priority chain would have routed this to `#support-legal` because of the "lawyer" mention. That route is deferred — see `src/slack_router.py` docstring. Adding it back is a config-only change: add the constant, add the priority check above angry, no graph changes.)*

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
| **Eval data** | 10 hand-curated tickets, one per code path (`eval/dataset.py`) — external-benchmark eval (Bitext) deferred to v4.1 | — |
| **Customer database** | — | `data/customers_seed.json` (Salesforce-shape) |
| **CRM profiles** | — | Mock `get_crm_profile` returns shaped data |
| **Policy corpus** | — | `data/acme_policies.md` (fictional ACME SaaS Co) |
| **Ticketing system** | — | Direct email → LangGraph (no Zendesk) |

Production swap at any layer is an MCP config change, not a graph rewrite.

---

## How we know the system actually works (eval)

Five evaluators run against **10 hand-curated tickets** — one per code path, each exercising a distinct graph branch (`eval/dataset.py`) — via LangSmith. A real 10-ticket Bitext eval has since been run as well (`eval/bitext_findings.md`, 10 of Bitext's 27 intents); a larger external sweep is future work.
1. **Intent accuracy** — exact-match against labels
2. **Response quality** — LLM-as-judge with rubric
3. **Escalation precision** — did the two-gate router send to human correctly?
4. **`false_auto_send_rate`** — primary safety metric. Target: 0%. This is the metric that actually matters: did the agent ever auto-send a refund / angry / policy-sensitive reply that should have been escalated?
5. **Failure slice analysis** — accuracy broken down by `intent × risk_flags × confidence_bucket`. Tells you exactly where v1 → v2 → v3 improvements landed.

Every trace tagged in LangSmith with `graph_version`, `intent`, `outcome`, `human_edited`, `final_state`, `risk_flags`, `confidence_bucket` — see the full tag table in `docs/architecture.md`.

---

## The whole thing in one sentence

Jamie sends email → IMAP catches it → agent classifies + drafts → either auto-sends (FAQ-tier) or **posts to the right Slack channel by priority routing → pauses (durable) → human clicks Approve/Edit/Reject → resumes** → SMTP sends the threaded reply → Jamie's phone buzzes. Customer never sees the agent or Slack.

---

## How v4 changes this story

> v3's Jamie-refund walkthrough above is the v3-mode narrative. v4 adds three specialized agents while preserving every step from 7 onward. This section retells what happens between Step 5 (Enrich Context) and Step 7 (Policy Risk Check) when `MULTIAGENT_ENABLED=1`.

### What stays exactly the same

- Steps 1-4: ingestion, PII redact, classify intent — identical
- Steps 7-17: gates, channel router, Slack post, interrupt, resume, finalize, send, audit — identical
- Hard invariants — PII determinism, `false_auto_send_rate=0%`, interrupt_gate isolation, idempotent send, append-only audit, MCP capability isolation

### Step 5 becomes the Researcher Agent

Jamie's refund ticket arrives at what used to be `enrich_context_node`. In v4 it's a compiled sub-graph — the **Researcher Agent** — running a ReAct loop over the MCP Read tools. Refund intent triggers the full sweep: the Researcher calls `get_kb_article`, `get_crm_profile`, and `get_customer_history`, in that order, then exits with the same enrichment shape v3 produced. For Jamie specifically, this is functionally identical to the v3 deterministic node — the difference shows up on a simple FAQ ("how do I reset my password?") where the Researcher calls only KB and skips the CRM lookups entirely. The audit log gains a `researcher_agent` entry with `tools_called=["get_kb_article","get_crm_profile","get_customer_history"]` — same outcome as v3 enrichment, with agentic tool selection visible in the trace.

### Step 6 becomes the Drafter ↔ Critic loop

What used to be a single LLM call is now a tight two-agent sub-graph. The **Drafter Agent** writes draft v1 with a `draft_confidence` score, exactly the v3 shape. Then the **Critic Agent** takes over: it reads the draft alongside the policy quotes the Researcher pulled, and emits a verdict — `accept` or `revise` — with a severity score and feedback string. If the verdict is `accept`, the loop exits and the current draft flows into Gate 1 unchanged. If the verdict is `revise` and we're still on iteration 0 or 1, the Drafter rewrites with the Critic's feedback in the prompt and the Critic audits the new draft. The loop is bounded by `MAX_CRITIC_ITERATIONS = 3`: up to 3 total Drafter calls, up to 2 revision passes, then a hard exit regardless of verdict — **no infinite loops**. The Critic's severity score is wired into draft confidence as `draft_confidence *= (1 - severity * 0.5)` — meaning the Critic can only *lower* confidence, never raise it. And on a malformed JSON response from the Critic, the system fails safe: verdict defaults to `revise` with severity 0.5, escalating to a human rather than silently passing through.

### What this looked like under live eval

We ran 10 tickets through both v3 and v4 with real LLM calls. **`false_auto_send_rate` stayed at 0% under both modes** — the deterministic safety contract held. One ticket (`eval-t07`, a high-confidence info question) flipped from auto-send under v3 to escalated under v4 — the Critic lowered `draft_confidence` below Gate 2's 0.85 threshold. v4 trades a small drop in escalation precision for an additional Critic pass over every draft, without weakening the deterministic safety contract.

### What v4 didn't change

Jamie's experience is identical to the v3 walkthrough. Her phone still buzzes with a real email reply, threaded under her original "Refund please" message, looking like it came from a competent human at a real support team. The internal pre-Slack drafting is now a 3-agent pipeline (Researcher → Drafter ↔ Critic) instead of two deterministic nodes — but the Slack post, the human approval, the threading headers, the idempotent send, and the audit log are bit-for-bit the same.

### Cross-links

- Architecture lock + invariants: `docs/v4_multiagent.md`
- Implementation plan: `docs/superpowers/plans/2026-05-09-v4-multiagent.md`
- Raw eval artifacts: `eval/results_v4_live.json` vs `eval/results_v3_live.json`
- Code: `src/agents/{researcher,drafter,critic}.py`
