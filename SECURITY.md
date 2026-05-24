# Security policy

## Supported versions

This is a portfolio project. The only supported version is the current `main`
branch. There is no LTS, no backport policy, and no published release artifact.

## Reporting a vulnerability

If you find a security issue (a real one — not a hypothetical concern from
reading the code), please **do not open a public GitHub issue**. Use one of:

1. **Preferred:** [Open a private security advisory on GitHub](https://github.com/Ranjith36963/hitl-support-agent/security/advisories/new).
   This is GitHub's private vulnerability reporting flow — only the maintainer
   sees it until a fix lands.
2. **Fallback:** Email `ranjithmaligaguruprakash@gmail.com` with subject
   `[hitl-support-agent SECURITY]`. Allow up to 7 days for an initial reply.

When reporting, include:

- A clear description of the issue and the attack scenario it enables.
- Reproduction steps (a minimal failing test case is ideal).
- Affected files / commit hash you tested against.
- Your suggested fix, if you have one.

Responsible-disclosure expectation: please give the maintainer reasonable
time (~30 days) to ship a fix before public disclosure.

## What's in scope

Real bugs that affect the security posture of the agent as deployed:

- Auth / signature bypass (Slack HMAC, Gmail App Password handling)
- PII leak paths (LangSmith trace exfiltration, audit-log poisoning, vault
  bypass)
- Capability-isolation bypass between the three MCP servers
- Prompt-injection bypasses of the documented two-gate routing
- Idempotency-failure paths that could cause duplicate sends
- Webhook replay / forgery
- Dependency CVEs with a real reachable exploit in this codebase

## What's out of scope

- Theoretical risks not realised in the current code
- Issues that require the attacker to already have shell on the host or
  write access to `.env`
- Local-development-only configurations (Grafana anonymous viewer on
  `127.0.0.1:3000`, dev-mode Socket Mode tokens, etc.) — these are
  documented as dev-only in `docs/threat_model.md`
- Findings in deferred features explicitly marked "**MISSING**" in
  `docs/threat_model.md` — those are tracked production gaps, not bugs

## Architecture-level defences (for context)

The intended defence-in-depth is laid out in detail in
[`docs/threat_model.md`](./docs/threat_model.md). Highlights:

- **Three MCP servers with capability isolation** — Read can't send,
  Email Write can't post Slack, Slack Write can't email.
- **HMAC-verified Slack webhooks** with 5-minute replay window and
  constant-time signature compare.
- **PII redaction at entry, restore in Finalize** — the LLM never sees
  real PII in cleartext.
- **App-layer idempotency** on the send path; SMTP itself does not dedupe.
- **Two-gate routing** — Policy Risk first, Confidence Floor second; both
  must pass for auto-send.
- **`false_auto_send_rate` as primary safety metric** — measured by eval
  (gate not yet enforced in CI — flagged in `eval/METHODOLOGY.md`
  Limitations).

If your report demonstrates one of these defences being bypassable end-to-end,
that's the highest-priority class of finding.
