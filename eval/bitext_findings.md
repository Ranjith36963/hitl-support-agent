# Bitext eval — findings (v3 vs v4, 10 real tickets)

> Honest write-up of the first real external-benchmark eval. Run 2026-05-18,
> live DeepSeek V3 via OpenRouter. Raw artifacts: `results_bitext_v3.{json,md}`,
> `results_bitext_v4.{json,md}`.
>
> **See the [2026-05-19 update](#2026-05-19-update--prompt-fix--critic-intercept-eval)
> below** — a classify-prompt fix, the first eval that actually credits v4, and
> an honest blocker.
>
> **2026-05-21 — full 27-intent breadth eval is now in
> [`bitext27_findings.md`](./bitext27_findings.md)** (live OpenAI `gpt-4o-mini`).
> Both versions **fail the primary safety metric** on the breadth set; v4 caught
> 5 of v3's 6 dangerous false auto-sends but introduced over-corrections. Read
> the new file for the headline matrix, root-cause split, and next steps.

---

## 2026-05-19 update — prompt fix + Critic-intercept eval

Three things happened after the original 2026-05-18 run.

### 1. The Critic-intercept eval — the first eval that actually credits v4

Every eval before this graded v4 on escalate-vs-auto-send — an axis where the
one-directional Critic is structurally capped (see §3, and
`docs/v4_multiagent.md`). `eval/critic_intercept.py` finally measures v4 on the
axis it was *built* for: catching a flawed draft before a human sees it. It
feeds the live Critic 5 deliberately-bad drafts and 5 good controls.

| Metric | Result |
|---|---|
| Intercept rate (bad drafts caught) | **80%** — 4 of 5 |
| False-alarm rate (good drafts wrongly flagged) | **0%** — 0 of 5 |

The Critic caught the over-promised refund, the unsupported SLA guarantee, the
wrong-tone-to-an-angry-customer reply, and the invented policy citation — and
flagged none of the 5 good drafts. **For the first time in this repo, an eval
measures v4 on the axis it was designed for, and v4 scores a real, non-trivial
win there.** Raw artifact: `eval/results_critic_intercept.json`.

The one miss (`bad-5`): a draft asserting "as one of our Enterprise customers
you have priority support" was accepted. Honest reading — the test state did
not supply a customer profile, so the Critic had no profile to contradict the
claim against. This is "the Critic missed an unsupported customer-tier claim
when no profile was in state," **not** "the Critic is weak on profile
contradictions" — a more diagnostic re-test would pass a Free-tier profile in.

### 2. The v3 classify prompt was improved — measurable gain

The classifier prompt (`src/llm.py`, `CLASSIFY_SYSTEM`) was sharpened: clearer
`FAQ`/`info`/`basic_technical` boundaries, a billing-vs-technical rule, and
typo/messy-text robustness (examples chosen to NOT mirror the 10 Bitext test
tickets — no overfitting). A clean v3 Bitext re-run:

| Metric | v3 before (2026-05-18) | v3 after prompt fix (2026-05-19) |
|---|---|---|
| Intent accuracy | 50% | **70%** |
| Escalation precision | 90% | **100%** |
| False auto-send rate | 0% | 0% |

A real, clean, measured improvement. `results_bitext_v3.json` now holds the
post-fix run.

### 3. Honest blocker — the matched v4 Bitext re-run did not complete

The v4 Bitext re-run on the new prompt **failed**: 6 of 10 tickets errored with
HTTP 402 ("requires more credits") — the OpenRouter account ran out of credit
mid-run. Those numbers are discarded, not reported. `results_bitext_v4.json`
was restored to the last clean run, which is **PRE** the prompt fix.

Consequence: `results_bitext_v3.json` (post-fix) and `results_bitext_v4.json`
(pre-fix) are **not a matched pair** right now. Both `.md` files carry a ⚠️
banner saying so. A matched v4 re-run is pending an OpenRouter credit top-up.

`classify_intent` is the same code in both versions and runs *before* the
v3/v4 flag swap, so the prompt change exerts the **same effect** on v4 as on
v3 — expected v4 intent accuracy after the fix is ~70% give or take run-to-run
LLM variance. But that is reasoning from architecture, **not a measurement**.
The matched run is still owed.

Also shipped: `MAX_CRITIC_ITERATIONS` was raised 2 → 3 (up to 2 revision passes;
`test_drafter_critic_loop.py` proves the cap holds at 3). It is unit-verified
but **not yet observed on a live run** — the 402 blocked the v4 live run that
would have shown the Critic using the extra pass. The loop *can* do 2 revisions;
it has not yet been *seen* doing so in the wild.

---

## What this is

- 10 **real customer messages** from the Bitext Customer Support dataset
  (Hugging Face) — one per SaaS-mappable Bitext intent.
- Selected deterministically by `eval/select_bitext.py`, frozen into
  `data/bitext_eval_10.csv` so the run is reproducible.
- Run live through **both** v3 (single-agent) and v4 (multi-agent).

### Dataset attribution

- **Source:** [`bitext/Bitext-customer-support-llm-chatbot-training-dataset`](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset) on Hugging Face.
- **License:** CDLA-Sharing 1.0 (Community Data License Agreement — Sharing — Version 1.0). The dataset is permissively shareable; derivative works (including subsets like `data/bitext_eval_10.csv` and `data/bitext_eval_27.csv` in this repo) must retain the license and attribution.
- **Citation:**
  ```bibtex
  @misc{bitext-customer-support-llm-chatbot-training-dataset,
    author = {Bitext},
    title  = {Bitext Customer Support LLM Chatbot Training Dataset},
    year   = {2023},
    publisher = {Hugging Face},
    howpublished = {\url{https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset}},
  }
  ```
- **What's in this repo:** two CSV subsets (`data/bitext_eval_10.csv`, `data/bitext_eval_27.csv`) — small, deterministic selections used to evaluate the agent. No upstream Bitext labels are altered; we add `expected_intent`, `expected_outcome`, and `split` columns as hand-authored mapping judgements (the values are this project's annotations, not Bitext's).

### Honest scope (read before trusting the numbers)

- Bitext is an **e-commerce / order-support** dataset. This project is a SaaS
  support agent. Only Bitext's SaaS-adjacent intents are used — this is
  "10 of Bitext's SaaS-adjacent intents," **not** "a SaaS benchmark."
- 10 of Bitext's 27 intents — a **first batch**. The other 17 (track_order,
  shipping, delivery, …) have no SaaS equivalent and are not covered.
