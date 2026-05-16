# Discussion & Findings — v3/v4 review, eval audit, Bitext claim

> Working document. Records a review session that audited the v3↔v4 comparison,
> the eval harness, and the project's "Bitext" eval claim. Every finding below
> is backed by a file path + line reference so any agent can re-verify.
> Date of review: 2026-05-16. Reviewed against commit `d1a1725`.

---

## 0. Why this document exists

A review was asked to answer three questions:

1. Should v3 be deleted (kept "v4 only"), or should both versions stay?
2. Did v4 (multi-agent) actually perform better than v3?
3. Were the evals done honestly, or were they "done in desperation and mocked"?

The short answers:

1. **Do not delete v3 yet.** It is the only in-repo proof both versions were
   measured. (See §1, §6.)
2. **No — v4 did not beat v3.** They tie; v3 is marginally ahead. And v4 is
   *structurally incapable* of beating v3 on the metrics measured. (See §2, §3.)
3. **The numbers are not fabricated, but the eval is weak and mislabeled.**
   It is branded as a "Bitext" benchmark eval; no Bitext data exists in the
   repo. (See §4, §5.)

---

## 1. The v3 / v4 situation

- Two implementations live in the codebase behind a feature flag.
  - **v3** — single-agent path (`enrich_context_node` + `draft_response_node`
    in `src/nodes.py`).
  - **v4** — multi-agent path: a Researcher sub-graph + a Drafter↔Critic loop
    sub-graph (`src/agents/researcher.py`, `src/agents/drafter.py`,
    `src/agents/critic.py`).
- Toggle: env var `MULTIAGENT_ENABLED`. `1` = v4, `0` (default) = v3.
  Branch logic: `src/graph.py:187` (`_MULTIAGENT = ... == "1"`) and the
  `if _MULTIAGENT:` swap at `src/graph.py:194-200`.
- The flag only swaps **two nodes** — `enrich_context` and `draft_response`.
  Everything else (PII redact, classify intent, gates, channel router, Slack,
  interrupt, finalize, send) is shared.
- A deprecation comment at `src/graph.py:171-185` schedules v3 for deletion in
  "v4.1", calling v3 a "comparison artifact … NOT a production-rollback safety
  net." Three removal triggers are listed (Demo 4 recorded / first interview
  cycle done / v4.1 plan starts).

**Consequence of the flag only swapping 2 nodes** — any metric computed from a
node *before* the swap is identical between v3 and v4 by construction. This
matters for §4.

---

## 2. The eval results — what was measured

Two result files exist for the live (real-LLM) eval:

- `eval/results_v3_live.md` / `.json` — generated `2026-05-09T19:35:03`.
- `eval/results_v4_live.md` / `.json` — generated `2026-05-09T19:33:04`.

Head-to-head, 10-ticket real-LLM eval (OpenRouter / DeepSeek V3):

| Metric | v3 | v4 | Winner |
|---|---|---|---|
| False auto-send rate (primary safety metric) | 0% | 0% | tie |
| Intent accuracy | 70% | 70% | tie (see §4 — cannot differ) |
| Escalation precision | 100% | 90% | **v3** |
| Response quality (LLM judge) | 4.30/5 | 4.30/5 | tie |

- v4's single loss: ticket `eval-t07` ("Technical question — expected
  auto-send"). v3 auto-sent it correctly; **v4 over-escalated it.** Recorded in
  `eval/results_v4_live.md:50-54` ("Escalation mismatches" table).
- Sample size is 10. One ticket *is* the entire v3-vs-v4 gap. That is within
  noise — no statistical conclusion can be drawn from it.

**Plain-language conclusion:** the "newer, fancier" multi-agent v4 did **not**
win. Best case it ties v3; on the one metric that moved, v3 won.

---

## 3. Root-cause analysis — why v4 cannot beat v3

This is structural, not a tuning accident.

### 3.1 The Critic only ever lowers confidence (one-directional)

- `src/agents/critic.py:81`:
  ```python
  new_confidence = state.get("draft_confidence", 1.0) * (1 - severity * 0.5)
  ```
- `severity` is clamped to `[0, 1]` (`critic.py:80`). So the multiplier
  `(1 - severity*0.5)` is always in `[0.5, 1.0]` — it can only **decrease**
  `draft_confidence`, never raise it.
- Gate 2 escalates when `draft_confidence < 0.85` (two-gate logic, see
  `CLAUDE.md` "Two-gate routing"; thresholds in `src/policy.py`).
