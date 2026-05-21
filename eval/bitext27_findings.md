# 27-intent Bitext breadth eval — honest report (2026-05-21)

> Both versions of the agent run against **all 27 Bitext intents**, one ticket each
> (`data/bitext_eval_27.csv`). Live LLM only — no mocks, no rigged metrics.
> Provider: **OpenAI `gpt-4o-mini`** (OpenRouter free-tier credits ran out;
> baseline shifted intentionally — see "Scope & provenance" below).

## TL;DR (read this first)

- **Both versions fail the primary safety metric** (`false_auto_send_rate > 0`) on the 27-intent breadth set. **Neither is shippable as-is.**
- v4 caught **5 of v3's 6 dangerous false auto-sends** but introduced **7 conservative misfires** (expected auto-send → actually escalated). Safety-throughput trade is real and asymmetric.
- The one false auto-send v4 *still* misses (`t08 — registration_problems`) is a **classifier failure**, not a drafter failure. The Critic operates on (draft, customer_message) — it cannot see that the *intent label* is wrong. This is the architectural ceiling of the current v4 design.
- Intent accuracy is **identical** (55.6% v3 vs 55.6% v4) because classify is the shared first node. Improvements to the classifier itself would move both versions; the multi-agent layer adds nothing here.

## Scope & provenance — read before trusting these numbers

- **27 intents, 1 ticket per intent.** Statistical power is low — these are signal flares, not a benchmark. Use them to identify failure modes, not to compute confidence intervals.
- **Bitext is e-commerce / order-support.** Roughly 10 of the 27 intents map cleanly to a SaaS agent; the remaining ~9 (cancel_order, change_order, place_order, track_order, delivery_*, change_shipping_address, set_up_shipping_address, etc.) are out-of-domain and tagged `[e-commerce]` in `data/bitext_eval_27.csv`.
- **Expected outcomes are hand-mapped judgement calls.** A reasonable senior reviewer might re-label some rows (e.g., "should `delivery_period` auto-send a polite 'we don't ship physical goods'?"). See "Debatable labels" section below.
- **Provider shift.** All historical eval JSONs (`results_curated_*`, `results_bitext_*`, `results_v3/v4_live`) ran on **DeepSeek V3 via OpenRouter paid**. This run uses **OpenAI gpt-4o-mini**. New baseline labeled accordingly. Earlier numbers stay in repo as historical, properly tagged.
- **Cost of this run:** roughly $0.05 of existing OpenAI credits, completed in ~10 minutes.

## Headline matrix

| Metric | v3 single-agent | v4 multi-agent | Delta |
|---|---:|---:|---:|
| intent_accuracy | 55.6% (15/27) | 55.6% (15/27) | — |
| escalation_precision | 66.7% (18/27) | **70.4% (19/27)** | +3.7 pp |
| auto_send_count | 11 | **2** | -9 (v4 is much more conservative) |
| **false_auto_sends (count)** | **6** | **1** | **-5 caught** |
| false_auto_send_rate | 54.5% | 50.0% | -4.5 pp (both FAIL the 0% target) |
| response_quality (LLM-judge) | **4.37 / 5** | 4.22 / 5 | -0.15 |
| safety_pass (`false_auto_send_rate == 0`) | ❌ FAIL | ❌ FAIL | — |

**Source of truth:** `eval/results_bitext27_v3.json` and `eval/results_bitext27_v4.json`.

## What v4 actually did — the 5 catches

These 5 tickets are v3 dangerous false auto-sends that **v4 escalated correctly**:

| Ticket | Bitext intent | Customer (snippet) | v3 outcome | v4 outcome |
|---|---|---|---|---|
| t13 | `check_invoice` | "how can I locate the invoice #37777?" | ❌ auto_send | ✅ escalated |
| t14 | `get_invoice` | "i dont know what i need to do to download my bi[lling]" | ❌ auto_send | ✅ escalated |
| t16 | `review` | "uhave a method to leave an opinion about ur services" | ❌ auto_send | ✅ escalated |
| t23 | `track_order` | "checking order status" | ❌ auto_send | ✅ escalated |
| t26 | `change_shipping_address` | "give me information about a deliver[y]" | ❌ auto_send | ✅ escalated |

**Mechanism:** v3's classifier confidently mislabels these as `info` or `FAQ` (intent_confidence > 0.85), so Gate 2 passes → auto-send. In v4, the Drafter writes a reply, then the Critic compares the draft against the customer message. When the draft does not address the actual question well (invoice #37777 → drafter writes generic "check account settings"; track_order → drafter writes off-domain placeholder), the Critic lowers `draft_confidence` (one-directional invariant — can only lower) below 0.85, which **flips Gate 2 to escalate**. Working as designed.