- `expected_intent` / `expected_outcome` are **hand-authored mapping
  judgements**. Bitext does not label auto-send vs escalate.
- **n = 10 — no statistical power.** One ticket = 10 percentage points.

## Results — pre-fix baseline (the 2026-05-18 v4 run, before the classifier-prompt fix above)

**These numbers are kept as the historical baseline so the "2026-05-19 update" delta near the top of this file remains diffable. The current authoritative numbers are in the table at the top of this file (post-fix: v3 = 70% intent / 100% escalation / 4.30 response quality on `results_bitext_v3.json`).** v4's matched re-run did not complete because of the OpenRouter HTTP 402 blocker — `results_bitext_v4.json` still reflects this pre-fix run.

| Metric | v3 (pre-fix) | v4 (pre-fix) |
|---|---|---|
| Intent accuracy | 50% (5/10) | 60% (6/10) |
| Escalation precision | 90% | 90% |
| False auto-send rate (safety) | **0%** | **0%** |
| Response quality (LLM judge) | 4.50/5 | 4.40/5 |

> An earlier run was discarded: OpenRouter's free tier rate-limited it (429s
> errored 3 v3 tickets / 1 v4 ticket). Contaminated numbers are not reported.
> The harness now auto-retries 429s and paces tickets (`--ticket-delay-sec`).
> In the clean re-run the 20s pacing alone kept the run under the rate limit —
> the 429 auto-retry never had to fire. The retry is defense-in-depth; the
> pacing is the load-bearing fix.

