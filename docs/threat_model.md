# Threat model — HITL Customer Support Agent

> STRIDE-shaped threat model for the production-mode agent. Every "Existing
> mitigation" entry cites a real file path in this repo — if a mitigation is
> claimed but the code can't be pointed to, that's a gap, not a feature.

## Scope

This threat model covers the **runtime production deploy** of the agent:
inbound customer email → LangGraph workflow → Slack approval → outbound
customer email. Out of scope: the local dev / eval harness (`eval/`,
`tests/`), the model-training data pipeline (we don't train models —
the LLM is consumed as an API), and end-user mail-client security.

## Trust boundaries

```
   ┌─────────────────────────────────────────────────────────────────┐
   │                       UNTRUSTED ZONE                            │
   │   ── customer (sender of inbound emails, arbitrary content)     │
   │   ── public internet (attackers, scrapers, prompt-injectors)    │
   └────────────┬────────────────────────────────────────────────────┘
                │ Gmail IMAP IDLE (TLS)
   ┌────────────▼────────────────────────────────────────────────────┐
   │                    SEMI-TRUSTED ZONE                            │
   │   ── Slack workspace (approver humans, but Slack is third-party)│
   │   ── OpenAI / OpenRouter API (third-party LLM provider)         │
   │   ── Bitext dataset (third-party, used only at eval time)       │
   └────────────┬────────────────────────────────────────────────────┘
                │ HTTPS + signed webhooks
   ┌────────────▼────────────────────────────────────────────────────┐
   │                       TRUSTED ZONE                              │
   │   ── LangGraph orchestrator + SQLite checkpointer               │
   │   ── 3 MCP subprocesses (Read / Email Write / Slack Write)      │
   │   ── audit log (append-only)                                    │
   │   ── ACME policy corpus (data/acme_policies.md)                 │
   └─────────────────────────────────────────────────────────────────┘
```

## Asset list

| ID | Asset | Confidentiality | Integrity | Availability |
|---|---|---|---|---|
| A1 | Customer email body + headers (inbound) | High (PII) | High | Medium |
| A2 | Outbound agent reply | Medium | **Critical** (no false auto-sends) | Medium |
| A3 | Gmail App Password | **Critical** | High | High |
| A4 | LLM API key (OpenAI / OpenRouter) | **Critical** | High | Medium |
| A5 | Slack signing secret + bot token | **Critical** | High | High |
| A6 | SQLite checkpointer DB (state + audit_log) | High (PII) | **Critical** | High |
| A7 | ACME policy KB (data/acme_policies.md) | Low | Medium | High |
| A8 | Customer profile / history (from CRM) | High (PII) | Medium | Medium |
| A9 | MCP subprocesses (3 capability-separated) | n/a | **Critical** | High |
| A10 | LangSmith traces | Medium (may contain PII) | Low | Low |

## STRIDE threats per asset

The "Existing mitigation" column points to real files. If a row's mitigation
is empty or marked "**MISSING**", that's a real production gap — track in
`PRODUCTION_READINESS.md`.

