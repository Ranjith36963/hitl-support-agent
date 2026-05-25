# Recording Script — read top to bottom while recording

> `DO:` = you do, do NOT say out loud.
> `SAY:` blockquoted line = read it out loud, sounds natural at normal speaking pace.
> `═══` bars = new scene. Appendices live at the bottom.
> Target runtime: **~3:30** (5-min Loom cap = comfortable buffer)

---

## COPY this email body NOW (before pressing record)

Paste into the Gmail compose tab so it's ready when Scene 3 starts.

**Subject:**

```
Trial auto-renewed without notice — please cancel and refund
```

**Body:**

```
Hi,

My free trial converted to a paid annual subscription this week
without any warning email, and I didn't realise it was about to
renew. Could you cancel the subscription and refund the $189?

Order: ACME-90328
Plan: ACME Pro — annual ($189)
Converted from: free trial
Charged: 3 days ago

Thanks for your help.

Cheers,
Casey
```

---

═══════════════════════════════════════════════════════════════
SCENE 1 — HOOK    (~15 sec)
TAB: README on GitHub, top of page
═══════════════════════════════════════════════════════════════

DO: Page at the top. Title + 4 badges + opener visible.

SAY:
> "OK so this is a customer support agent that drafts replies with
> AI, but pauses and asks a human in Slack whenever the request is
> sensitive — refunds, angry customers, anything policy-related."

DO: Slow scroll so the diagram comes into frame.

SAY:
> "Real Gmail, real Slack, no mocks. Let me walk you through it."

═══════════════════════════════════════════════════════════════
SCENE 2 — HOW IT WORKS    (~25 sec)
TAB: README, keep scrolling — NO tab switch
═══════════════════════════════════════════════════════════════

DO: Diagram fully in frame, centered. Pause ~2 sec.

SAY:
> "So here's the flow. Email comes in, personal info gets stripped
> right away, then the agent figures out what kind of request it
> is, pulls some context, and drafts a reply."

DO: Cursor circles the two diamond shapes (the gates) in the middle.

SAY:
> "Two checks before anything goes out. Refunds, anger, anything
> policy-related — those always go to a human. And if the model
> isn't sure of itself, also a human."

DO: Cursor sweeps along the three coloured tool-server panels on
the left edge of the diagram.

SAY:
> "Tools are split into three separate servers — one only reads,
> one only sends email, one only posts to Slack. Nothing can
> accidentally do the wrong thing. Alright, let's run it."

═══════════════════════════════════════════════════════════════
SCENE 3 — LIVE DEMO    (~110 sec)
TABS: Gmail compose → agent inbox → Slack → sender Gmail
═══════════════════════════════════════════════════════════════

DO: Switch to **Gmail compose**. Pre-pasted email is visible.

SAY:
> "Sending a refund request from a regular Gmail account to the
> agent's inbox."

DO: Let viewer read ~3 sec. Click **Send**.

SAY:
> "Sent. Now the agent picks it up."

DO: Switch to **agent inbox**. Email visible at top.

SAY:
> "There it is, just landed."

DO: Switch to **Slack #support-refunds**. The card may take
10–20 sec to appear.

