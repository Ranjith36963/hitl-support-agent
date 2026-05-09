"""MCP SLACK WRITE Server — capability-isolated to Slack API only.

Implements spec.md §8 "MCP SLACK WRITE Server":
  - post_approval_request(channel, blocks) → posts Block Kit message;
      returns slack_message_ts (saved to AgentState for update + resume)
  - update_message(channel, ts, blocks) → updates the same message in-place
      (used for "Approved by @x", delta panels, "📤 Sent", final queue status)
  - open_edit_modal(trigger_id, ticket_id, current_draft) → opens Slack
      views.open modal for the Edit flow (human edits draft inline in Slack)

Connected to: Slack Notification node, Summarize Changes, Send Email
  (post-send confirmation), Manual Queue (final status).
ZERO email code. Cannot send email under any circumstance.

Env vars read (from .env.example):
  SLACK_BOT_TOKEN   — xoxb-... from app's "OAuth & Permissions" page

Run standalone (stdio):
    python mcp_server/support_slack_write.py
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# ---------------------------------------------------------------------------
# Config — read from env, never hardcoded
# ---------------------------------------------------------------------------
SLACK_BOT_TOKEN: str = os.environ.get("SLACK_BOT_TOKEN", "")


def _client() -> AsyncWebClient:
    """Create a fresh AsyncWebClient. Called per request to stay stateless."""
    if not SLACK_BOT_TOKEN:
        raise RuntimeError(
            "SLACK_BOT_TOKEN must be set in environment. See .env.example."
        )
    return AsyncWebClient(token=SLACK_BOT_TOKEN)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "support-slack-write",
    instructions=(
        "SLACK WRITE server. Posts approval requests, updates existing messages, "
        "and opens edit modals — all via Slack API. "
        "Has no ability to send email or read CRM/KB data."
    ),
)


@mcp.tool()
async def post_approval_request(
    channel: str,
    blocks: list[dict[str, Any]],
    text: str = "New support ticket requires approval",
) -> dict[str, Any]:
    """Post a Block Kit approval message to *channel*.

    Args:
        channel:  Slack channel name or ID (e.g. "#support-refunds").
        blocks:   List of Slack Block Kit block dicts. The caller (nodes.py
                  Slack Notification node) is responsible for assembling the
                  full Block Kit payload including the customer card, "Why I
                  paused" panel, draft section, and Approve/Edit/Reject buttons.
        text:     Fallback plain-text summary (used by screen readers and
                  notifications).

    Returns: dict with keys:
        slack_message_ts (str) — Slack message timestamp; used to update the
                                 message in place and to resume on the correct
                                 message after a server restart.
        channel (str)          — Resolved channel ID returned by Slack.
        ok (bool)              — True on success.
    """
    client = _client()
    try:
        resp = await client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=text,
        )
    except SlackApiError as exc:
        logger.error("post_approval_request failed: %s", exc.response)
        raise

    ts: str = resp["ts"]
    resolved_channel: str = resp["channel"]
    logger.info(
        "post_approval_request: posted to channel=%s ts=%s", resolved_channel, ts
    )
    return {
        "slack_message_ts": ts,
        "channel": resolved_channel,
        "ok": True,
    }


@mcp.tool()
async def update_message(
    channel: str,
    ts: str,
    blocks: list[dict[str, Any]],
    text: str = "Message updated",
) -> dict[str, Any]:
    """Update an existing Slack message identified by *channel* + *ts*.

    Used for:
      - "✅ Approved by @user · 22 sec" after human approves
      - "✏️ Edited & approved by @user" after edit+approve
      - "↩️ Rejected by @user — redrafting (2/3)" after rejection
      - Delta panel posted as a thread update when context_hash changed
      - "📤 Reply sent to <customer>" after successful SMTP send
      - "🚦 3 rejections — manual queue" on rejection limit reached
      - "⏰ Still pending — paging backup" for SLA warnings

    Args:
        channel:  Channel name or ID that contains the original message.
        ts:       slack_message_ts of the message to update.
        blocks:   New Block Kit block list to replace the message content.
        text:     Fallback plain-text (accessibility + notification preview).

    Returns: dict with keys:
        ok (bool), ts (str), channel (str).
    """
    client = _client()
    try:
        resp = await client.chat_update(
            channel=channel,
            ts=ts,
            blocks=blocks,
            text=text,
        )
    except SlackApiError as exc:
        logger.error("update_message failed: channel=%s ts=%s error=%s", channel, ts, exc.response)
        raise

    logger.info("update_message: updated channel=%s ts=%s", channel, ts)
    return {
        "ok": True,
        "ts": resp.get("ts", ts),
        "channel": resp.get("channel", channel),
    }


@mcp.tool()
async def open_edit_modal(
    trigger_id: str,
    ticket_id: str,
    current_draft: str,
) -> dict[str, Any]:
    """Open a Slack views.open modal for the human to edit the draft inline.

    Called by the FastAPI Slack action handler when the human clicks "Edit".
    Keeps the human inside Slack — no redirect to a separate web UI.

    The modal contains a plain_text_input block pre-filled with *current_draft*.
    On modal submit, Slack sends an interaction payload to the FastAPI handler
    which extracts the edited text, updates final_draft in state, and resumes
    the graph with Command(resume="edit").

    Args:
        trigger_id:     Slack trigger_id from the button-click interaction
                        payload (valid for 3 seconds).
        ticket_id:      Used as the modal's callback_id so the submit handler
                        can route back to the correct LangGraph thread.
        current_draft:  The current draft text pre-filled in the text area.

    Returns: dict with keys:
        ok (bool), view_id (str).
    """
    client = _client()

    modal_view: dict[str, Any] = {
        "type": "modal",
        "callback_id": f"edit_draft_{ticket_id}",
        "title": {"type": "plain_text", "text": "Edit Reply Draft"},
        "submit": {"type": "plain_text", "text": "Approve & Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "draft_block",
                "label": {
                    "type": "plain_text",
                    "text": "Edit the draft reply below:",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": "draft_input",
                    "multiline": True,
                    "initial_value": current_draft,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Edit the reply here...",
                    },
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_Ticket ID: {ticket_id}_",
                    }
                ],
            },
        ],
    }

    try:
        resp = await client.views_open(trigger_id=trigger_id, view=modal_view)
    except SlackApiError as exc:
        logger.error(
            "open_edit_modal failed: trigger_id=%s ticket=%s error=%s",
            trigger_id, ticket_id, exc.response,
        )
        raise

    view_id: str = resp["view"]["id"]
    logger.info("open_edit_modal: opened view_id=%s for ticket=%s", view_id, ticket_id)
    return {
        "ok": True,
        "view_id": view_id,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