- Therefore: for the *same* draft, v4's `draft_confidence` ≤ v3's. v4 escalates
  **≥** v3, always. v4 can *add* escalations but never *remove* them.
- v3 already scores 100% escalation precision on the 10-ticket set. 100% is the
  ceiling. A component that can only push toward "escalate" cannot beat a
  version already at the ceiling — it can only tie or lose.
- **This is the direct cause of the `eval-t07` over-escalation.** A minor Critic
  nitpick (`severity > 0`) on an otherwise-fine technical reply multiplied
  `draft_confidence` below 0.85, tripping Gate 2.

### 3.2 The Drafter↔Critic "loop" barely loops

- `src/agents/drafter.py:28`: `MAX_CRITIC_ITERATIONS = 2`.
- `src/agents/drafter.py:94-100` — `_route_after_critic`:
  ```python
  if verdict == "revise" and iteration < MAX_CRITIC_ITERATIONS - 1:
      return "drafter_loop"
  return END
  ```
- `iteration < MAX_CRITIC_ITERATIONS - 1` → `iteration < 1` → loops **only at
  iteration 0**. After one revision the sub-graph exits to the gates even if
  the Critic still says "revise".
- So v4's headline feature — iterative refinement — is in practice **one
  optional retry**, not a meaningful loop.

### 3.3 draft_confidence is self-graded, then penalized

- `src/agents/drafter.py:73`: `"draft_confidence": float(result.get("draft_confidence", 0.5))`
  — the Drafter LLM grades **its own** draft.
- The Critic then multiplies that self-grade down (§3.1).
- v4's Gate 2 input is therefore "LLM self-assessment × Critic penalty" — a
  noisier signal than v3's, not a more reliable one.

### 3.4 The eval never measures what v4 is actually for

- v4's intended value: the Critic catches a *bad draft* before a human sees it
  → higher first-draft quality, fewer human reject→redraft loops.
- The eval measures escalate-vs-auto-send, intent accuracy, and a generic
  quality score. It does **not** measure Critic-intercept rate, first-draft
  acceptance, or reject-loop count.
- So even if v4's Critic does add value, the current eval is blind to it.
  v4 was graded only on axes where it is structurally capped (§3.1).

---

## 4. Eval methodology problems

### 4.1 What is genuinely sound (not fabricated)

- The harness **refuses to emit fake metrics**. `eval/run_experiments.py:354-362`
  — if `OPENROUTER_API_KEY` is unset in real mode it prints
  "No fake metrics will be produced." and `sys.exit(1)`.
- In real-LLM mode, `classify_intent` and `draft_response` are **not** mocked —
  real LLM calls run. Mocks for those are applied *only* in `--no-llm` mode
  (`run_experiments.py:239-250`).
- The LLM judge genuinely ran. `eval/results_v3_live.json` and
  `results_v4_live.json` contain **different per-ticket `reason` strings** in
  `response_quality_details` — real, distinct LLM output, not copy-paste.
- v4 genuinely ran with the flag on: `eval-t07` outcome, escalation precision,
  and per-ticket quality reasons all differ from v3. Those can only change via
  the v4 code path, so v4 was really exercised.
- Mocking the MCP router (Gmail / Slack / CRM) is **correct** — an eval should
  not hit real external services. `_fake_router` at `run_experiments.py:83-153`.

### 4.2 What is wrong or overstated

**(a) The eval is partly circular — fixtures injected from expected answers.**
- `eval/run_experiments.py:162-193` — `_router_for_ticket`. Lines 167-174:
  ```python
  if ticket.expected_intent in ("refund", "billing"):
      router.read.get_kb_article = AsyncMock(return_value=KBResult(
          matched_sections=["4.2.1"], ...))
  ```
- Gate 1 escalates refund/billing tickets because the harness **injected a KB
  policy match**, and it injected it based on the ticket's *expected* intent.
- This does not test real KB retrieval. It tests "when we tell Gate 1 there is
  a policy match, does it escalate." The expected outcome is fed into the input.

**(b) n = 10, one ticket per code path — no statistical power.**
- `eval/dataset.py` defines exactly 10 `EvalTicket(...)` objects.
- "70% intent accuracy" = 7 of 10. "100% vs 90% escalation precision" = a
  one-ticket difference. No comparison on n=10 is meaningful.