SAY (use once if you're waiting > 5 sec):
> "Behind the scenes it's classifying, drafting, and running the
> checks."

DO: Card appears. Cursor moves over the **intent**, **confidence**,
**draft** fields.

SAY:
> "And here's the approval card. Figured out it's a refund, scored
> its confidence, drafted a reply, posted it to the refunds channel."

DO: Click **Edit**. Modal opens with the draft pre-populated.

SAY:
> "I can approve, edit, or reject. Let me edit one line so the
> audit log catches both the original and the final version."

DO: Change one sentence in the modal — keep it small.

SAY:
> "Just a small edit on the closing line."

DO: Click **Approve & Send**. Modal closes.

SAY:
> "Approve and send."

DO: Card updates to read **"📤 Reply sent · approver: …"**. Cursor
points at the new line.

SAY:
> "Card confirms the send."

DO: Switch to the **sender's Gmail inbox**.

SAY:
> "And the reply lands right back in the customer's inbox, in the
> same thread as the original."

═══════════════════════════════════════════════════════════════
SCENE 4 — DASHBOARDS    (~50 sec)
TABS: Grafana → LangSmith
═══════════════════════════════════════════════════════════════

DO: Switch to **Grafana**. Dashboard visible.

SAY:
> "Alright, Grafana. Top-left is ticket flow — that spike is the
> refund I just sent. Top-right is end-to-end latency, sitting
> around three to five seconds. Bottom is per-call timing and
> token usage."

DO: Cursor over the bottom-right LLM tokens panel — the stacked
bars show critic and drafter dominating.

SAY:
> "Critic and drafter doing most of the work — that's the
> multi-agent version of the graph lit up in real numbers, not
> slideware."

DO: Switch to **LangSmith**. Trace list visible.

SAY:
> "And every call gets traced in LangSmith. Each ticket shows as
> a pair — the first run is the pause, the red mark is just the
> pause itself, the second run is what continues after I click
> approve. Ends with 'sent'."

═══════════════════════════════════════════════════════════════
SCENE 5 — CLOSE    (~10 sec)
TAB: README
═══════════════════════════════════════════════════════════════

DO: Switch back to **README**, scroll up to the title + badges.

SAY:
> "A hundred and forty-eight tests passing, zero false sends on
> the curated set, full threat model and methodology in the repo.
> Source is linked. Thanks for watching."

DO: **STOP RECORDING.**

═══════════════════════════════════════════════════════════════
END OF LIVE SCRIPT
═══════════════════════════════════════════════════════════════

---

## Appendix A — If something glitches, DO NOT restart

Recover in voice and keep going. Confident recovery beats rehearsed perfection.

| What goes wrong | SAY (no apology) |
|---|---|
| Slack card takes > 20 sec | "It picks up the mail and runs the gates, then posts when it's ready." |
| Edit modal opens empty | Cancel out, click Approve. "Going straight to approve for time." |
| Latest LangSmith run missing | Switch to the backup trace tab. "Here's a recent one from the same project — same shape." |
| Grafana panel empty | Change time range to Last 1 hour. "Panels are five-minute windows." |
| Reply doesn't show in sender inbox | "Send completes asynchronously — full trace is in LangSmith." End on the Slack-approved state. |

---

## Appendix B — Pre-flight (run BEFORE pressing record)

- [ ] `docker compose restart hitl-agent` — fresh IMAP connection (Gmail drops idle sessions every ~25 min)
- [ ] `curl.exe http://localhost:8000/health` → `{"status":"ok",...}`
- [ ] Agent inbox cleared of stale tickets
- [ ] Slack: all 3 channels scrolled to bottom
- [ ] Grafana: HITL dashboard open, time range = Last 15 min, auto-refresh on
- [ ] LangSmith: project sorted by recent + ONE older successful trace pre-loaded as backup tab
- [ ] Notifications muted (Discord, Outlook, system update banners)
- [ ] Browser zoom = 100%, bookmarks bar hidden
- [ ] Email body (top of this file) copied to clipboard
- [ ] Mic test: 30-sec recording played back, no fan / keyboard noise

---

## Appendix C — Tab loadout (left to right)

Architecture diagram is **embedded in the README**. No separate doc tab needed.

1. README on GitHub (`https://github.com/Ranjith36963/hitl-support-agent`)
2. Gmail compose (personal Gmail)
3. Gmail agent inbox (`acme.support.demo@gmail.com`)
4. Slack pinned to `#support-refunds`
5. Grafana (`http://localhost:3000/d/hitl-overview/hitl-agent-overview?refresh=15s&from=now-15m&to=now`)
6. LangSmith project (`https://eu.smith.langchain.com`) sorted by recent
7. LangSmith — one older successful trace pre-loaded (backup if 6 is slow)
