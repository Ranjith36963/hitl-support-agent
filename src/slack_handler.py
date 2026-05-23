"""Slack action handler — verifies signed webhooks and resumes the graph.

Dev mode uses Bolt's Socket Mode (no public URL needed). Production would
expose `/slack/events` via FastAPI and verify HMAC-SHA256 signatures. Both
code paths live here so the swap is one env var (`SLACK_APP_TOKEN` present →
Socket Mode).

Security boundary (spec §6 + architecture.md "Key design points"):
  - HMAC-SHA256 of `v0:{X-Slack-Request-Timestamp}:{raw body}` with the
    signing secret. Constant-time compare.
  - Reject 401 if signature mismatches OR timestamp is older than 5 minutes
    (replay-attack defense).

Resume contract:
  - Approve  → Command(resume={"action": "approve", "approver_id": "U..."})
  - Edit     → views.open modal; on submit → Command(resume={"action": "edit", "edited_draft": "..."})
  - Reject   → views.open small modal; on submit → Command(resume={"action": "reject", "reason": "..."})
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from src.config import settings

# A separate Bolt app instance so the FastAPI app can mount its handlers
# without re-instantiating Bolt. Compiled lazily so importing this module
# is safe before secrets are provisioned.

_app: AsyncApp | None = None


def get_app() -> AsyncApp:
    global _app
    if _app is None:
        settings.require_secrets("slack_bot_token", "slack_signing_secret")
        _app = AsyncApp(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )
        _register_handlers(_app)
    return _app


# ---------------------------------------------------------------------------
# HMAC verification (used by the FastAPI webhook path; Bolt does its own
# verification in Socket Mode but we keep this for the prod webhook path
# and so the `tests/test_slack_handler.py` suite can prove it).
# ---------------------------------------------------------------------------


def verify_slack_signature(
    *, body: bytes, timestamp: str, signature: str, signing_secret: str | None = None,
    now: float | None = None,
) -> bool:
    """Verify a Slack request signature.

    - timestamp must be within 5 minutes of `now` (default: time.time())
    - signature must equal `"v0=" + HMAC-SHA256(secret, "v0:{ts}:{body}")`
    - constant-time compare
    """
    secret = signing_secret or settings.slack_signing_secret
    if not secret:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > 60 * 5:
        return False  # replay defense
    base = f"v0:{ts}:".encode() + body
    expected = "v0=" + hmac.new(
        secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


# ---------------------------------------------------------------------------
# Slack action handlers — wired into the AsyncApp on first `get_app()` call.
# Each handler's job: extract the resume payload, then call back into the
# compiled LangGraph via the graph_runner module (Phase 3 integration).
# ---------------------------------------------------------------------------


def _register_handlers(app: AsyncApp) -> None:
    import logging

    log = logging.getLogger(__name__)

    @app.action("approve_button")
    async def on_approve(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        thread_id = _thread_id_from_body(body)
        approver = body.get("user", {}).get("id", "")
        log.info("approve_button clicked: thread_id=%r approver=%r", thread_id, approver)
        if not thread_id:
            log.warning("approve_button: could not resolve thread_id from body — resume skipped")
            return
        await _resume_graph(thread_id, {"action": "approve", "approver_id": approver})

    @app.action("reject_button")
    async def on_reject(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        thread_id = _thread_id_from_body(body)
        log.info("reject_button clicked: thread_id=%r", thread_id)
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=_reject_modal(thread_id),
        )

    @app.action("edit_button")
    async def on_edit(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        thread_id = _thread_id_from_body(body)
        log.info("edit_button clicked: thread_id=%r", thread_id)
        original_draft = _draft_from_body(body)
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=_edit_modal(thread_id, original_draft),
        )

    @app.view("edit_view")
    async def on_edit_submit(ack: Any, body: dict[str, Any], view: dict[str, Any]) -> None:
        await ack()
        thread_id = json.loads(view.get("private_metadata", "{}")).get("thread_id", "")
        edited = (
            view["state"]["values"]["edited_draft_block"]["edited_draft_input"]["value"]
            or ""
        )
        approver = body.get("user", {}).get("id", "")
        await _resume_graph(
            thread_id,
            {"action": "edit", "edited_draft": edited, "approver_id": approver},
        )

    @app.view("reject_view")
    async def on_reject_submit(ack: Any, body: dict[str, Any], view: dict[str, Any]) -> None:
        await ack()
        thread_id = json.loads(view.get("private_metadata", "{}")).get("thread_id", "")
        reason = (
            view["state"]["values"]["reason_block"]["reason_input"]["value"] or ""
        )
        approver = body.get("user", {}).get("id", "")
        await _resume_graph(
            thread_id,
            {"action": "reject", "reason": reason, "approver_id": approver},
        )


def _thread_id_from_body(body: dict[str, Any]) -> str:
    """Recover the LangGraph thread_id (== ticket_id) from a Slack action payload.

    Three resolution paths in order of reliability:
      1. button `value` (set explicitly by `_build_approval_blocks` in nodes.py)
      2. action's `block_id` (Slack carries the parent block's block_id on
         the action; we set this to ticket_id on the actions block)
      3. message-level metadata (legacy fallback)
    """
    actions = body.get("actions") or []
    if actions:
        first = actions[0]
        # 1. button value — most reliable, explicitly set by us
        value = str(first.get("value", ""))
        if value.startswith("ticket-"):
            return value
        # 2. block_id on the actions block
        block_id = str(first.get("block_id", ""))
        if block_id.startswith("ticket-"):
            return block_id
    # 3. metadata fallback
    msg = body.get("message", {})
    metadata = msg.get("metadata", {}).get("event_payload", {})
    return str(metadata.get("thread_id", ""))


def _draft_from_body(body: dict[str, Any]) -> str:
    msg = body.get("message", {})
    metadata = msg.get("metadata", {}).get("event_payload", {})
    return str(metadata.get("draft", ""))


# ---------------------------------------------------------------------------
# Modal payloads
# ---------------------------------------------------------------------------


def _edit_modal(thread_id: str, draft: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "edit_view",
        "private_metadata": json.dumps({"thread_id": thread_id}),
        "title": {"type": "plain_text", "text": "Edit reply"},
        "submit": {"type": "plain_text", "text": "Approve & Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "edited_draft_block",
                "label": {"type": "plain_text", "text": "Reply"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "edited_draft_input",
                    "multiline": True,
                    "initial_value": draft,
                },
            }
        ],
    }


def _reject_modal(thread_id: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "reject_view",
        "private_metadata": json.dumps({"thread_id": thread_id}),
        "title": {"type": "plain_text", "text": "Reject draft"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "reason_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Why? (optional, helps redraft)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_input",
                    "multiline": True,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Graph runner — re-enters the compiled LangGraph with Command(resume=...).
# Imported lazily so this module is import-safe even before graph_runner.py
# exists. Phase 3 integration creates `src/graph_runner.py` with a singleton
# compiled graph + a thread-safe `resume(thread_id, value)` coroutine.
# ---------------------------------------------------------------------------


async def _resume_graph(thread_id: str, resume_value: dict[str, Any]) -> None:
    if not thread_id:
        return
    from src.graph_runner import resume as runner_resume

    await runner_resume(thread_id, resume_value)


# ---------------------------------------------------------------------------
# Socket Mode entry point — `python -m src.slack_handler` runs the bot.
# ---------------------------------------------------------------------------


async def run_socket_mode() -> None:
    settings.require_secrets("slack_bot_token", "slack_app_token", "slack_signing_secret")
    app = get_app()
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()  # type: ignore[no-untyped-call]


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_socket_mode())
