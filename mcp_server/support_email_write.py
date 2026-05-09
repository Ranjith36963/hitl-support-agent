"""MCP EMAIL WRITE Server — capability-isolated to Gmail SMTP only.

Implements spec.md §8 "MCP EMAIL WRITE Server":
  - send_email(to, subject, body, in_reply_to, idempotency_key, references)
    → sends via Gmail SMTP; app-layer idempotent.

Critical invariants (spec §6 Send Email, CLAUDE.md):
  1. App-layer idempotency: before SMTP call, check mcp_server/.email_idem.db for
     the idempotency_key. If already sent, return cached sent_message_id.
  2. Three threading headers (ALL required for Gmail threading):
       In-Reply-To: <original_message_id>
       References: <original_message_id>
       Subject: Re: <original subject>
  3. ZERO Slack code. Cannot post to Slack under any circumstance.

Env vars read (from .env.example):
  GMAIL_USER           — sender address (e.g. support@yourcompany.com)
  GMAIL_APP_PASSWORD   — Gmail App Password (not the account password)
  GMAIL_SMTP_HOST      — defaults to smtp.gmail.com
  GMAIL_SMTP_PORT      — defaults to 587

Run standalone (stdio):
    python mcp_server/support_email_write.py
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import aiosqlite
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# ---------------------------------------------------------------------------
# Config — read from env, never hardcoded
# ---------------------------------------------------------------------------
GMAIL_USER: str = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD: str = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_SMTP_HOST: str = os.environ.get("GMAIL_SMTP_HOST", "smtp.gmail.com")
GMAIL_SMTP_PORT: int = int(os.environ.get("GMAIL_SMTP_PORT", "587"))

# ---------------------------------------------------------------------------
# Idempotency DB — tiny SQLite table keyed by idempotency_key
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_IDEM_DB_PATH = pathlib.Path(__file__).parent / ".email_idem.db"


async def _ensure_idem_schema() -> None:
    """Create idempotency table if it doesn't exist yet."""
    async with aiosqlite.connect(_IDEM_DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_emails (
                idempotency_key TEXT PRIMARY KEY,
                sent_message_id TEXT NOT NULL,
                sent_at         TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _lookup_sent(idempotency_key: str) -> str | None:
    """Return sent_message_id if already sent, else None."""
    async with aiosqlite.connect(_IDEM_DB_PATH) as db:
        async with db.execute(
            "SELECT sent_message_id FROM sent_emails WHERE idempotency_key = ?",
            (idempotency_key,),
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def _record_sent(idempotency_key: str, sent_message_id: str) -> None:
    """Persist sent_message_id for this idempotency_key."""
    async with aiosqlite.connect(_IDEM_DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO sent_emails (idempotency_key, sent_message_id, sent_at)
            VALUES (?, ?, ?)
            """,
            (idempotency_key, sent_message_id, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# SMTP helper — uses aiosmtplib (async-first per CLAUDE.md)
# ---------------------------------------------------------------------------

def _build_message(
    to: str,
    subject: str,
    body: str,
    in_reply_to: str,
    references: str,
    message_id: str,
) -> EmailMessage:
    """Assemble an RFC-822 EmailMessage with the three required threading headers."""
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = to
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = f"<{message_id}>"

    # Ensure Subject starts with "Re: " for Gmail threading
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    msg["Subject"] = subject

    # Three threading headers — ALL required (spec §6 + CLAUDE.md invariants)
    if in_reply_to:
        # Normalise: wrap in angle brackets if missing
        if not in_reply_to.startswith("<"):
            in_reply_to = f"<{in_reply_to}>"
        msg["In-Reply-To"] = in_reply_to

        # References = existing references chain + in_reply_to
        ref_chain = references.strip() if references else ""
        if in_reply_to not in ref_chain:
            ref_chain = (ref_chain + " " + in_reply_to).strip()
        msg["References"] = ref_chain

    msg.set_content(body)
    return msg


async def _smtp_send(msg: EmailMessage) -> str:
    """Send *msg* via aiosmtplib and return the sent Message-ID."""
    import aiosmtplib  # local import — zero Slack code in module namespace

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER and GMAIL_APP_PASSWORD must be set in environment. "
            "See .env.example."
        )

    await aiosmtplib.send(
        msg,
        hostname=GMAIL_SMTP_HOST,
        port=GMAIL_SMTP_PORT,
        username=GMAIL_USER,
        password=GMAIL_APP_PASSWORD,
        start_tls=True,
    )
    # Message-ID was set before send; return it as the stable sent_message_id
    return msg["Message-ID"]


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "support-email-write",
    instructions=(
        "EMAIL WRITE server. Sends customer reply emails via Gmail SMTP. "
        "App-layer idempotent — will not double-send for the same idempotency_key. "
        "Has no ability to read CRM data or post to Slack."
    ),
)


@mcp.tool()
async def send_email(
    to: str,
    subject: str,
    body: str,
    in_reply_to: str,
    idempotency_key: str,
    references: str = "",
) -> dict[str, Any]:
    """Send a reply email via Gmail SMTP, idempotently.

    Args:
        to:               Recipient email address.
        subject:          Email subject. If it doesn't start with 'Re: ', the
                          server prepends it (required for Gmail threading).
        body:             Plain-text body of the reply.
        in_reply_to:      RFC-822 Message-ID of the customer's original email
                          (used for In-Reply-To AND References headers).
        idempotency_key:  Stable key set once at ticket entry. If this key has
                          been sent before, returns the cached sent_message_id
                          without calling SMTP again.
        references:       Optional existing References header chain to extend.

    Returns: dict with keys:
        sent_message_id (str)  — RFC-822 Message-ID of the sent email.
        was_duplicate (bool)   — True if idempotency guard fired (no SMTP call).
        status (str)           — "sent" | "duplicate_skipped".
    """
    await _ensure_idem_schema()

    # --- App-layer idempotency check ---
    cached = await _lookup_sent(idempotency_key)
    if cached:
        logger.info(
            "send_email: duplicate detected for key=%s; returning cached id=%s",
            idempotency_key,
            cached,
        )
        return {
            "sent_message_id": cached,
            "was_duplicate": True,
            "status": "duplicate_skipped",
        }

    # --- Build message with threading headers ---
    new_message_id = f"{uuid.uuid4()}@{GMAIL_SMTP_HOST}"
    msg = _build_message(
        to=to,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        message_id=new_message_id,
    )

    # --- Send via SMTP ---
    sent_id = await _smtp_send(msg)
    logger.info("send_email: sent to=%s message_id=%s", to, sent_id)

    # --- Persist idempotency record ---
    await _record_sent(idempotency_key, sent_id)

    return {
        "sent_message_id": sent_id,
        "was_duplicate": False,
        "status": "sent",
    }


if __name__ == "__main__":
    # _ensure_idem_schema() runs on first send_email call; no need to pre-run here.
    mcp.run(transport="stdio")
