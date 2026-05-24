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

Persistent sidecar (added 2026-05-24, opt-in via `pii_vault_db_path`):
- Default behaviour unchanged: vault lives only in process memory.
- When `settings.pii_vault_db_path` is non-empty, vault writes through to a
  sidecar SQLite file at that path AND reads from it on cache miss. This
  lets a paused ticket survive process death (the "kill mid-interrupt +
  resume" durable-execution demo).
- Trade-off: real recipient addresses + redaction token-maps now sit on
  disk in plaintext SQLite. Acceptable for the local docker demo (single
  trust boundary). For prod, encrypt the data dir or move the vault behind
  a KMS-fronted secret store. Documented in `docs/threat_model.md` A1.
- LangSmith traces are NOT affected — only the local sidecar file. The
  audit_log entries still carry only `token_count`, never the values.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ephemeral PII vault — module-level, NOT serialized to state, NOT checkpointed.
# Keyed by ticket_id. Threading lock guards concurrent ticket processing.
# Optional SQLite sidecar (see `_sidecar_*` helpers) persists the same data
# when `PII_VAULT_DB_PATH` is set so paused tickets survive process death.
# ---------------------------------------------------------------------------

_TOKEN_VAULT: dict[str, dict[str, str]] = {}
_ENVELOPE_VAULT: dict[str, str] = {}
_VAULT_LOCK = threading.Lock()

# Lazily-initialised SQLite connection for the persistent sidecar. None when
# the feature is disabled (default). Single connection reused for the process
# lifetime; sqlite3 is configured with check_same_thread=False because the
# graph executes nodes on a worker thread distinct from where pii.py was
# first imported.
_SIDECAR_CONN: sqlite3.Connection | None = None
_SIDECAR_INITED: bool = False


def _sidecar_path() -> str:
    """Resolve the sidecar SQLite path.

    Env var wins so tests can `monkeypatch.setenv(...)` without having to
    rebuild the (cached, import-time) Settings object. Falls back to
    settings.pii_vault_db_path when the env var is unset — production path
    reads it from .env via pydantic-settings.
    """
    env_value = os.environ.get("PII_VAULT_DB_PATH", "")
    if env_value:
        return env_value
    try:
        from src.config import settings

        return settings.pii_vault_db_path
    except Exception:  # noqa: BLE001 — never let config import crash pii ops
        return ""


def _sidecar() -> sqlite3.Connection | None:
    """Return the persistent sidecar connection, opening it on first use.

    Returns None when the feature is disabled (empty path). Callers MUST be
    holding `_VAULT_LOCK` — the global state mutation here is not internally
    synchronised.
    """
    global _SIDECAR_CONN, _SIDECAR_INITED
    if _SIDECAR_INITED:
        return _SIDECAR_CONN

    path = _sidecar_path()
    _SIDECAR_INITED = True
    if not path:
        _SIDECAR_CONN = None
        return None

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pii_vault ("
            "ticket_id TEXT PRIMARY KEY, "
            "envelope_from TEXT, "
            "token_map TEXT"
            ")"
        )
        conn.commit()
        _SIDECAR_CONN = conn
        log.info("PII vault sidecar opened at %s", path)
    except Exception as exc:  # noqa: BLE001 — disk failure must not crash send
        log.warning("PII vault sidecar at %s failed to open: %s — falling back to memory-only", path, exc)
        _SIDECAR_CONN = None
    return _SIDECAR_CONN


def _sidecar_put(ticket_id: str, *, envelope_from: str | None = None, token_map: dict[str, str] | None = None) -> None:
    """Upsert a row in the sidecar. Either field optional → preserves the other.

    No-op when the sidecar is disabled or its open failed. Caller MUST hold
    `_VAULT_LOCK`.
    """
    conn = _sidecar()
    if conn is None:
        return
    try:
        cur = conn.execute("SELECT envelope_from, token_map FROM pii_vault WHERE ticket_id = ?", (ticket_id,))
        existing = cur.fetchone()
        new_env = envelope_from if envelope_from is not None else (existing[0] if existing else None)
        new_tm = json.dumps(token_map) if token_map is not None else (existing[1] if existing else None)
        conn.execute(
            "INSERT INTO pii_vault (ticket_id, envelope_from, token_map) VALUES (?, ?, ?) "
            "ON CONFLICT(ticket_id) DO UPDATE SET envelope_from=excluded.envelope_from, token_map=excluded.token_map",
            (ticket_id, new_env, new_tm),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — sidecar errors must not crash the graph
        log.warning("PII vault sidecar write failed for %s: %s", ticket_id, exc)


def _sidecar_get(ticket_id: str) -> tuple[str | None, dict[str, str]]:
    """Read (envelope_from, token_map) from the sidecar.

    Returns (None, {}) when sidecar is disabled, absent, or read fails.
    Caller MUST hold `_VAULT_LOCK`.
    """
    conn = _sidecar()
    if conn is None:
        return None, {}
    try:
        cur = conn.execute("SELECT envelope_from, token_map FROM pii_vault WHERE ticket_id = ?", (ticket_id,))
        row = cur.fetchone()
        if not row:
            return None, {}
        env = row[0]
        tm: dict[str, str] = json.loads(row[1]) if row[1] else {}
        return env, tm
    except Exception as exc:  # noqa: BLE001
        log.warning("PII vault sidecar read failed for %s: %s", ticket_id, exc)
        return None, {}


def _sidecar_delete(ticket_id: str) -> None:
    """Drop the row for ticket_id from the sidecar. No-op when disabled."""
    conn = _sidecar()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM pii_vault WHERE ticket_id = ?", (ticket_id,))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("PII vault sidecar delete failed for %s: %s", ticket_id, exc)


def _reset_sidecar_for_tests() -> None:
    """Test-only: close and forget the cached sidecar connection so the next
    call re-reads the env. NOT a public API."""
    global _SIDECAR_CONN, _SIDECAR_INITED
    if _SIDECAR_CONN is not None:
        try:
            _SIDECAR_CONN.close()
        except sqlite3.ProgrammingError:
            # Connection was already closed by something else (test teardown
            # ordering edge case). Safe to ignore at this scope.
            pass
    _SIDECAR_CONN = None
    _SIDECAR_INITED = False


def store_token_map(ticket_id: str, token_map: dict[str, str]) -> None:
    """Persist a token_map for ticket_id in the ephemeral vault.

    Also writes through to the persistent sidecar when configured so resume
    after process death can restore PII.
    """
    with _VAULT_LOCK:
        _TOKEN_VAULT[ticket_id] = dict(token_map)
        _sidecar_put(ticket_id, token_map=dict(token_map))


def get_token_map(ticket_id: str) -> dict[str, str]:
    """Retrieve the token_map for ticket_id (empty dict if absent).

    Memory first; falls back to the persistent sidecar when configured. A
    sidecar hit warms the memory cache so subsequent calls are zero-I/O.
    """
    with _VAULT_LOCK:
        if ticket_id in _TOKEN_VAULT:
            return dict(_TOKEN_VAULT[ticket_id])
        _, tm = _sidecar_get(ticket_id)
        if tm:
            _TOKEN_VAULT[ticket_id] = dict(tm)
        return dict(tm)


def store_envelope_from(ticket_id: str, envelope_from: str) -> None:
    """Persist the SMTP envelope-from address for ticket_id.

    This is the address Send Email uses as `to:`, NOT the spoofable
    RFC-822 `From:` header. Captured from `Return-Path` by the email
    listener at ingest time. Writes through to the persistent sidecar
    when configured so the recipient survives process death.
    """
    with _VAULT_LOCK:
        _ENVELOPE_VAULT[ticket_id] = envelope_from
        _sidecar_put(ticket_id, envelope_from=envelope_from)


def get_envelope_from(ticket_id: str) -> str | None:
    """Retrieve the SMTP envelope-from for ticket_id (None if absent).

    Memory first; falls back to the persistent sidecar when configured. A
    sidecar hit warms the memory cache so subsequent calls are zero-I/O.
    """
    with _VAULT_LOCK:
        if ticket_id in _ENVELOPE_VAULT:
            return _ENVELOPE_VAULT[ticket_id]
        env, _ = _sidecar_get(ticket_id)
        if env:
            _ENVELOPE_VAULT[ticket_id] = env
        return env


def resolve_customer_email(state: Any) -> str:
    """Return the trustworthy customer email for *state*.

    Single source of truth for recipient-address recovery — call this from
    every node / agent that needs the customer email after PII redaction
    has run. Three-tier lookup:

      Tier 1 — `envelope_from` for `state["ticket_id"]` in the live or
               persistent vault (the trustworthy SMTP envelope captured at
               ingest; spoof-proof).
      Tier 2 — first `[EMAIL_*]` entry in the live token_map for that
               ticket (matches whatever the spoofable `From:` header said;
               fallback when no envelope was captured, e.g. test fixtures).
      Tier 3 — legacy compatibility — read the `pii_redact` audit_log entry
               for an inline `token_map` dict. Covers checkpoints persisted
               before the 2026-05-09 vault refactor (audit C1/C2 hardening).

    Returns the sentinel `unknown@example.com` if no path resolves. The
    Send Email node MUST treat this sentinel as "fail closed" — sending to
    a stranger is worse than not sending at all (see `src/nodes.py:
    send_email_node`'s `unknown_recipient` skip path).

    Previously duplicated in `src/nodes.py` and `src/agents/researcher.py`;
    DRY'd into `src/pii.py` on 2026-05-24 (audit Medium #15) so drift
    between the two copies can't silently re-introduce the spoofed-
    recipient bug.

    Args:
        state: AgentState (or any dict-like with `ticket_id` and
               `audit_log` keys). Typed as `Any` to avoid a circular
               import of `src.state.AgentState` from this leaf module.
    """
    ticket_id = state.get("ticket_id", "") if hasattr(state, "get") else ""
    if ticket_id:
        envelope = get_envelope_from(ticket_id)
        if envelope:
            return envelope
        tm = get_token_map(ticket_id)
        for token, original in tm.items():
            if token.startswith("[EMAIL_"):
                return original
    audit_log = state.get("audit_log") if hasattr(state, "get") else None
    for entry in reversed(audit_log or []):
        if entry.get("node") == "pii_redact":
            tm_legacy = entry.get("token_map") or {}
            for token, original in tm_legacy.items():
                if token.startswith("[EMAIL_"):
                    return str(original)
    return "unknown@example.com"


def clear_ticket(ticket_id: str) -> None:
    """Remove all PII vault entries for ticket_id. Call at terminal nodes.

    Clears both the in-memory cache and the persistent sidecar (when
    configured) so PII does not linger past the ticket's lifetime.
    """
    with _VAULT_LOCK:
        _TOKEN_VAULT.pop(ticket_id, None)
        _ENVELOPE_VAULT.pop(ticket_id, None)
        _sidecar_delete(ticket_id)

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