**(c) One of the four comparison metrics cannot differ between v3 and v4.**
- Intent classification happens in `classify_intent_node`, which runs **before**
  the v3/v4 flag swap (the flag only swaps `enrich_context` + `draft_response`
  — see §1 and `src/graph.py:194-200`).
- Therefore intent accuracy is identical (70%/70%) **by construction**.
  Presenting it in a "v3 vs v4" table implies a measured comparison; it is the
  same code path for both.

**(d) The result files are mislabeled.**
- `eval/run_experiments.py:485` hardcodes the title `"# HITL Agent Eval
  Results -- v3"`; line 493 hardcodes the table column header `"v3"`.
- The harness has **no v3/v4 awareness in its output.** As a result
  `eval/results_v4_live.md` literally begins with the title "v3" and labels its
  column "v3".
- The two runs were distinguished only by **hand-renaming files** afterward
  (`results.md` → `results_v3_live.md` / `results_v4_live.md`). There is no
  in-file evidence of which run is which.

**(e) The "human" in human-in-the-loop is faked as always-approve.**
- `eval/run_experiments.py:263-272` — for escalated tickets the harness
  auto-resumes with `{"action": "approve", "approver_id": "U_EVAL"}`.
- The human reviewer's judgment — the core premise of the product — is never
  evaluated. Approve / edit / reject behavior, reject-loop handling, SLA
  timeout: none are exercised by the eval's resume logic.

**(f) The "Skipped without OPENROUTER_API_KEY" note is misleading but harmless.**
- `eval/results_*_live.md` shows the quality metric `4.30/5` while the Notes
  column says "Skipped without OPENROUTER_API_KEY".
- This is **not** a contradiction in the data — it is a hardcoded static
  column label (`run_experiments.py:498`). The judge did run (see §4.1). The
  note text is just wrong/stale and should be removed.

---

## 5. The "Bitext" claim is false

This is the most serious finding.

### 5.1 What the docs claim

- `CLAUDE.md` — tech-stack table: *"Eval data | Bitext Customer Support
  (50-ticket sample, 40 dev / 10 holdout)"*.
- `CLAUDE.md` — project state: *"both modes hold `false_auto_send_rate = 0%` on
  10-ticket Bitext sample."*
- `CLAUDE.md` — folder layout lists a file `data/  bitext_sample.csv`.
- The string "bitext" also appears in `README.md`, `spec.md`, `adviserplan.md`,
  `HOW_IT_WORKS.md`, `docs/architecture.md`, `docs/v4_multiagent.md`,
  `docs/superpowers/plans/2026-05-09-v4-multiagent.md`.

### 5.2 What is actually in the repo (verified)

- `data/` contains only: `acme_policies.md`, `customers_seed.json`, `prompts/`.
  **There is no `bitext_sample.csv`. There is no Bitext data file of any kind.**
  Verified: `find . -iname "*bitext*"` returns nothing;
  `ls data/` shows the three items above.
- "bitext" appears in **7 documentation/markdown files and 0 Python files and
  0 data files.** Verified: `grep -rln -i bitext` lists only `.md` docs.
- The real eval set is `eval/dataset.py` — its own module docstring (line 1)
  reads: *"10 hand-curated eval tickets — one per code path."* The 10 tickets
  were written by the author to exercise each graph branch (one FAQ, one
  refund, one angry complaint, …), with `expected_intent`, `expected_outcome`,
  and even `canned_classification` / `canned_draft` all author-supplied.

### 5.3 Why this is a real problem, not a nitpick

- Bitext is a real public dataset (Bitext Customer Support LLM Chatbot Training
  Dataset, on Hugging Face). Citing it claims "I evaluated on external data I
  did not control."
- The repo instead evaluates on 10 examples the author invented *and* wrote the
  expected answers for. A hand-written test cannot surprise its author — it
  cannot reveal failures on input shapes the author did not anticipate. That is
  the entire reason to cite an external benchmark.
- The docs go further than a vague claim — they assert specific splits
  ("50-ticket sample, 40 dev / 10 holdout") and a specific filename
  (`data/bitext_sample.csv`) that **do not exist**. That is a fabricated
  detail, not merely an omission.
- `CLAUDE.md`'s own "Red flags — do not ship with these" list names both
  *"Single test case (no Bitext eval)"* and *"Fake v1→v2→v3 metrics"*. The
  current state trips both.
- This also violates `CLAUDE.md`'s non-negotiable **Honesty rule**
  ("Never sugarcoat … a half-working feature is described as half-working").

---

## 6. The keep-vs-delete decision (v3)

