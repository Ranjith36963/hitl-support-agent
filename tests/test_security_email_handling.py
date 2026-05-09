"""Regression tests for audit findings C1 + C2 + H3.

C1 — From-header spoofing: bot must reply to the SMTP envelope-from
     (Return-Path), NOT the spoofable RFC-822 `From:` header. Slack
     approval blocks must show the recipient so a human can catch
     mismatches before approving.

C2 — PII in audit_log: token_map (real emails / phones / CCs) must not
     appear in audit_log entries. audit_log is checkpointed to SQLite
     and emitted to LangSmith, leaking PII to a third-party SaaS and
     an on-disk persistence layer.

H3 — restore() token-prefix collision: with 10+ distinct emails,
     `[EMAIL_1]` must not be substituted before `[EMAIL_10]` (would leave
     `0]` suffix in place of the longer token).
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from src.email_listener import (
    _extract_envelope_from,
    parse_email_to_ticket,
    ticket_to_initial_state,
)
from src.nodes import _build_approval_blocks, _customer_email_from_audit, pii_redact_node
from src.pii import (
    clear_ticket,
    get_envelope_from,
    get_token_map,
    redact,
    restore,
    store_envelope_from,
)

# ---------------------------------------------------------------------------
# C1 — envelope-from is the trustworthy recipient, From: is spoofable
# ---------------------------------------------------------------------------


def _build_raw_email(envelope: str, header_from: str, subject: str, body: str) -> bytes:
    """Build raw RFC-822 bytes with a Return-Path that may differ from From:."""
    msg = EmailMessage()
    msg["Return-Path"] = f"<{envelope}>"
    msg["From"] = header_from
    msg["Subject"] = subject
    msg["Message-ID"] = "<test-msg-001@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def test_envelope_from_extracted_from_return_path() -> None:
    """Plain Return-Path with angle brackets resolves cleanly."""
    raw = _build_raw_email(
        envelope="real@sender.com",
        header_from="real@sender.com",
        subject="Test",
        body="Hi",
    )
    parsed = parse_email_to_ticket(raw)
    assert parsed["envelope_from"] == "real@sender.com"


def test_envelope_from_distinguishes_spoofed_from_header() -> None:
    """The audit-finding C1 attack: header From says victim, envelope says attacker.

    parse_email_to_ticket must capture envelope_from = attacker (real sender)
    while still surfacing the spoofed From in `from` for LLM context.
    """
    raw = _build_raw_email(
        envelope="attacker@evil.com",
        header_from="victim@target.com",
        subject="Hi",
        body="please help",
    )
    parsed = parse_email_to_ticket(raw)
    # Envelope captures the trustworthy address.
    assert parsed["envelope_from"] == "attacker@evil.com"
    # From: header is preserved for LLM context but is NOT the recipient.
    assert "victim@target.com" in parsed["from"]


def test_ticket_to_initial_state_stores_envelope_in_vault() -> None:
    """ticket_to_initial_state must put envelope_from into the PII vault,
    NOT into customer_message or audit_log where it would be checkpointed."""
    raw = _build_raw_email(
        envelope="alice@gmail.com",
        header_from="alice@gmail.com",
        subject="Refund",
        body="please",
    )
    ticket = parse_email_to_ticket(raw)
    ticket_id, state = ticket_to_initial_state(ticket)

    try:
        # Vault populated.
        assert get_envelope_from(ticket_id) == "alice@gmail.com"
        # NOT in audit_log (it's an empty list at this point — no entries yet).
        assert state["audit_log"] == []
    finally:
        clear_ticket(ticket_id)


def test_customer_email_prefers_envelope_over_token_map() -> None:
    """Even if the token_map (from From: header redaction) names a different
    address, envelope-from in the vault wins. This is the load-bearing
    defense against the C1 spoofing attack."""
    ticket_id = "TKT-SPOOF-001"
    store_envelope_from(ticket_id, "attacker@evil.com")

    state = {
        "ticket_id": ticket_id,
        # Imagine PII redact tokenized the spoofed From: header into [EMAIL_1].
        "audit_log": [
            {
                "node": "pii_redact",
                "token_map": {"[EMAIL_1]": "victim@target.com"},
            }
        ],
    }
    try:
        recipient = _customer_email_from_audit(state)
        # The trustworthy envelope wins — NOT the spoofed From: address.
        assert recipient == "attacker@evil.com"
    finally:
        clear_ticket(ticket_id)


def test_approval_blocks_show_recipient_line() -> None:
    """Slack approval blocks MUST show 'Reply will go to ...' so humans
    can catch a spoofed-recipient ticket before approving."""
    ticket_id = "TKT-VISIBLE-001"
    store_envelope_from(ticket_id, "alice@gmail.com")

    state = {
        "ticket_id": ticket_id,
        "intent": "refund",
        "intent_confidence": 0.9,
        "draft_confidence": 0.85,
        "sentiment": "neutral",
        "risk_flags": ["refund"],
        "policy_matches": ["ACME 4.2.1"],
        "customer_tier": "Standard",
        "final_draft": "Refund issued.",
        "original_draft": "Refund issued.",
    }
    try:
        blocks = _build_approval_blocks(state, kb_quote="ACME 4.2.1")
        # Find any block that mentions "Reply will go to" — the recipient line.
        has_recipient_line = any(
            "Reply will go to" in str(block)
            for block in blocks
        )
        assert has_recipient_line, (
            f"Slack blocks lack the recipient line. Blocks: {blocks}"
        )
        # And the actual address must appear in the blocks.
        assert any("alice@gmail.com" in str(block) for block in blocks)
    finally:
        clear_ticket(ticket_id)


# ---------------------------------------------------------------------------
# C2 — PII never written into audit_log
# ---------------------------------------------------------------------------


def test_pii_redact_node_does_not_leak_token_map_to_audit_log() -> None:
    """audit_log entries must record token_count only — never the map values.

    Storing real emails / phones / CCs in audit_log leaks PII into the
    SQLite checkpoint AND any LangSmith trace that serializes state.
    """
    ticket_id = "TKT-AUDIT-001"
    state = {
        "ticket_id": ticket_id,
        "customer_message": "From: alice@example.com\nMy phone is 555-867-5309.",
        "audit_log": [],
    }
    try:
        result = pii_redact_node(state)
        last = result["audit_log"][-1]
        # Token COUNT is fine for observability.
        assert last.get("token_count", 0) >= 1
        # Token MAP must NOT be present.
        assert "token_map" not in last, (
            f"token_map leaked into audit_log: {last!r}"
        )
        # Real emails / phones must not appear in any audit-log entry value.
        joined = str(result["audit_log"])
        assert "alice@example.com" not in joined
        assert "555-867-5309" not in joined
        # Vault has the data instead.
        tm = get_token_map(ticket_id)
        assert "alice@example.com" in tm.values()
    finally:
        clear_ticket(ticket_id)


# ---------------------------------------------------------------------------
# H3 — restore() handles 10+ tokens without prefix collision
# ---------------------------------------------------------------------------


def test_restore_no_prefix_collision_with_10_emails() -> None:
    """[EMAIL_10] must be restored before [EMAIL_1] — otherwise the shorter
    token's substitution leaves a `0]` suffix where [EMAIL_10] should be."""
    text = " ".join(f"a{i}@example.com" for i in range(1, 12))  # 11 distinct emails
    redacted, token_map = redact(text)
    assert len(token_map) == 11
    # Confirm the bug-trigger condition exists: tokens [EMAIL_1] AND [EMAIL_10] both present.
    assert "[EMAIL_1]" in token_map
    assert "[EMAIL_10]" in token_map

    restored = restore(redacted, token_map)
    # Round-trip identity.
    assert restored == text
    # Specifically: the [EMAIL_10] address must appear intact, with no `0]` suffix.
    assert "a10@example.com" in restored
    assert "0]" not in restored  # prefix-collision smell