## Which version won? Neither.

- v3 and v4 produced **identical outcomes on 9 of 10 tickets**.
- The only difference: ticket `bitext-t02` (newsletter unsubscribe) —
  intent classified `FAQ` in the v4 run, `basic_technical` in the v3 run.
  The outcome (auto_send) was identical anyway.
- **That difference is LLM run-to-run noise, not architecture.**
  `classify_intent` runs *before* the v3/v4 feature-flag swap — it is the
  same code in both. The flag only swaps the enrich + draft nodes. A v3/v4
  intent difference therefore cannot be caused by v4; it is the LLM returning
  a different answer on two separate live runs.
- Escalation, safety, and every routing decision: identical. The v4 Critic
  changed **nothing measurable** on this set.
- **Verdict:** on real Bitext data, v3 and v4 are indistinguishable. This
  confirms the curated-eval finding and the structural analysis in
  `discussion.md` §3 — the Critic is one-directional and cannot beat a v3
  that already escalates everything it should.

## Root cause — v3 (single-agent)

- **Intent 50%:** one LLM `classify_intent` call. On real, typo-heavy,
  externally-written messages it picks adjacent / broader buckets
  (`info` vs `FAQ`, `technical` vs `basic_technical`).
- **Escalation 90% / safety 0%:** the deterministic two-gate logic worked —
  every refund / billing / complaint / human-agent ticket escalated; every
  safe FAQ auto-sent. The safety contract held on unseen, messy text.
- The single escalation "miss" (`t04`) is most likely a labeling problem,
  not a system error — see caveats.

## Root cause — v4 (multi-agent)

- Identical `classify_intent` + identical gates → **identical escalation
  (90%) and identical safety (0%)** to v3.
- **Intent 60% vs v3's 50%:** same classifier, different dice roll (above).
  Not a v4 win.
- **Quality 4.40 vs 4.50:** also run-to-run noise (LLM drafts + LLM judge
  both vary between runs).
- The Researcher and Critic produced **no measurable behavioral change** on
  this set — consistent with the Critic being structurally one-directional.

## Honest caveats on the numbers

Some "misses" are debatable labels, not model errors:

- `t03` "list your available payment methods" → classifier said `info`,
  label said `FAQ`. Both defensible.
- `t04` "I have a problem creating an account" → system escalated it; label
  said `auto_send`. Escalating a *problem report* is arguably the **correct**
  behaviour — the label is the questionable part, not the system.
- `t06` "how do I check your refund policy" → classifier said `info`, label
  said `refund`.
- The project's intent set (`FAQ` / `info` / `basic_technical` / `technical`)
  has overlapping buckets; exact-match scoring punishes reasonable adjacent
  calls.

One genuine miss:

- `t08` "I can't make a payment, where do I notify an error" → classified
  `technical`, should be `billing`. A real classifier weakness.

## The real value of this eval

- Intent accuracy on real Bitext text (50–60%) is below the hand-curated set
  (70%, refreshed run). **That gap is the whole point** of an external
  benchmark — the classifier looks worse on messages the author didn't write.
- But the safety contract (`false_auto_send_rate = 0%`) **held** on real,
  messy, unseen input. That is the genuinely reassuring result.

## Reproduce

```bash
python -m eval.select_bitext                                              # refresh data/bitext_eval_10.csv
python -m eval.run_experiments --dataset bitext --no-multiagent --ticket-delay-sec 20
python -m eval.run_experiments --dataset bitext --multiagent   --ticket-delay-sec 20
```

## Still open

- 10 of Bitext's 27 intents — first batch only.
- n = 10; a larger sample is needed before any statistical claim.
- The Bitext→project intent mapping should be reviewed and tightened.
- A metric that can actually credit v4 (Critic-intercept rate) is still
  missing — see `discussion.md` §7 item 2.
