# Bitext eval — findings (v3 vs v4, 10 real tickets)

> Honest write-up of the first real external-benchmark eval. Run 2026-05-18,
> live DeepSeek V3 via OpenRouter. Raw artifacts: `results_bitext_v3.{json,md}`,
> `results_bitext_v4.{json,md}`.

## What this is

- 10 **real customer messages** from the Bitext Customer Support dataset
  (Hugging Face) — one per SaaS-mappable Bitext intent.
- Selected deterministically by `eval/select_bitext.py`, frozen into
  `data/bitext_eval_10.csv` so the run is reproducible.
- Run live through **both** v3 (single-agent) and v4 (multi-agent).

### Honest scope (read before trusting the numbers)

- Bitext is an **e-commerce / order-support** dataset. This project is a SaaS
  support agent. Only Bitext's SaaS-adjacent intents are used — this is
  "10 of Bitext's SaaS-adjacent intents," **not** "a SaaS benchmark."
- 10 of Bitext's 27 intents — a **first batch**. The other 17 (track_order,
  shipping, delivery, …) have no SaaS equivalent and are not covered.
- `expected_intent` / `expected_outcome` are **hand-authored mapping
  judgements**. Bitext does not label auto-send vs escalate.
- **n = 10 — no statistical power.** One ticket = 10 percentage points.

## Results — clean run (all 10 tickets completed, no rate-limit errors)

| Metric | v3 | v4 |
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