### A1 — Customer email body (PII, untrusted content)

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Email sender spoofing (faked `From:`) | Spoofing | Envelope-from extracted server-side via `aioimaplib`, not the user-supplied `From:` header. See `src/email_listener.py` IMAP message-id extraction; threading uses `In-Reply-To` / `References`, not display name. | Medium — IMAP envelope is trustworthy but Gmail does accept relay traffic from misconfigured upstream senders. | DMARC / DKIM / SPF verification on the listener side is **MISSING** — we rely on Gmail's inbound spam filter. |
| PII in raw email reaching the LLM | Information disclosure | `src/pii.py` redacts emails, names, phone numbers BEFORE the message reaches the classifier. Restored at `finalize_action`. Tested in `tests/test_pii.py` (19 tests). | Low — PII never reaches the LLM in cleartext. | Redaction is regex-based; a sufficiently novel PII shape (e.g. an SSN-shaped string with letters) can slip through. Address: add LLM-based PII auditor as a second-pass scrubber. |
| Prompt injection inside the customer message | Tampering / Elevation | Classifier prompt in `src/llm.py:CLASSIFY_SYSTEM` instructs to classify by intent, not echo. Adversarial eval `eval/adversarial_dataset.py:adv-pi-*` probes 5 injection styles; v3/v4 catch 4/5 each on the live run. | **HIGH** — `adv-pi-03` (system-prompt leak) still false-positives the check and one real injection still leaks the term "system prompt" into the draft. | The single most-active threat. A guard-model layer (separate LLM that classifies messages as benign/injection) is **MISSING**. |
| Oversize / pathological input (DoS) | Denial of service | `data/adversarial_eval.csv:adv-pa-03` 10K-char wall passes `no_crash` on v3+v4. Token-limit will eventually cap LLM input. | Low — single ticket cannot DoS the queue. | No per-customer rate limit at the IMAP listener. **MISSING**. |