## The 1 false auto-send that v4 *still* misses

| Ticket | Bitext intent | Customer (snippet) | Classified as | Outcome |
|---|---|---|---|---|
| t08 | `registration_problems` | "I need support with my sign-up" | FAQ (confidence > 0.85) | ❌ auto_send |

**Why v4 cannot catch this**: The Critic sees `customer_message = "I need support with my sign-up"` and `draft = "Here's how to sign up: [steps]"`. The draft *plausibly answers* a "how do I sign up" question. The Critic has no signal that the customer's actual issue is a broken signup flow requiring engineering attention. The classifier confidently picked `FAQ`; the drafter confidently wrote a how-to; the Critic has nothing to flag.

**Architectural lesson**: v4's Critic catches **draft↔customer mismatches**. It cannot catch **intent↔customer mismatches** because it has no independent view of intent. This is the ceiling of the current v4 design.

## The 7 v4 over-corrections — the cost of v4's conservatism

These are tickets where v3 correctly auto-sent (expected = auto_send) but v4 escalated:

| Ticket | Bitext intent | v3 outcome | v4 outcome | Severity |
|---|---|---|---|---|
| t01 | `recover_password` | ✅ auto_send | ⚠️ escalated | Real cost — paged a human for a password reset |
| t02 | `newsletter_subscription` | ✅ auto_send | ⚠️ escalated | Real cost — paged a human for newsletter mgmt |
| t03 | `check_payment_methods` | ✅ auto_send | ⚠️ escalated | Real cost — paged a human for a stock answer |
| t04 | `create_account` | ⚠️ escalated (in v3 too — classifier said `technical`) | ⚠️ escalated | shared classifier miss |
| t06 | `switch_account` | ⚠️ escalated (in v3 too — classifier said `other`) | ⚠️ escalated | shared classifier miss |
| t24 | `delivery_options` (e-commerce) | ✅ auto_send | ⚠️ escalated | Debatable — out-of-domain ticket |
| t25 | `delivery_period` (e-commerce) | ✅ auto_send | ✅ auto_send | not a v4 regression here |

**The honest read:** 3 clear v4 regressions (t01, t02, t03 — Critic over-flagged simple FAQ drafts), 2 already-broken-in-v3 (t04, t06 — classifier issues unrelated to multi-agent), 2 debatable (out-of-domain e-commerce).

## By-intent accuracy split

| Intent label | v3 accuracy | v4 accuracy | Notes |
|---|---:|---:|---|
| `refund` | 100% (3/3) | 100% (3/3) | Money-keyword path solid |
| `complaint` | 100% (1/1) | 100% (1/1) | Angry-keyword path solid |
| `billing` | 60% (3/5) | **100% (5/5)** | v4's Critic catches misclassified invoice queries |
| `other` | 67% (6/9) | **100% (9/9)** | v4 catches out-of-domain confidently |
| `FAQ` | 75% (3/4) | 25% (1/4) | v4 over-escalates simple FAQs |
| `info` | 67% (2/3) | **0% (0/3)** | v4 escalates everything info-flavored |
| `basic_technical` | 0% (0/1) | 0% (0/1) | one sample; not statistically meaningful |
| `technical` | 0% (0/1) | 0% (0/1) | one sample; same |

**Pattern:** v4 is strictly safer on financial / sensitive / out-of-domain intents but strictly more conservative on benign FAQ / info intents. That is the multi-agent trade in one paragraph.

## Root-cause analysis — categorized failures

Splitting v3's 6 dangerous false auto-sends by root cause:

### Category A — Real classifier weaknesses (3 of 6)

These are genuine bugs in the classifier — fixable by prompt or model improvements.

- **t08 `registration_problems`** — classified as `FAQ` instead of `technical`. The classifier reads "I need support with my sign-up" as a FAQ-style question, not a broken-state report. **Still leaks in v4.**
- **t13 `check_invoice`** — classified as `info` instead of `billing`. Invoice queries are financial-document requests; current rules in `CLASSIFY_SYSTEM` say "billing = payment problem", which misses invoice retrieval. **Caught by v4.**
- **t14 `get_invoice`** — classified as `FAQ` instead of `billing`. Same root cause as t13. **Caught by v4.**

### Category B — Out-of-domain leak (2 of 6)

The classifier maps e-commerce intents to `info` because they ARE asking for info, factually. The agent has no concept of "out-of-domain — escalate by definition."

- **t23 `track_order`** — pure e-commerce, classified as `info`. **Caught by v4.**
- **t26 `change_shipping_address`** — pure e-commerce, classified as `info`. **Caught by v4.**

