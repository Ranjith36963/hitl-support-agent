# Demo — v4 Critic Catches Policy Violation

> The agent-to-agent self-correction set-piece for v4. Shows the Critic intercepting a Drafter mistake before it reaches Slack — the demo v3 cannot do.

## Why this demo matters

v3 lets a Drafter mistake reach Slack, where a human catches it. v4 lets the Critic catch it agent-to-agent. **Hard policy gates are still deterministic** — Critic only adjusts `draft_confidence`. The demo shows the Critic removing obvious mistakes *before* the human ever sees them, making the human's job easier without weakening the safety contract.

## Setup

1. `MULTIAGENT_ENABLED=1` in `.env`
2. Real LLM credentials provisioned (`OPENROUTER_API_KEY`, `LANGSMITH_API_KEY`)
3. Pick a refund ticket from `data/customers_seed.json` with amount > $500 (intentionally over ACME Policy 4.2.1's $500 cap)
4. Open LangSmith trace view in one window, terminal in another
5. Pre-stage a Drafter prompt iteration that's likely to over-promise (or wait for it to happen naturally during the demo run)

## Recording (≤ 90 seconds)

| Time | Show | Voice-over |
|---|---|---|
| 0:00–0:10 | Customer email: *"I want a $750 refund."* | "Customer asks for $750. ACME policy 4.2.1 caps refunds at $500." |
| 0:10–0:25 | Trigger the agent. In LangSmith, point at the Drafter's first span — show it offering a $750 refund. | "First draft over-promises. v3 would post this to Slack." |
| 0:25–0:45 | Point at the Critic span — show JSON: `{"verdict":"revise","severity":0.8,"feedback":"Policy 4.2.1 caps refunds at $500"}`. | "Critic catches it. Severity 0.8. Feedback cites the policy." |
| 0:45–1:00 | Point at the SECOND Drafter span — revised draft offering $500 with explanation. | "Drafter rewrites. Now offering $500 with the policy reasoning." |
| 1:00–1:15 | Show the Slack approval message — it never contained the $750 number. | "What landed in Slack is the corrected draft. Human reviews a clean version." |
| 1:15–1:30 | LangSmith run-tree — researcher (1 span) → drafter→critic loop (2 iterations) → gates → channel router → slack. | "Multi-agent self-correction, fully traced. Hard gates unchanged. The Critic doesn't replace the human — it removes obvious mistakes before the human ever sees them." |

## What to verify before recording

- [ ] `pytest` 100% green (resume + Critic invariant tests both PASS)
- [ ] Run the ticket end-to-end COLD once — fix any flake — then record on the second run
- [ ] LangSmith trace shows nested spans correctly: researcher → drafter → critic → drafter (2nd) → critic (2nd) → exit
- [ ] Slack message in `#support-refunds` shows $500, not $750
- [ ] `false_auto_send_rate` still 0% on the eval suite (`python -m eval.run_experiments`)

## Pitfalls to avoid in the recording

- **Do not** claim the Critic "replaces" Gates 1+2 — it adjusts `draft_confidence` only. Hard gates stay hard.
- **Do not** show the trace metadata raw — frame it as "the Critic disagreed with the Drafter and asked for a revision."
- **Do not** keep irrelevant spans expanded — collapse `pii_redact`, `classify_intent`, `channel_router` for clarity.
- **Do not** stage the failure — let the Drafter actually over-promise organically. If it doesn't, accept that and pick a different ticket.

## Calibration — when this demo will actually fire

The Critic only intercepts when the Drafter makes a mistake. In a healthy system, that's 15-40% of tickets (per `critic_disagreement_with_drafter` target). For demo recording, pick a ticket from the 15-40% slice — easiest is a refund-amount-over-cap ticket since the Drafter sometimes hallucinates the cap.

If you can't get the Drafter to fail naturally on any of your 10 eval tickets, that's actually a *good* result — the Critic is correctly silent. In that case, fall back to **Demo 1 (durable execution)** as the v4 demo and document Critic intercept as "rare in our eval set, expected at 15-40% in production."

## How to wire this into the README

After recording:
- Upload the video (Loom / YouTube / direct mp4 in `demo/recordings/`)
- Add link in `README.md` v4 section
- Cross-link `docs/v4_multiagent.md` for architecture, `docs/v3_completion_status.md` for v3 baseline