def test_restore_cc_and_phone_token_lengths_also_safe() -> None:
    """Same prefix-collision risk applies to CC_1 vs CC_10 and PHONE_1 vs PHONE_10."""
    # Build text with 11 distinct phone numbers
    phones = [f"555-{i:03d}-{i*111:04d}" for i in range(11)]
    text = " ".join(phones)
    redacted, token_map = redact(text)
    assert len(token_map) == 11
    restored = restore(redacted, token_map)
    assert restored == text


# ---------------------------------------------------------------------------
# Cross-cutting: vault helpers
# ---------------------------------------------------------------------------


def test_clear_ticket_removes_both_vault_entries() -> None:
    """Terminal nodes call clear_ticket — must wipe both token_map and envelope."""
    ticket_id = "TKT-CLEAR-001"
    store_envelope_from(ticket_id, "alice@gmail.com")

    # Manually populate token_map slot via the official helper.
    from src.pii import store_token_map
    store_token_map(ticket_id, {"[EMAIL_1]": "alice@gmail.com"})

    assert get_envelope_from(ticket_id) == "alice@gmail.com"
    assert get_token_map(ticket_id)

    clear_ticket(ticket_id)

    assert get_envelope_from(ticket_id) is None
    assert get_token_map(ticket_id) == {}


def test_extract_envelope_from_handles_missing_return_path() -> None:
    """When Return-Path is absent (e.g., test fixtures), envelope is empty
    string — caller must treat as 'recipient unknown'."""
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Hi"
    msg.set_content("hi")
    assert _extract_envelope_from(msg) == ""


def test_extract_envelope_from_bare_address_no_brackets() -> None:
    """Some MTAs write Return-Path as bare 'addr@x.com' without angle brackets.
    Our regex must still extract it."""
    msg = EmailMessage()
    msg["Return-Path"] = "alice@gmail.com"
    msg["From"] = "alice@gmail.com"
    msg["Subject"] = "Hi"
    msg.set_content("hi")
    assert _extract_envelope_from(msg) == "alice@gmail.com"


# ---------------------------------------------------------------------------
# Send Email fail-closed on unknown recipient (ties to C1 — never SMTP-send
# to unknown@example.com sentinel under any path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_node_fails_closed_on_unknown_recipient() -> None:
    """If neither envelope nor audit_log resolves a recipient, Send Email
    must NOT call SMTP — refusing to send beats sending to a stranger."""
    from src.nodes import send_email_node

    state = {
        "ticket_id": "TKT-NOENV-001",
        "final_draft": "Hi there",
        "send_idempotency_key": "idem-x",
        "email_thread_id": "<orig@x.com>",
        "audit_log": [],  # No pii_redact entry → no token_map → no recipient.
    }
    # Ensure no vault entry.
    clear_ticket("TKT-NOENV-001")

    result = await send_email_node(state)
    # Must terminate to manual queue, NOT call SMTP.
    assert result["send_status"] == "failed_manual"
    assert result["final_state"] == "failed_manual"
    last_audit = result["audit_log"][-1]
    assert last_audit.get("skipped") == "unknown_recipient"
