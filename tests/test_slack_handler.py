"""Slack webhook signature verification — security boundary test.

Spec source: spec.md §6 Slack Action Handler + architecture.md
"Slack webhook signatures are verified, not trusted."

Failure modes that MUST reject:
  - Wrong signature
  - Timestamp older than 5 minutes (replay attack)
  - Missing signing secret
  - Malformed timestamp

Pass mode:
  - Correct HMAC-SHA256("v0:{ts}:{body}") within 5-minute window
"""

from __future__ import annotations

import hashlib
import hmac
import time

from src.slack_handler import verify_slack_signature

SECRET = "8f742231b9a1c0e9ca1e1c2c3d4e5f60"  # test fixture, not a real secret


def _sig(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    base = f"v0:{timestamp}:".encode() + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    body = b"payload=%7B%22action%22%3A%22approve%22%7D"
    ts = int(time.time())
    assert verify_slack_signature(
        body=body,
        timestamp=str(ts),
        signature=_sig(body, ts),
        signing_secret=SECRET,
    )


def test_wrong_signature_rejected() -> None:
    body = b"payload=anything"
    ts = int(time.time())
    assert not verify_slack_signature(
        body=body,
        timestamp=str(ts),
        signature="v0=" + "0" * 64,  # not the real digest
        signing_secret=SECRET,
    )


def test_timestamp_too_old_rejected() -> None:
    """Replay defense: anything older than 5 minutes is rejected."""
    body = b"payload=anything"
    ts = int(time.time()) - (60 * 6)  # 6 minutes ago
    assert not verify_slack_signature(
        body=body,
        timestamp=str(ts),
        signature=_sig(body, ts),
        signing_secret=SECRET,
    )


def test_timestamp_too_far_in_future_rejected() -> None:
    body = b"payload=anything"
    ts = int(time.time()) + (60 * 6)  # 6 minutes ahead
    assert not verify_slack_signature(
        body=body,
        timestamp=str(ts),
        signature=_sig(body, ts),
        signing_secret=SECRET,
    )


def test_missing_signing_secret_rejected() -> None:
    body = b"payload"
    ts = int(time.time())
    assert not verify_slack_signature(
        body=body,
        timestamp=str(ts),
        signature=_sig(body, ts, "wrong"),
        signing_secret="",
    )


def test_malformed_timestamp_rejected() -> None:
    body = b"payload"
    assert not verify_slack_signature(
        body=body,
        timestamp="not-a-number",
        signature="v0=anything",
        signing_secret=SECRET,
    )


def test_body_tampering_detected() -> None:
    """Body change → signature recompute fails. Anti-tamper proof."""
    original = b"payload=%7B%22action%22%3A%22approve%22%7D"
    tampered = b"payload=%7B%22action%22%3A%22reject%22%7D"
    ts = int(time.time())
    sig_for_original = _sig(original, ts)
    assert not verify_slack_signature(
        body=tampered,
        timestamp=str(ts),
        signature=sig_for_original,
        signing_secret=SECRET,
    )
