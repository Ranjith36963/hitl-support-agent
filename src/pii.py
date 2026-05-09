"""PII redaction and restoration middleware.

Replaces emails, credit card numbers, and phone numbers with stable tokens
before passing text to any LLM call. Tokens are restored in the Finalize
Action node before sending the real customer reply.

Token format:
  [EMAIL_1], [EMAIL_2], ...
  [PHONE_1], [PHONE_2], ...
  [CC_1], [CC_2], ...

Invariants:
- Same PII value within one redact() call → same token (stable).
- token_map keys are unique; values are the original PII strings.
- restore(redact(text)[0], redact(text)[1]) == text (round-trip identity).
- restore() substitutes longer tokens first to defeat [EMAIL_1] vs [EMAIL_10]
  prefix collisions when the same ticket has 10+ distinct emails.
- redact()/restore() are pure functions — no I/O, no LLM, no DB.

Vault (added 2026-05-09 to close audit findings C1 + C2):
- Token map MUST NOT be stored in `audit_log` — that field is checkpointed
  to SQLite + emitted to LangSmith, leaking real PII to a third-party SaaS
  and an on-disk persistence layer in violation of the "LLM never sees real
  PII" invariant in spirit.
- Module-level `_TOKEN_VAULT` keyed by ticket_id is the new home. It lives
  for the duration of the process; cleared at terminal nodes.
- An additional `envelope_from` slot per ticket distinguishes the SMTP
  envelope sender (trustworthy, captured from Return-Path) from the RFC-822
  `From:` header (spoofable). Send Email uses envelope_from as the recipient.
"""

from __future__ import annotations

import re
import threading

# ---------------------------------------------------------------------------
# Ephemeral PII vault — module-level, NOT serialized to state, NOT checkpointed.
# Keyed by ticket_id. Threading lock guards concurrent ticket processing.
# ---------------------------------------------------------------------------

_TOKEN_VAULT: dict[str, dict[str, str]] = {}
_ENVELOPE_VAULT: dict[str, str] = {}
_VAULT_LOCK = threading.Lock()


def store_token_map(ticket_id: str, token_map: dict[str, str]) -> None:
    """Persist a token_map for ticket_id in the ephemeral vault."""
    with _VAULT_LOCK:
        _TOKEN_VAULT[ticket_id] = dict(token_map)


def get_token_map(ticket_id: str) -> dict[str, str]:
    """Retrieve the token_map for ticket_id (empty dict if absent)."""
    with _VAULT_LOCK:
        return dict(_TOKEN_VAULT.get(ticket_id, {}))


def store_envelope_from(ticket_id: str, envelope_from: str) -> None:
    """Persist the SMTP envelope-from address for ticket_id.

    This is the address Send Email uses as `to:`, NOT the spoofable
    RFC-822 `From:` header. Captured from `Return-Path` by the email
    listener at ingest time.
    """
    with _VAULT_LOCK:
        _ENVELOPE_VAULT[ticket_id] = envelope_from


def get_envelope_from(ticket_id: str) -> str | None:
    """Retrieve the SMTP envelope-from for ticket_id (None if absent)."""
    with _VAULT_LOCK:
        return _ENVELOPE_VAULT.get(ticket_id)


def clear_ticket(ticket_id: str) -> None:
    """Remove all PII vault entries for ticket_id. Call at terminal nodes."""
    with _VAULT_LOCK:
        _TOKEN_VAULT.pop(ticket_id, None)
        _ENVELOPE_VAULT.pop(ticket_id, None)

# ---------------------------------------------------------------------------
# Regex patterns (ordered most-specific first to avoid partial matches)
# ---------------------------------------------------------------------------

# Credit card: 16 digits, optionally grouped with spaces or hyphens
_CC_RE = re.compile(
    r"\b(?:\d{4}[\s\-]){3}\d{4}\b"  # 4x4 with separators
    r"|\b\d{16}\b"                   # 16 consecutive digits
)

# Phone: US-centric patterns — (555) 867-5309, 555-867-5309, 555.867.5309
# Requires 10 digits in common US patterns (not raw 10-digit runs → avoid
# matching plain numbers like order IDs).
_PHONE_RE = re.compile(
    r"\(?\d{3}\)?[\s\-\.]\d{3}[\-\.]\d{4}\b"
)

# Email: RFC-5321 simplified — user@domain.tld
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)


def redact(text: str) -> tuple[str, dict[str, str]]:
    """Replace PII in *text* with stable tokens.

    Returns:
        (redacted_text, token_map) where token_map maps token → original.
        token_map is empty when no PII is found.

    The same PII value always maps to the same token within one call.
    """
    if not text:
        return text, {}

    token_map: dict[str, str] = {}
    # Reverse map: original_value → token (for stable per-value assignment)
    value_to_token: dict[str, str] = {}
    counters: dict[str, int] = {"EMAIL": 0, "PHONE": 0, "CC": 0}

    def _make_token(prefix: str, value: str) -> str:
        if value in value_to_token:
            return value_to_token[value]
        counters[prefix] += 1
        token = f"[{prefix}_{counters[prefix]}]"
        value_to_token[value] = token
        token_map[token] = value
        return token

    # Order matters: CC before phone (16-digit strings could partially match
    # phone patterns), and both before email.
    result = text

    # 1. Credit cards
    def _replace_cc(m: re.Match[str]) -> str:
        return _make_token("CC", m.group(0))

    result = _CC_RE.sub(_replace_cc, result)

    # 2. Phones (operate on post-CC-redacted string so CC tokens don't clash)
    def _replace_phone(m: re.Match[str]) -> str:
        return _make_token("PHONE", m.group(0))

    result = _PHONE_RE.sub(_replace_phone, result)

    # 3. Emails
    def _replace_email(m: re.Match[str]) -> str:
        return _make_token("EMAIL", m.group(0))

    result = _EMAIL_RE.sub(_replace_email, result)

    return result, token_map


def restore(text: str, token_map: dict[str, str]) -> str:
    """Replace tokens in *text* with their original PII values.

    Substitutes longer tokens first so `[EMAIL_10]` is replaced before
    `[EMAIL_1]` — otherwise the shorter token's substitution leaves a
    `0]` suffix in place of `[EMAIL_10]` in tickets with 10+ emails.
    Safe to call on text that has no tokens (returns text unchanged).
    """
    if not token_map:
        return text
    result = text
    # Longest-first iteration defeats [EMAIL_1] vs [EMAIL_10] prefix collisions.
    for token in sorted(token_map.keys(), key=len, reverse=True):
        result = result.replace(token, token_map[token])
    return result