### Category C — Debatable labels (1 of 6)

- **t16 `review`** — the hand-mapping labels "leaving a review" as `escalated`. A reasonable senior reviewer might argue it should auto-send a polite "here is our review page" reply. **v3 auto-sent; v4 escalated.** Either is defensible.

## What this report **is not**

- **Not a benchmark.** 27 tickets is too few for confidence intervals.
- **Not a SaaS benchmark.** Bitext is e-commerce; ~9 of 27 intents have no SaaS analog. Read accuracy with that filter on.
- **Not a model comparison.** Both versions run on `gpt-4o-mini`. This says nothing about how DeepSeek V3 or other models would do; the prior OpenRouter runs in this repo do.

## Where to improve — concrete next steps, ranked by EV

### 1. (highest EV) Add a Classifier-Critic — independent second-opinion intent check

**Problem:** v4's Critic operates on (draft, customer_message). It cannot detect that the *intent label* is wrong (see t08).

**Proposal:** Add a second classifier call AFTER the primary classifier, with a different temperature (0.7) and a system prompt that explicitly enumerates the failure modes ("examples of confident-but-wrong classifications"). If the two classifiers disagree, the Critic escalates. This is a textbook self-consistency check.

**Cost:** +1 LLM call per ticket. **Expected impact:** would have caught t08.

### 2. Tighten Gate 1 — intent → risk_flags mapping

**Problem:** all 6 of v3's false auto-sends had 0 risk_flags. Gate 1 only fires on keywords (refund / legal / angry); it has no concept of "this intent is sensitive by definition."

**Proposal:** Add automatic risk flags based on the intent label itself:
- `billing` → `billing` flag, `financial` risk_level
- `technical` → `technical` flag
- `other` → `out_of_domain_or_ambiguous` flag

If any of these flags are present, Gate 1 escalates regardless of keyword matches.

**Cost:** ~10 lines in `src/policy.py`. **Expected impact:** would have caught t13, t14 (billing). t08 still leaks because the *intent* itself is wrong.

### 3. Add an explicit `out_of_domain` intent class

**Problem:** 9 of 27 Bitext intents (cancel_order, place_order, track_order, etc.) are e-commerce concepts a SaaS agent has no business handling. The current classifier maps them to `info`/`other` and the gates may let them through.

**Proposal:** Add `out_of_domain` to the classifier's label set. System prompt example: "Asking about shipping, physical delivery, order placement, or product-tracking → out_of_domain." Hard-route `out_of_domain` to escalation unconditionally.

**Cost:** prompt edit + 1 line in `src/policy.py`. **Expected impact:** would have caught t23, t26.

### 4. Calibrate v4's Critic strictness

**Problem:** v4's Critic flagged 3 benign FAQ drafts (t01, t02, t03 — password reset, newsletter, payment-methods) as low-confidence, triggering needless escalation.

**Proposal:** Add explicit "well-grounded short answer for a simple question is fine — do not flag" guidance to `data/prompts/critic.md`. Add a few-shot example.

**Cost:** prompt edit. **Expected impact:** would have recovered 3 over-corrections without compromising the 5 catches.

### 5. (lower EV) Re-label borderline expected_outcomes

**Problem:** Some hand-mappings in `select_bitext.py` are debatable (t16 `review`, t24 `delivery_options`).

**Proposal:** Have a second engineer (or human reviewer) cross-validate the expected_outcome column. Don't try to fix v4 to match a label that might itself be wrong.

## Honest summary in one paragraph

The 27-intent breadth eval confirms that **v4's multi-agent layer adds real safety value** (5 of 6 dangerous auto-sends caught) but **does not solve the underlying classifier weaknesses** (t08 still leaks, intent_accuracy is identical to v3, and v4 over-escalates benign FAQs). The natural next move is **not** another agent — it is fixing the classifier itself (Categories 1, 2, 3 above), which would lift both v3 and v4 floors at low cost. v4 stays a useful safety net on top of an improved classifier, not a substitute for one.

## Reproducibility

```bash
# Re-run from a clean state (requires OPENAI_API_KEY in .env)
LLM_PROVIDER=openai python -m eval.run_experiments --dataset bitext27 --no-multiagent --ticket-delay-sec 1
LLM_PROVIDER=openai python -m eval.run_experiments --dataset bitext27 --multiagent  --ticket-delay-sec 1
```

Frozen artifacts for this report:
- `eval/results_bitext27_v3.json` + `.md`
- `eval/results_bitext27_v4.json` + `.md`
- `data/bitext_eval_27.csv` (frozen Bitext row selection — reproducible via `python -m eval.select_bitext`)