### A2 — Outbound agent reply (the critical safety asset)

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| False auto-send (unsafe content auto-sent) | **Tampering / Elevation** | Two-gate routing in `src/policy.py`: Gate 1 = policy risk (refund/money/legal/angry/edge-intent escalate); Gate 2 = confidence floor (intent + draft < 0.85 escalate). 136+ tests including `test_policy.py` cover gate behaviour. Primary safety metric `false_auto_send_rate == 0` is **measured by eval, not yet enforced by CI** (gate is in METHODOLOGY.md's Limitations list). | **HIGH** — bitext27_test and adversarial sets show non-zero failure rate. See `eval/bitext27_findings.md`. | Architectural ceiling: Critic operates on (draft, customer_message), cannot detect classifier-confidently-wrong. A Classifier-Critic is the highest-EV next step (see plan: rippling-soaring-sutherland.md). |
| Send to wrong recipient (cross-ticket leak) | Information disclosure | `src/pii.py:get_envelope_from(ticket_id)` recovers the envelope-from from a separate vault; `src/nodes._customer_email_from_audit` uses vault first, audit_log fallback. Audit C1 closed this exact bug. | Low — vault is keyed on ticket_id. | Vault is in-process memory by default. Opt-in persistent sidecar via `PII_VAULT_DB_PATH` (added 2026-05-24 to support kill-mid-interrupt durable execution) puts envelope_from + token_map in a local SQLite file — encrypt the data dir or restrict ACL when enabled. Multi-worker deploys still need an external store (Redis) — **MISSING** for horizontal scale. |
| Duplicate send (idempotency failure) | Integrity / Availability | `src/nodes.py:send_email_node` checks `sent_message_id` in AgentState before SMTP. SMTP retries don't dedupe at the protocol level — we own this. Tested in `tests/test_email_idempotency.py` (6 tests). | Low. | The `send_idempotency_key` is per-ticket; a re-injection of the same ticket_id with mutated body would not retrigger dedupe. Address via content-hash dedupe. |
| Echo of attacker-controlled HTML in reply | Tampering | `eval/adversarial_evaluators._check_no_injected_text` with `forbidden_strings` covering `<script>`, `onerror=`, etc. Adversarial `adv-pa-04` passes on v3+v4. | Low — the LLM does not by default round-trip raw HTML. | The check is a regression detector; a creative model could rephrase HTML in plain text. Real fix: HTML-strip the outbound body before SMTP — **MISSING**. |

### A3 — Gmail App Password

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Secret exposed in source repo | Information disclosure | `src/config.py` reads via pydantic-settings from `.env`; `.env` is gitignored; `.env.example` carries placeholders only. | Low. | No secret-rotation policy. **MISSING**. Production deploy must use Vault / AWS Secrets Manager / Doppler — `.env` files are dev-only. |
| Account hijack via App Password leak | Spoofing / Elevation | Gmail App Passwords are scoped to a single app and revocable in Gmail Settings; we recommend per-deploy distinct passwords. | Medium. | App Passwords are deprecated in favour of OAuth2 for Gmail in 2026; we have not migrated. **OAuth2 is MISSING.** |

### A4 — LLM API key

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Key exfiltration from `.env` | Information disclosure | `.gitignore` excludes `.env`. The smoke probe `eval/_smoke_openai.py` only prints the last 6 chars. | Low. | No key-rotation policy. **MISSING**. |
| Cost-explosion attack (attacker triggers loops) | Denial of service | Drafter↔Critic loop is hard-capped at `MAX_CRITIC_ITERATIONS=3` in `src/agents/drafter.py`. `MAX_HUMAN_REJECTIONS=3` caps the human-loop. Per-call cost telemetry surfaced in `eval/results_*.json` via Commit 1. | Low at solo-dev volume. | No live cost budget alert. **MISSING.** Production deploy would need per-day spend cap with circuit-breaker. |
| Provider account locked / credit zeroed | Availability | `LLM_PROVIDER` env switch (`src/llm.py`) supports OpenAI or OpenRouter. Smoke probes verify `:free` tier availability. | Medium — single point of failure when one provider goes down. | No automatic failover. **MISSING.** Production would need a secondary-provider fallback. |

### A5 — Slack signing secret + bot token

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Forged webhook from attacker | Spoofing | `src/slack_handler.py` verifies HMAC-SHA256 of `v0:{timestamp}:{body}` with the signing secret, constant-time compare, 5-minute replay window. CLAUDE.md non-negotiable. | Low. | Webhook is open to the internet by design (it has to receive Slack POSTs). Rate-limit by IP would be defence-in-depth — **MISSING**. |
| Replay attack (capture + replay valid webhook) | Spoofing / Tampering | 5-minute replay window on `v0:{timestamp}` rejection. | Low. | No nonce store — within the 5-minute window the same payload can be replayed (cheap mitigation: short window suffices). |
| Approver Slack account compromised | Spoofing | Slack 2FA / SSO is on the customer org's side. The agent treats any `approver_id` in the workspace channel as authoritative. | Medium. | No multi-approver requirement for high-risk tickets. **MISSING** — production should require 2-of-N approvers on legal / large-refund tickets. |

### A6 — SQLite checkpointer DB

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Disk theft / unauthorised read | Information disclosure | File ACL on the deploy host. The PII vault (separate from the checkpoint) stores envelope-from data; the checkpoint itself has redacted messages only (PII tokens, not real PII). | Medium — if the host is compromised, the redacted checkpoint still leaks structure. | Encryption at rest is **MISSING**. SQLite is plaintext. Production should use SQLCipher or move to a managed DB (Postgres + EBS encryption). |
| DB corruption / loss | Integrity / Availability | LangGraph checkpointer writes per super-step; partial failures don't corrupt earlier states. `test_resume.py` validates kill-and-resume. | Medium. | No backup rotation. **MISSING.** Production needs scheduled backups + DR drill. |
| Multi-worker write contention | Availability | Single-writer assumption is documented; no current multi-worker deploy. | High in any horizontal-scale scenario. | SQLite is single-writer; production horizontal scale requires migrating to a managed DB. **Migration MISSING.** |

### A7 — ACME policy corpus

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Policy file tampering | Tampering | File is in git; PR review required for edits. `mcp_server/support_read.py:search_kb` reads from the committed file. | Low. | No runtime integrity check (signed hash). For a high-stakes deploy, sign the file + verify at startup. **MISSING for compliance regimes.** |

### A8 — Customer profile / history (from CRM)

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Cross-customer profile leak (wrong profile returned for a ticket) | Information disclosure | `_customer_email_from_audit` (`src/nodes.py` + `src/agents/researcher.py`) recovers envelope-from before any CRM lookup. Audit C1 fixed the original spoofed-recipient bug. | Low — keyed on trustworthy envelope-from. | Same vault concern as A2 (in-process memory; multi-worker needs external store). |

### A9 — MCP subprocesses (capability isolation)

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| Capability bleed (Read server gains send_email power) | Elevation of privilege | Three separate MCP subprocesses (`mcp_server/support_read.py`, `support_email_write.py`, `support_slack_write.py`). Each exposes ONLY its capability set. The runtime router (`src/mcp_client.py:_ReadClient` etc.) does not allow cross-server calls. Tested in `tests/test_mcp_subprocess_boot.py`. | Low — the design is the mitigation. | No runtime capability assertion on every call (we trust the import-time wiring). |
| Subprocess crash / hang | Availability | Each MCP server is its own subprocess; one crash doesn't kill the LangGraph orchestrator. | Low. | No health-check on subprocesses; a hung child would block its tool call until the call timeout (which is not explicitly configured). **MISSING.** |

### A10 — LangSmith traces

| Threat | Type | Existing mitigation | Residual risk | Honest gap |
|---|---|---|---|---|
| PII in trace metadata | Information disclosure | Inputs reach LangSmith only AFTER PII redaction (`src/pii.py` runs before `classify_intent`). Metadata in `src/llm.py:_ls_metadata` ships only graph_version / ticket_id / intent — no message body. | Low — by design. | Token-cost metadata is captured but the underlying provider may log inputs separately (OpenAI's API logs). Mitigate via the provider's DPA — **VERIFICATION MISSING** in this repo. |
| LangSmith account compromise | Information disclosure | Token in `.env`; standard secret-handling. | Medium. | Same rotation gap as A4. |

## Cross-cutting threats not asset-bound

| Threat | Existing mitigation | Honest gap |
|---|---|---|
| Supply-chain attack (compromised pip dep) | `pip-audit --strict` in CI (`.github/workflows/ci.yml`); `bandit -r src mcp_server -ll`. | No SBOM, no signed-image production deploy. **MISSING.** |
| Insider modifies code without review | Single dev → no review process; production should require CODEOWNERS + branch protection. | **CODEOWNERS file MISSING.** |
| Compliance: GDPR Art. 22 (automated-decision records) | Append-only audit_log captures every gate decision + draft + approver. | No customer-facing data-export tooling. **MISSING.** |
| Compliance: EU AI Act (high-risk system labelling) | n/a in this version | **EU AI Act risk classification + labelling MISSING.** |

## Summary — the residual-risk register

In rough priority order (highest residual first):

1. **Prompt injection via customer body** (A1) — visible adversarial-eval failures; a guard-model layer is the highest-EV remaining safety work.
2. **False auto-send on confidently-wrong classification** (A2) — t08 / pii_leak_probe cases; needs a Classifier-Critic.
3. **Disk-level PII exposure** (A6) — SQLite is plaintext; encryption at rest is a hard requirement for any compliance-bound deploy.
4. **Multi-approver requirement for high-value tickets** (A5) — production HITL should require ≥ 2 approvers on legal / refund-over-threshold tickets.
5. **Gmail OAuth2 migration** (A3) — App Passwords are deprecated.
6. **Multi-worker horizontal scale path** (A6, A8) — current SQLite + in-process vault design is single-writer.
7. **Cost / rate-limit circuit-breaker** (A1, A4) — no per-day spend cap; no per-customer rate limit.
8. **EU AI Act + GDPR Art. 22 compliance docs** — neither shipped.

Each of these is honest engineering work that an enterprise security
reviewer would expect to see tracked, NOT silently absent. The
production-readiness checklist in `PRODUCTION_READINESS.md` (forthcoming —
or referenced in the plan `rippling-soaring-sutherland.md`) covers the
non-security side of the same gap analysis.

---

*Maintainer note: when a security control is added or removed in the codebase,
update the matching row here. STRIDE threat models drift fast; an out-of-date
threat model is worse than no threat model.*
