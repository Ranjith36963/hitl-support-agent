"""Regression tests for audit finding H2 — atomic idempotency.

The pre-fix pattern was:
    cached = await _lookup_sent(key)        # SELECT
    if cached: return cached
    sent_id = await _smtp_send(msg)         # SMTP
    await _record_sent(key, sent_id)        # INSERT OR IGNORE

Two concurrent calls with the same key both pass the lookup (no row),
both call SMTP, and the second `_record_sent` silently no-ops via
INSERT OR IGNORE. Customer gets duplicate replies.

Fix: atomic reserve via single `INSERT OR IGNORE` with a sentinel value.
Only one caller wins (`cursor.rowcount == 1`). The loser sees the row
already exists and refuses to call SMTP. SMTP runs outside the SQLite
write lock; success path UPDATEs the sentinel to the real Message-ID.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Add mcp_server/ to sys.path so we can import the module directly for unit tests.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp_server import support_email_write as ew


@pytest.fixture
def fresh_idem_db(tmp_path, monkeypatch):
    """Point the idempotency DB at a tmp file so each test starts clean."""
    db_path = tmp_path / "test_idem.db"
    monkeypatch.setattr(ew, "_IDEM_DB_PATH", db_path)
    return db_path


@pytest.mark.asyncio
async def test_try_reserve_first_caller_wins(fresh_idem_db) -> None:
    """First reservation returns True (caller proceeds)."""
    await ew._ensure_idem_schema()
    won = await ew._try_reserve("idem-001")
    assert won is True


@pytest.mark.asyncio
async def test_try_reserve_second_caller_loses(fresh_idem_db) -> None:
    """Second reservation with same key returns False (caller MUST not SMTP)."""
    await ew._ensure_idem_schema()
    first = await ew._try_reserve("idem-002")
    second = await ew._try_reserve("idem-002")
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_try_reserve_concurrent_callers_only_one_wins(fresh_idem_db) -> None:
    """20 concurrent _try_reserve calls with the same key → exactly 1 winner.

    This is the race the audit found. Pre-fix code would let multiple
    callers pass the lookup, hit SMTP, and `_record_sent` silently
    no-op the duplicates. Post-fix: single atomic INSERT OR IGNORE
    means only one rowcount == 1.
    """
    await ew._ensure_idem_schema()
    results = await asyncio.gather(
        *(ew._try_reserve("idem-race-001") for _ in range(20))
    )
    winners = [r for r in results if r is True]
    losers = [r for r in results if r is False]
    assert len(winners) == 1, (
        f"Race produced {len(winners)} winners — atomicity broken."
    )
    assert len(losers) == 19


@pytest.mark.asyncio
async def test_send_email_duplicate_skip_after_completion(fresh_idem_db) -> None:
    """Second send_email call for completed key returns the cached id, no SMTP."""
    fake_smtp = AsyncMock(return_value="<sent-msg-id-abc@x.com>")
    monkey_email_user = "test@gmail.com"
    monkey_email_pw = "test-pw"
    with (
        patch.object(ew, "_smtp_send", fake_smtp),
        patch.object(ew, "GMAIL_USER", monkey_email_user),
        patch.object(ew, "GMAIL_APP_PASSWORD", monkey_email_pw),
    ):
        # FastMCP's @mcp.tool() registers but doesn't wrap — call directly.
        send_fn = ew.send_email

        # First send — SMTP fires once.
        r1 = await send_fn(
            to="customer@example.com",
            subject="Test",
            body="Hi",
            in_reply_to="<orig@x.com>",
            idempotency_key="idem-dup-001",
        )
        assert r1["status"] == "sent"
        assert r1["was_duplicate"] is False
        assert fake_smtp.await_count == 1

        # Second send with same key — SMTP must NOT fire.
        r2 = await send_fn(
            to="customer@example.com",
            subject="Test",
            body="Hi",
            in_reply_to="<orig@x.com>",
            idempotency_key="idem-dup-001",
        )
        assert r2["status"] == "duplicate_skipped"
        assert r2["was_duplicate"] is True
        assert fake_smtp.await_count == 1, (
            "SMTP fired on the duplicate — idempotency broken!"
        )


@pytest.mark.asyncio
async def test_send_email_concurrent_callers_smtp_fires_exactly_once(
    fresh_idem_db,
) -> None:
    """Two concurrent send_email coroutines with same key → SMTP called exactly once.

    The headline test that proves the fix. Before the fix, both calls would
    pass the lookup and both would call SMTP. After the fix, only one wins
    the reservation.
    """
    smtp_calls = 0

    async def slow_smtp(msg: Any) -> str:
        nonlocal smtp_calls
        smtp_calls += 1
        # Simulate SMTP latency so concurrent calls actually overlap.
        await asyncio.sleep(0.05)
        return f"<sent-{smtp_calls}@x.com>"

    with (
        patch.object(ew, "_smtp_send", slow_smtp),
        patch.object(ew, "GMAIL_USER", "test@gmail.com"),
        patch.object(ew, "GMAIL_APP_PASSWORD", "pw"),
    ):
        send_fn = ew.send_email

        results = await asyncio.gather(
            send_fn(
                to="customer@example.com",
                subject="Test",
                body="Hi",
                in_reply_to="<o@x.com>",
                idempotency_key="idem-concurrent-001",
            ),
            send_fn(
                to="customer@example.com",
                subject="Test",
                body="Hi",
                in_reply_to="<o@x.com>",
                idempotency_key="idem-concurrent-001",
            ),
        )

    # SMTP must have been called EXACTLY once across the two concurrent calls.
    assert smtp_calls == 1, (
        f"SMTP fired {smtp_calls} times on concurrent same-key sends — "
        f"the audit-found race is back."
    )

    # Exactly one result is `status=sent`; the other is duplicate or
    # concurrent_in_flight (both are non-SMTP outcomes).
    sent_count = sum(1 for r in results if r["status"] == "sent")
    non_sent = [r["status"] for r in results if r["status"] != "sent"]
    assert sent_count == 1, f"Expected 1 'sent', got {sent_count}: {results}"
    assert len(non_sent) == 1
    assert non_sent[0] in ("duplicate_skipped", "concurrent_in_flight"), (
        f"Loser status was unexpectedly {non_sent[0]!r}"
    )


@pytest.mark.asyncio
async def test_smtp_failure_releases_reservation(fresh_idem_db) -> None:
    """If SMTP raises, the reservation row must be released so the
    LangGraph retry path can re-attempt with the same idempotency_key."""
    fake_smtp_fail = AsyncMock(side_effect=ConnectionError("SMTP down"))

    with (
        patch.object(ew, "_smtp_send", fake_smtp_fail),
        patch.object(ew, "GMAIL_USER", "test@gmail.com"),
        patch.object(ew, "GMAIL_APP_PASSWORD", "pw"),
    ):
        send_fn = ew.send_email

        with pytest.raises(ConnectionError):
            await send_fn(
                to="customer@example.com",
                subject="Test",
                body="Hi",
                in_reply_to="<o@x.com>",
                idempotency_key="idem-fail-001",
            )

    # Now retry with the same key — must succeed (reservation was released).
    fake_smtp_ok = AsyncMock(return_value="<sent-after-retry@x.com>")
    with (
        patch.object(ew, "_smtp_send", fake_smtp_ok),
        patch.object(ew, "GMAIL_USER", "test@gmail.com"),
        patch.object(ew, "GMAIL_APP_PASSWORD", "pw"),
    ):
        send_fn = ew.send_email
        retry_result = await send_fn(
            to="customer@example.com",
            subject="Test",
            body="Hi",
            in_reply_to="<o@x.com>",
            idempotency_key="idem-fail-001",
        )

    assert retry_result["status"] == "sent", (
        "Retry after SMTP failure was blocked — reservation was not released."
    )
    assert retry_result["was_duplicate"] is False
