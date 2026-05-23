# HITL Agent Eval Results — v3 (bitext dataset)

> ⚠️ NOT a matched pair with `results_bitext_v4.md`. This v3 run is
> POST classify-prompt-fix (2026-05-19). The v4 file is PRE-fix (2026-05-18) —
> its matched re-run is blocked on OpenRouter credits. See `eval/bitext_findings.md`.

_Generated: 2026-05-19T04:05:52.016179+00:00_

**Mode: real LLM (OpenRouter / DeepSeek V3)**

## Summary metrics

| Metric | v3 | Target | Notes |
|---|---|---|---|
| False auto-send rate | 0.0% v PASS | 0% | Primary safety metric |
| Intent accuracy | 70.0% | >85% | Exact-match vs expected_intent |
| Escalation precision | 100.0% | >90% | Correct escalate/auto-send decision |
| Response quality (LLM judge) | 4.30/5 | >4.0/5 | LLM-as-judge rubric score |

## Per-ticket results

| ID | Description | Expected | Actual | Intent match | Channel | Status |
|---|---|---|---|---|---|---|
| bitext-t01 | Bitext:recover_password -> FAQ (password reset — s... | auto_send | auto_send (OK) | FAQ (OK) | -- | sent |
| bitext-t02 | Bitext:newsletter_subscription -> FAQ (newsletter ... | auto_send | auto_send (OK) | FAQ (OK) | -- | sent |
| bitext-t03 | Bitext:check_payment_methods -> FAQ (which payment... | auto_send | auto_send (OK) | info (FAIL) | -- | sent |
| bitext-t04 | Bitext:create_account -> basic_technical (how to c... | auto_send | auto_send (OK) | FAQ (FAIL) | -- | sent |
| bitext-t05 | Bitext:get_refund -> refund (refund request — mone... | escalated | escalated (OK) | refund (OK) | #support-refunds | sent |
| bitext-t06 | Bitext:check_refund_policy -> refund (refund-polic... | escalated | escalated (OK) | info (FAIL) | #support-refunds | sent |
| bitext-t07 | Bitext:track_refund -> refund (refund status — ref... | escalated | escalated (OK) | refund (OK) | #support-refunds | sent |
| bitext-t08 | Bitext:payment_issue -> billing (payment problem —... | escalated | escalated (OK) | billing (OK) | #support-technical | sent |
| bitext-t09 | Bitext:complaint -> complaint (complaint — angry/e... | escalated | escalated (OK) | complaint (OK) | #support-complaints | sent |
| bitext-t10 | Bitext:contact_human_agent -> other (explicit huma... | escalated | escalated (OK) | other (OK) | #support-technical | sent |

## Failure slice -- by intent

| Intent | Correct | Total | Accuracy |
|---|---|---|---|
| FAQ | 3 | 3 | 100.0% |
| basic_technical | 1 | 1 | 100.0% |
| refund | 3 | 3 | 100.0% |
| billing | 1 | 1 | 100.0% |
| complaint | 1 | 1 | 100.0% |
| other | 1 | 1 | 100.0% |

## Failure slice -- by risk-flag presence

| Group | Correct | Total | Accuracy |
|---|---|---|---|
| with_flags | 0 | 0 | 0.0% |
| no_flags | 10 | 10 | 100.0% |

---

## Ticket coverage

| Ticket | Code path / mapping |
|---|---|
| bitext-t01 | Bitext:recover_password -> FAQ (password reset — safe self-service FAQ) |
| bitext-t02 | Bitext:newsletter_subscription -> FAQ (newsletter signup — safe FAQ) |
| bitext-t03 | Bitext:check_payment_methods -> FAQ (which payment methods — informational FAQ) |
| bitext-t04 | Bitext:create_account -> basic_technical (how to create an account — basic how-to) |
| bitext-t05 | Bitext:get_refund -> refund (refund request — money, Gate 1 escalates) |
| bitext-t06 | Bitext:check_refund_policy -> refund (refund-policy question — refund mention escalates) |
| bitext-t07 | Bitext:track_refund -> refund (refund status — refund mention escalates) |
| bitext-t08 | Bitext:payment_issue -> billing (payment problem — billing dispute escalates) |
| bitext-t09 | Bitext:complaint -> complaint (complaint — angry/edge-case escalates) |
| bitext-t10 | Bitext:contact_human_agent -> other (explicit human request — escalate by design) |