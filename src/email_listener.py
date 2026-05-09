"""IMAP email listener — turns inbound Gmail into LangGraph entry points.

Two modes (architecture.md "Detailed flow"):
  - **Preferred:** IMAP IDLE — Gmail pushes a notification within ~1s of the
    new mail arriving. Implemented via `aioimaplib`.
  - **Fallback:** poll every `IMAP_POLL_INTERVAL_SEC` seconds if IDLE drops
    or is unsupported on the connection.

Each new email becomes an `AgentState` and is fed into the compiled
LangGraph via `graph_runner.start_ticket(...)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import email as stdlib_email
import logging
import re
import uuid
from email.header import decode_header
from typing import Any

import aioimaplib

from src.config import settings
from src.state import initial_state

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------


def _decode_header(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    parts = decode_header(raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace"))
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _extract_body(msg: stdlib_email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


_ENVELOPE_RE = re.compile(r"<([^>]+)>|([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def _extract_envelope_from(msg: stdlib_email.message.Message) -> str:
    """Return the SMTP envelope-from address from `Return-Path`.

    Gmail (and most MTAs) populate `Return-Path` with the actual SMTP
    envelope-from. This is the trustworthy sender — the RFC-822 `From:`
    header is content the sender controls and is spoofable.

    Falls back to the empty string if Return-Path is missing or malformed,
    in which case the caller should treat the recipient as unknown.
    """
    raw = msg.get("Return-Path") or msg.get("X-Original-Sender") or ""
    if not raw:
        return ""
    match = _ENVELOPE_RE.search(raw)
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def parse_email_to_ticket(raw_bytes: bytes) -> dict[str, Any]:
    """Parse raw RFC-822 bytes into a ticket dict ready to seed AgentState.

    `envelope_from` is the trustworthy recipient address. `from` is the
    spoofable header — kept for LLM context only, NEVER used as recipient.
    """
    msg = stdlib_email.message_from_bytes(raw_bytes)
    return {
        "envelope_from": _extract_envelope_from(msg),
        "from": _decode_header(msg.get("From")),
        "subject": _decode_header(msg.get("Subject")),
        "body": _extract_body(msg),
        "email_thread_id": msg.get("Message-ID", ""),
    }


def ticket_to_initial_state(ticket: dict[str, Any]) -> Any:
    """Build the initial AgentState from a parsed ticket dict.

    Stores envelope_from in the ephemeral PII vault (NOT in audit_log)
    so Send Email can retrieve the trustworthy recipient address without
    leaking it to LangSmith / SQLite checkpoints.
    """
    from src.pii import store_envelope_from

    ticket_id = "ticket-" + uuid.uuid4().hex[:12]
    envelope = ticket.get("envelope_from", "")
    if envelope:
        store_envelope_from(ticket_id, envelope)

    # customer_message: the LLM sees the spoofable From for context, but
    # the agent NEVER uses it as the recipient — Send Email reads the
    # envelope-from from the vault.
    return ticket_id, initial_state(
        ticket_id=ticket_id,
        customer_message=f"From: {ticket['from']}\nSubject: {ticket['subject']}\n\n{ticket['body']}",
        email_thread_id=ticket["email_thread_id"],
        send_idempotency_key="idem-" + uuid.uuid4().hex,
    )


# ---------------------------------------------------------------------------
# IMAP loop — IDLE first, polls as fallback.
# ---------------------------------------------------------------------------


async def listen_forever(on_ticket: Any) -> None:  # on_ticket: async callable(ticket_id, state)
    """Main loop. `on_ticket` receives every new email as (ticket_id, AgentState)."""
    settings.require_secrets("gmail_user", "gmail_app_password")

    while True:
        try:
            await _idle_loop(on_ticket)
        except Exception as exc:  # broad: connection errors, network blips
            log.warning("IMAP IDLE failed (%s) — falling back to poll", exc)
            try:
                await _poll_loop(on_ticket)
            except Exception:
                log.exception("Poll loop crashed; sleeping before retry")
                await asyncio.sleep(settings.imap_poll_interval_sec)


async def _connect() -> aioimaplib.IMAP4_SSL:
    client = aioimaplib.IMAP4_SSL(host=settings.gmail_imap_host)
    await client.wait_hello_from_server()
    await client.login(settings.gmail_user, settings.gmail_app_password)
    await client.select("INBOX")
    return client


async def _idle_loop(on_ticket: Any) -> None:
    client = await _connect()
    try:
        # Fetch any UNSEEN already in the mailbox first.
        await _fetch_unseen(client, on_ticket)

        while True:
            idle_task = await client.idle_start(timeout=29 * 60)  # < Gmail's 30min ceiling
            await client.wait_server_push()
            client.idle_done()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(idle_task, timeout=10)
            await _fetch_unseen(client, on_ticket)
    finally:
        with contextlib.suppress(Exception):
            await client.logout()


async def _poll_loop(on_ticket: Any) -> None:
    while True:
        client = await _connect()
        try:
            await _fetch_unseen(client, on_ticket)
        finally:
            with contextlib.suppress(Exception):
                await client.logout()
        await asyncio.sleep(settings.imap_poll_interval_sec)


async def _fetch_unseen(client: aioimaplib.IMAP4_SSL, on_ticket: Any) -> None:
    typ, data = await client.search("UNSEEN")
    if typ != "OK" or not data or not data[0]:
        return
    ids = data[0].split()
    for msg_id in ids:
        typ, msg_data = await client.fetch(msg_id.decode(), "(RFC822)")
        if typ != "OK" or not msg_data:
            continue
        # aioimaplib returns a list with the body bytes at index 1
        for item in msg_data:
            if isinstance(item, (bytes, bytearray)) and len(item) > 100:
                ticket = parse_email_to_ticket(bytes(item))
                ticket_id, state = ticket_to_initial_state(ticket)
                log.info("Inbound email -> ticket %s", ticket_id)
                await on_ticket(ticket_id, state)
                break
        # Mark as Seen so we don't re-process on reconnect.
        await client.store(msg_id.decode(), "+FLAGS", "(\\Seen)")
