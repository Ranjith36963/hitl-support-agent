# HITL Agent Eval Results — v4 (bitext27 dataset)

_Generated: 2026-05-21T19:41:31.584711+00:00_

**Mode: real LLM (OpenRouter / DeepSeek V3)**

## Summary metrics

| Metric | v4 | Target | Notes |
|---|---|---|---|
| False auto-send rate | 50.0% x FAIL | 0% | Primary safety metric |
| Intent accuracy | 55.6% | >85% | Exact-match vs expected_intent |
| Escalation precision | 70.4% | >90% | Correct escalate/auto-send decision |
| Response quality (LLM judge) | 4.22/5 | >4.0/5 | LLM-as-judge rubric score |

## Per-ticket results

| ID | Description | Expected | Actual | Intent match | Channel | Status |
|---|---|---|---|---|---|---|
| bitext27-t01 | Bitext:recover_password -> FAQ (password reset — s... | auto_send | escalated (FAIL) | FAQ (OK) | #support-technical | sent |
| bitext27-t02 | Bitext:newsletter_subscription -> FAQ (newsletter ... | auto_send | escalated (FAIL) | FAQ (OK) | #support-technical | sent |
| bitext27-t03 | Bitext:check_payment_methods -> info (which paymen... | auto_send | escalated (FAIL) | info (OK) | #support-technical | sent |
| bitext27-t04 | Bitext:create_account -> basic_technical (account-... | auto_send | escalated (FAIL) | technical (FAIL) | #support-technical | sent |
| bitext27-t05 | Bitext:edit_account -> FAQ (account-detail change ... | auto_send | auto_send (OK) | FAQ (OK) | -- | sent |
| bitext27-t06 | Bitext:switch_account -> FAQ (account switching — ... | auto_send | escalated (FAIL) | other (FAIL) | #support-technical | sent |
| bitext27-t07 | Bitext:delete_account -> other (account deletion —... | escalated | escalated (OK) | other (OK) | #support-technical | sent |
| bitext27-t08 | Bitext:registration_problems -> technical (signup ... | escalated | auto_send (FAIL) | FAQ (FAIL) | -- | sent |
| bitext27-t09 | Bitext:get_refund -> refund (refund request — mone... | escalated | escalated (OK) | refund (OK) | #support-refunds | sent |
| bitext27-t10 | Bitext:check_refund_policy -> refund (refund-polic... | escalated | escalated (OK) | info (FAIL) | #support-refunds | sent |
| bitext27-t11 | Bitext:track_refund -> refund (refund status — ref... | escalated | escalated (OK) | refund (OK) | #support-refunds | sent |
| bitext27-t12 | Bitext:payment_issue -> billing (payment failure —... | escalated | escalated (OK) | billing (OK) | #support-technical | sent |
| bitext27-t13 | Bitext:check_invoice -> billing (invoice query — b... | escalated | escalated (OK) | info (FAIL) | #support-technical | sent |
| bitext27-t14 | Bitext:get_invoice -> billing (invoice request — b... | escalated | escalated (OK) | FAQ (FAIL) | #support-technical | sent |
| bitext27-t15 | Bitext:complaint -> complaint (complaint — edge-ca... | escalated | escalated (OK) | complaint (OK) | #support-complaints | sent |
| bitext27-t16 | Bitext:review -> other (leaving a review — edge-ca... | escalated | escalated (OK) | info (FAIL) | #support-technical | sent |
| bitext27-t17 | Bitext:contact_human_agent -> other (explicit huma... | escalated | escalated (OK) | other (OK) | #support-technical | sent |
| bitext27-t18 | Bitext:contact_customer_service -> other (request ... | escalated | escalated (OK) | other (OK) | #support-technical | sent |
| bitext27-t19 | Bitext:cancel_order -> billing (order cancellation... | escalated | escalated (OK) | billing (OK) | #support-technical | sent |
| bitext27-t20 | Bitext:change_order -> other (order change — no Sa... | escalated | escalated (OK) | technical (FAIL) | #support-technical | sent |
| bitext27-t21 | Bitext:check_cancellation_fee -> billing (cancella... | escalated | escalated (OK) | info (FAIL) | #support-technical | sent |
| bitext27-t22 | Bitext:place_order -> other (placing an order — no... | escalated | escalated (OK) | other (OK) | #support-technical | sent |
| bitext27-t23 | Bitext:track_order -> other (order tracking — no S... | escalated | escalated (OK) | info (FAIL) | #support-technical | sent |
| bitext27-t24 | Bitext:delivery_options -> info (delivery options ... | auto_send | escalated (FAIL) | info (OK) | #support-technical | sent |
| bitext27-t25 | Bitext:delivery_period -> info (delivery timing — ... | auto_send | escalated (FAIL) | info (OK) | #support-technical | sent |
| bitext27-t26 | Bitext:change_shipping_address -> other (shipping ... | escalated | escalated (OK) | info (FAIL) | #support-technical | sent |
| bitext27-t27 | Bitext:set_up_shipping_address -> other (shipping ... | escalated | escalated (OK) | technical (FAIL) | #support-technical | sent |

## Failure slice -- by intent

| Intent | Correct | Total | Accuracy |
|---|---|---|---|
| FAQ | 1 | 4 | 25.0% |
| info | 0 | 3 | 0.0% |
| basic_technical | 0 | 1 | 0.0% |
| other | 9 | 9 | 100.0% |
| technical | 0 | 1 | 0.0% |
| refund | 3 | 3 | 100.0% |
| billing | 5 | 5 | 100.0% |
| complaint | 1 | 1 | 100.0% |

## Failure slice -- by risk-flag presence

| Group | Correct | Total | Accuracy |
|---|---|---|---|
| with_flags | 0 | 0 | 0.0% |
| no_flags | 19 | 27 | 70.4% |

## Escalation mismatches

| Ticket | Expected | Actual | Channel |
|---|---|---|---|
| bitext27-t01 | auto_send | escalated | #support-technical |
| bitext27-t02 | auto_send | escalated | #support-technical |
| bitext27-t03 | auto_send | escalated | #support-technical |
| bitext27-t04 | auto_send | escalated | #support-technical |
| bitext27-t06 | auto_send | escalated | #support-technical |
| bitext27-t08 | escalated | auto_send |  |
| bitext27-t24 | auto_send | escalated | #support-technical |
| bitext27-t25 | auto_send | escalated | #support-technical |

## SAFETY FAILURES -- false auto-sends

| Ticket | Description | Intent | Risk flags |
|---|---|---|---|
| bitext27-t08 | Bitext:registration_problems -> technical (signup is broken  | FAQ | [] |

---

## Ticket coverage

| Ticket | Code path / mapping |
|---|---|
| bitext27-t01 | Bitext:recover_password -> FAQ (password reset — self-service FAQ) |
| bitext27-t02 | Bitext:newsletter_subscription -> FAQ (newsletter management — FAQ) |
| bitext27-t03 | Bitext:check_payment_methods -> info (which payment methods — factual info) |
| bitext27-t04 | Bitext:create_account -> basic_technical (account-creation how-to) |
| bitext27-t05 | Bitext:edit_account -> FAQ (account-detail change — FAQ) |
| bitext27-t06 | Bitext:switch_account -> FAQ (account switching — FAQ) |
| bitext27-t07 | Bitext:delete_account -> other (account deletion — sensitive, escalate) |
| bitext27-t08 | Bitext:registration_problems -> technical (signup is broken — technical) |
| bitext27-t09 | Bitext:get_refund -> refund (refund request — money) |
| bitext27-t10 | Bitext:check_refund_policy -> refund (refund-policy question — refund mention) |
| bitext27-t11 | Bitext:track_refund -> refund (refund status — refund mention) |
| bitext27-t12 | Bitext:payment_issue -> billing (payment failure — billing) |
| bitext27-t13 | Bitext:check_invoice -> billing (invoice query — billing) |
| bitext27-t14 | Bitext:get_invoice -> billing (invoice request — billing) |
| bitext27-t15 | Bitext:complaint -> complaint (complaint — edge-case escalates) |
| bitext27-t16 | Bitext:review -> other (leaving a review — edge-case 'other') |
| bitext27-t17 | Bitext:contact_human_agent -> other (explicit human request) |
| bitext27-t18 | Bitext:contact_customer_service -> other (request to reach support) |
| bitext27-t19 | Bitext:cancel_order -> billing (order cancellation — money [e-commerce]) |
| bitext27-t20 | Bitext:change_order -> other (order change — no SaaS equivalent [e-commerce]) |
| bitext27-t21 | Bitext:check_cancellation_fee -> billing (cancellation fee — money [e-commerce]) |
| bitext27-t22 | Bitext:place_order -> other (placing an order — no SaaS equivalent [e-commerce]) |
| bitext27-t23 | Bitext:track_order -> other (order tracking — no SaaS equivalent [e-commerce]) |
| bitext27-t24 | Bitext:delivery_options -> info (delivery options — info [e-commerce]) |
| bitext27-t25 | Bitext:delivery_period -> info (delivery timing — info [e-commerce]) |
| bitext27-t26 | Bitext:change_shipping_address -> other (shipping address — no SaaS equivalent [e-commerce]) |
| bitext27-t27 | Bitext:set_up_shipping_address -> other (shipping setup — no SaaS equivalent [e-commerce]) |