An independent advisor review was run on this decision. Its conclusion:

- **Keep both versions. Do not delete v3. Do not flip the default to v4.**
- Reasoning:
  - Flipping the default to v4 asserts "v4 is better." The eval says the
    opposite (§2). Deleting the version that won and promoting the one that
    lost converts an honest result into a quiet misrepresentation.
  - The "cleaner code" benefit is small — v4 is a ~6-line flag branch plus
    `src/agents/`; v3 is two node functions. Keeping v3 costs almost nothing.
  - The honest story is the stronger portfolio story: "I built the multi-agent
    version, evaluated it head-to-head, it did not win, so I kept the simpler
    one as default and shipped the comparison." That shows measurement
    discipline and resistance to novelty bias.
- Advisor's concrete suggestions:
  - Keep `MULTIAGENT_ENABLED=0` (v3) as default — default should reflect the
    measured-best path.
  - Rewrite the `src/graph.py:171` deprecation comment — it currently schedules
    deletion of the best honesty asset. Reframe as "both paths retained — the
    A/B comparison is the deliverable."
  - Fix the README v4 section to state plainly: "v4 did not beat v3 on a
    10-ticket sample; one ticket separates them, within noise."
  - Cut the half-built scaffolding: `eval/ab_model_swap.py` and the
    un-aggregated v4 evaluators in `eval/multiagent_evaluators.py` — either run
    them and report real numbers or remove the promise.

---

## 7. Recommended actions (open — not yet done)

Ordered by urgency. None of these have been executed yet; this document only
records the findings.

1. **Stop the false "Bitext" claim (highest priority — it is a live false
   statement).** Either:
   - (a) Relabel honestly everywhere: "evaluated on 10 hand-curated tickets,
     one per code path" — true and defensible, just less impressive; or
   - (b) Actually obtain the Bitext dataset, add `data/bitext_sample.csv`, and
     run a real holdout eval. A few hours of work; makes the claim true.
2. **Fix the eval methodology** so v4 can be judged fairly:
   - Remove the expected-intent-driven KB injection (§4.2a) — retrieve KB
     matches the same way production does, or from a fixed fixture not keyed on
     the answer.
   - Add adversarial / bad-draft tickets where a single-agent Drafter produces
     something subtly wrong, so the Critic has something real to catch.
   - Add metrics that measure v4's actual value: Critic-intercept rate,
     first-draft acceptance rate, human reject-loop count.
   - Parameterize `_write_results_md` so output files self-identify as v3 or v4
     (§4.2d).
3. **Correct the docs** — `CLAUDE.md` test count (says 118, actual 136),
   the deprecation comment (§6), and the README v4 framing (§6).
4. **Decide v3's fate honestly** — recommendation (§6) is to keep both, keep v3
   as default, and reframe the deprecation comment rather than delete v3.
5. **Resolve the half-built eval scaffolding** — `eval/ab_model_swap.py` and
   `eval/multiagent_evaluators.py`: run them or remove them.

---

## 8. Quick-reference — file:line index of every proof

| Claim | Evidence |
|---|---|
| Flag swaps only 2 nodes | `src/graph.py:187`, `src/graph.py:194-200` |
| v3 deprecation scheduled for v4.1 | `src/graph.py:171-185` |
| Critic only lowers confidence | `src/agents/critic.py:79-81` |
| Drafter loop caps at 1 revision | `src/agents/drafter.py:28`, `:94-100` |
| draft_confidence is self-graded | `src/agents/drafter.py:73` |
| Harness refuses fake metrics | `eval/run_experiments.py:354-362` |
| LLM not mocked in real mode | `eval/run_experiments.py:239-250` |
| KB match injected from expected_intent | `eval/run_experiments.py:167-174` |
| Output title/column hardcoded "v3" | `eval/run_experiments.py:485`, `:493` |
| Human faked as always-approve | `eval/run_experiments.py:263-272` |
| Eval set is 10 hand-curated tickets | `eval/dataset.py:1` (docstring), 10× `EvalTicket(` |
| No Bitext data file exists | `data/` = acme_policies.md, customers_seed.json, prompts/ |
| "bitext" only in docs, never code/data | `grep -rln -i bitext` → 7 `.md` files only |
| v4 over-escalated eval-t07 | `eval/results_v4_live.md:50-54` |
| Intent accuracy identical 70%/70% | `eval/results_v3_live.md:12`, `results_v4_live.md:12` |
