"""Graph nodes — every step the graph executes between Email Listener and End.

Each function takes `AgentState` and returns a dict of state UPDATES (not the
full state — LangGraph merges the dict into the existing state).

Conditional edges are separate functions that return the next node's name as
a string.

Spec source of truth: spec.md §6 (Nodes Detailed) and CLAUDE.md "Graph node
order" line.

Two non-negotiable rules from CLAUDE.md and spec §6.5:
  Rule 1: `interrupt()` lives alone in its dedicated node — `interrupt_gate`
          here. NO side effects in that node.
  Rule 2: Never wrap `interrupt()` in try/except.

Cross-track imports (will resolve when Tracks B and C land):
  - src.mcp_client          ← Track B
  - src.policy              ← Track C
  - src.slack_router        ← Track C
  - src.pii                 ← Track C
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.types import interrupt

from src.llm import (
    classify_intent,
    draft_response,
    summarize_context_changes,
)

# Track B/C imports — kept in try/except so this module is importable even
# while Tracks B and C are still in flight. At integration time the imports
# either resolve or we fix the names.
from src.mcp_client import (
    EmailSendParams,
    SlackPostParams,
    SlackUpdateParams,
)
from src.pii import (
    clear_ticket as _pii_clear_ticket,
)
from src.pii import (
    get_envelope_from as _pii_envelope_from,
)
from src.pii import (
    get_token_map as _pii_get_token_map,
)
from src.pii import (
    redact,
    restore,
)
from src.pii import (
    store_token_map as _pii_store_token_map,
)
from src.policy import gate_one_policy_risk, should_auto_send
from src.slack_router import route_channel
from src.state import AgentState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_context(payload: Any) -> str:
    """Stable hash for the stale-context check during long approval pauses."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _audit(state: AgentState, node: str, **fields: Any) -> dict[str, Any]:
    """Build an append-only audit log entry. Caller merges it into the state
    update by spreading over `audit_log` from the existing state."""
    return {
        "ts": _now_iso(),
        "node": node,
        "ticket_id": state.get("ticket_id", ""),
        **fields,
    }


def _client() -> Any:
    """Get the live, long-lived MCPClientRouter from graph_runner.

    The router is opened once at service startup and torn down at shutdown
    (see `src/graph_runner.startup` / `shutdown_async`). Callers are async
    nodes — they `await router.read.x()` etc. directly.
    """
    from src.graph_runner import get_mcp_router

    return get_mcp_router()


def _build_approval_blocks(state: AgentState, kb_quote: str) -> list[dict[str, Any]]:
    """Build the Block Kit message body for the Slack approval post.

    Spec source: spec.md §6 Slack Notification + §7 HITL Design.

    The message contains:
      - Ticket header (id + intent + customer email)
      - "Why I paused" panel — risk flags + confidence + policy match
      - KB justification quote (verbatim ACME policy sentence)
      - Draft reply (expandable)
      - Approve / Edit / Reject buttons (action_id matches slack_handler.py)
    """
    ticket_id = state.get("ticket_id", "")
    intent = state.get("intent", "")
    intent_conf = state.get("intent_confidence", 0.0)
    draft_conf = state.get("draft_confidence", 0.0)
    sentiment = state.get("sentiment", "")
    risk_flags = state.get("risk_flags") or []
    policy_matches = state.get("policy_matches") or []
    customer_tier = state.get("customer_tier", "Free")
    draft = state.get("final_draft", "") or state.get("original_draft", "")

    why_lines = []
    if risk_flags:
        why_lines.append(f"• risk_flags: `{', '.join(risk_flags)}`")
    if policy_matches:
        why_lines.append(f"• policy_match: `{', '.join(policy_matches)}`")
    why_lines.append(f"• intent_confidence: `{intent_conf:.2f}`")
    why_lines.append(f"• draft_confidence: `{draft_conf:.2f}`")
    if sentiment:
        why_lines.append(f"• sentiment: `{sentiment}`")

    # Recipient address shown to the human reviewer so they can spot
    # spoofed-From attempts before approving. Read from the trustworthy
    # envelope-from in the pii vault, NOT from the spoofable From: header.
    recipient = _customer_email_from_audit(state)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🟡 {ticket_id} · {intent or 'support request'}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tier*\n{customer_tier}"},
                {"type": "mrkdwn", "text": f"*Intent*\n{intent} ({intent_conf:.2f})"},
                {"type": "mrkdwn", "text": f"*Reply will go to*\n`{recipient}`"},
            ],
        },
        {
            "type": "section",
            # NOTE: block_id was previously set here as ticket_id, but Slack
            # rejects messages with duplicate block_ids. The actions block
            # below is the one that needs ticket_id (so the action payload
            # can recover thread_id). Section gets a deterministic but
            # distinct id for stability across edit/update calls.
            "block_id": f"{ticket_id}-why",
            "text": {"type": "mrkdwn", "text": "*Why I paused*\n" + "\n".join(why_lines)},
        },
    ]
    if kb_quote:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Justification (KB)*\n> {kb_quote}"},
            }
        )
    blocks.extend(
        [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Draft reply*\n```{draft}```"},
            },
            {
                "type": "actions",
                # block_id on the ACTIONS block — Slack puts this on the
                # button's action payload at body["actions"][0]["block_id"].
                # Without it, that field would be an auto-generated hash and
                # _thread_id_from_body couldn't recover the ticket_id.
                "block_id": ticket_id,
                "elements": [
                    {
                        "type": "button",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": "approve_button",
                        # `value` is round-tripped to the action payload at
                        # body["actions"][0]["value"]. Defense-in-depth on top
                        # of block_id — either path resolves the ticket_id.
                        "value": ticket_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit"},
                        "action_id": "edit_button",
                        "value": ticket_id,
                    },
                    {
                        "type": "button",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "action_id": "reject_button",
                        "value": ticket_id,
                    },
                ],
            },
        ]
    )
    return blocks


# ---------------------------------------------------------------------------
# 1. PII Redact (middleware — runs before any LLM sees the ticket text)
# ---------------------------------------------------------------------------


def pii_redact_node(state: AgentState) -> dict[str, Any]:
    """Redact PII before any LLM call.

    The token_map (real PII) is stored in the ephemeral pii vault keyed
    by ticket_id — NOT in audit_log. audit_log is checkpointed to SQLite
    and emitted to LangSmith; storing real emails / phones / CCs there
    would violate the "LLM never sees real PII" invariant in spirit.
    The audit entry records only token_count for observability.
    """
    redacted, token_map = redact(state["customer_message"])
    ticket_id = state.get("ticket_id", "")
    if ticket_id:
        _pii_store_token_map(ticket_id, token_map)
    return {
        "customer_message": redacted,
        "audit_log": (state.get("audit_log") or [])
        + [_audit(state, "pii_redact", token_count=len(token_map))],
    }


# ---------------------------------------------------------------------------
# 2. Classify Intent
# ---------------------------------------------------------------------------


async def classify_intent_node(state: AgentState) -> dict[str, Any]:
    result = await classify_intent(state["customer_message"], state=state)
    return {
        "intent": result.intent,
        "intent_confidence": result.intent_confidence,
        "sentiment": result.sentiment,
        "risk_flags": result.risk_flags,
        "risk_level": result.risk_level,
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "classify_intent",
                intent=result.intent,
                confidence=result.intent_confidence,
                sentiment=result.sentiment,
            )
        ],
    }


# ---------------------------------------------------------------------------
# 3. Enrich Context (MCP READ — three calls in parallel where possible)
# ---------------------------------------------------------------------------


async def enrich_context_node(state: AgentState) -> dict[str, Any]:
    client = _client()
    customer_email = _customer_email_from_audit(state)

    profile = await client.read.get_crm_profile(customer_email)  # CRMProfile
    history = await client.read.get_customer_history(customer_email)  # list[HistoryEntry]
    kb = await client.read.get_kb_article(state["customer_message"])  # KBResult

    history_dicts = [h.model_dump() for h in history]
    context_payload = {
        "profile": profile.model_dump(),
        "history": history_dicts,
        "kb": kb.model_dump(),
    }
    kb_quote = kb.verbatim_quote
    return {
        "customer_history": history_dicts,
        "customer_tier": profile.customer_tier or "Free",
        "policy_matches": kb.policy_references or [],
        "context_hash": _hash_context(context_payload),
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "enrich_context",
                customer_tier=profile.customer_tier,
                history_len=len(history_dicts),
                kb_quote=kb_quote,
                policy_refs=kb.policy_references,
            )
        ],
    }


def _customer_email_from_audit(state: AgentState) -> str:
    """Recover the customer email after PII redact replaced it.

    Two-tier lookup, trustworthy first:
      1. SMTP envelope-from in the pii vault (captured from Return-Path
         by email_listener — NOT spoofable by message content).
      2. First [EMAIL_*] in the redacted token_map (matches whatever
         the From: header said — spoofable; only used when envelope is
         absent, e.g., test fixtures or non-IMAP entry points).

    Returns 'unknown@example.com' if neither path resolves — this sentinel
    address means the recipient is unknown; downstream Send Email MUST
    refuse rather than silently substituting it. (Audit caller should fail
    closed.)

    Compatibility shim: legacy state where token_map was stored inline in
    the audit_log entry still resolves correctly so checkpoints persisted
    before this fix continue to work after the upgrade.
    """
    ticket_id = state.get("ticket_id", "")
    # Tier 1: trustworthy envelope-from
    if ticket_id:
        envelope = _pii_envelope_from(ticket_id)
        if envelope:
            return envelope
        tm = _pii_get_token_map(ticket_id)
        for token, original in tm.items():
            if token.startswith("[EMAIL_"):
                return original
    # Tier 2: legacy compatibility — read from audit_log if vault is empty
    # (covers checkpointed state from before this fix).
    for entry in reversed(state.get("audit_log") or []):
        if entry.get("node") == "pii_redact":
            tm_legacy = entry.get("token_map") or {}
            for token, original in tm_legacy.items():
                if token.startswith("[EMAIL_"):
                    return str(original)
    return "unknown@example.com"


# ---------------------------------------------------------------------------
# 4. Draft Response
# ---------------------------------------------------------------------------


async def draft_response_node(state: AgentState) -> dict[str, Any]:
    profile = _last_profile_from_audit(state)
    history = state.get("customer_history") or []
    kb_quotes = _last_kb_quotes_from_audit(state)

    result = await draft_response(
        customer_message_redacted=state["customer_message"],
        intent=state.get("intent", "other"),
        customer_profile=profile,
        customer_history=history,
        policy_quotes=kb_quotes,
        rejection_reason=state.get("rejection_reason"),
        state=state,
    )
    return {
        "original_draft": state.get("original_draft") or result.draft,
        "final_draft": result.draft,
        "draft_confidence": result.draft_confidence,
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "draft_response",
                draft_confidence=result.draft_confidence,
                redraft=bool(state.get("rejection_reason")),
            )
        ],
    }


def _last_profile_from_audit(state: AgentState) -> dict[str, Any]:
    # Caller may not have a profile in state yet — return empty rather than
    # crashing the LLM call.
    return {"customer_tier": state.get("customer_tier", "Free")}


def _last_kb_quotes_from_audit(state: AgentState) -> list[str]:
    """Return the verbatim KB quote that Enrich Context attached. Stays in the
    audit log so the top-level state stays slim, but is needed for both the
    Draft prompt and the Slack approval message."""
    quotes: list[str] = []
    for entry in reversed(state.get("audit_log") or []):
        if entry.get("node") == "enrich_context":
            q = entry.get("kb_quote")
            if q:
                quotes.append(q)
            break
    if not quotes:
        for m in state.get("policy_matches") or []:
            quotes.append(f"Policy {m}")
    return quotes


# ---------------------------------------------------------------------------
# 5. Policy / Confidence — conditional edges (return next node name)
# ---------------------------------------------------------------------------


def route_after_draft(state: AgentState) -> str:
    """Combined Gate 1 + Gate 2 + intent safety. Single conditional edge from
    draft_response. Returns a real node name, not a virtual decision.

    - Gate 1 fail (risk_flags / policy match / refund / angry) → escalate
    - Gate 2 fail (either confidence < 0.85) → escalate
    - Both gates pass AND intent in safe set → auto-send
    - Both gates pass but unsafe intent → escalate
    """
    if gate_one_policy_risk(state):
        return "channel_router"
    return "auto_send_marker" if should_auto_send(state) else "channel_router"


# Kept individually for unit testing (Track C tests these gates directly).
def route_after_policy_check(state: AgentState) -> str:
    return "channel_router" if gate_one_policy_risk(state) else "confidence_check"


def route_after_confidence_check(state: AgentState) -> str:
    return "auto_send_marker" if should_auto_send(state) else "channel_router"


# ---------------------------------------------------------------------------
# 6. Channel Router (writes slack_channel; the router function itself is
#    pure — Track C ships it)
# ---------------------------------------------------------------------------


def channel_router_node(state: AgentState) -> dict[str, Any]:
    channel = route_channel(state)
    return {
        "slack_channel": channel,
        "audit_log": (state.get("audit_log") or [])
        + [_audit(state, "channel_router", channel=channel)],
    }


# ---------------------------------------------------------------------------
# 7. Slack Notification — posts Block Kit BEFORE interrupt. Saves message_ts.
# ---------------------------------------------------------------------------


async def slack_notification_node(state: AgentState) -> dict[str, Any]:
    """Posts the Block Kit approval message BEFORE the interrupt. Saves
    `slack_message_ts` so resume targets the same message even after a
    server restart."""
    client = _client()
    sla_deadline = datetime.now(UTC) + timedelta(
        hours=int(os.environ.get("SLA_DEADLINE_HOURS", "24"))
    )
    kb_quotes = _last_kb_quotes_from_audit(state)
    blocks = _build_approval_blocks(state, kb_quote=kb_quotes[0] if kb_quotes else "")
    fallback_text = f"🟡 {state.get('ticket_id', 'ticket')} needs approval ({state.get('intent', 'support')})"

    result = await client.slack.post_approval_request(
        SlackPostParams(
            channel=state["slack_channel"],
            blocks=blocks,
            text=fallback_text,
        )
    )
    return {
        "slack_message_ts": result.slack_message_ts,
        # Replace the human-readable channel name (e.g. "#support-refunds")
        # with the canonical channel ID (e.g. "C0B2U0W84MP") that Slack's
        # `chat.update` API requires. Without this, every subsequent
        # update_message call fails with `channel_not_found` and the
        # approval message never visually changes after Approve/Edit/Reject.
        "slack_channel": result.channel,
        "approval_status": "pending",
        "sla_deadline": sla_deadline,
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "slack_notification",
                channel=result.channel,
                # NOTE: kwarg renamed from `ts` to `slack_ts` — `_audit` builds
                # `{"ts": _now_iso(), **fields}` so passing `ts=` overwrites the
                # ISO 8601 wall-clock with the Slack unix-epoch string. That
                # silently broke `route_after_action`'s elapsed-time check
                # (datetime.fromisoformat raised ValueError, swallowed by a
                # bare except, always routed to "finalize"). Audit finding H1.
                slack_ts=result.slack_message_ts,
            )
        ],
    }


# ---------------------------------------------------------------------------
# 8. Interrupt Gate — DEDICATED. Implementation Rule 1.
# ---------------------------------------------------------------------------


def interrupt_gate(state: AgentState) -> dict[str, Any]:
    """ONLY interrupt(). Nothing else. Never wrap in try/except (Rule 2)."""
    action = interrupt(
        {
            "ticket_id": state.get("ticket_id", ""),
            "slack_channel": state.get("slack_channel", ""),
            "slack_message_ts": state.get("slack_message_ts", ""),
            "draft": state.get("final_draft", ""),
            "prompt": "Approve / Edit / Reject?",
        }
    )
    # `action` is whatever the webhook handler passed via Command(resume=...).
    # Conventionally a dict {"action": "approve|edit|reject", "edited_draft": "...", "reason": "...", "approver_id": "..."}.
    if isinstance(action, dict):
        return {
            "approval_status": action.get("action", str(action)),
            "approver_id": action.get("approver_id", ""),
            "approval_timestamp": _now_iso(),
            "rejection_reason": action.get("reason"),
            "final_draft": action.get("edited_draft") or state.get("final_draft", ""),
        }
    return {"approval_status": str(action), "approval_timestamp": _now_iso()}


# ---------------------------------------------------------------------------
# 9. Reject / Elapsed — conditional edges
# ---------------------------------------------------------------------------


def route_after_action(state: AgentState) -> str:
    """After the interrupt resumes, branch directly to a real node name.

    - reject → reject_increment (writes count++ then evaluates 3-strike rule)
    - approve / edit + elapsed > threshold → revalidate_context
    - approve / edit + elapsed ≤ threshold → finalize
    """
    status = (state.get("approval_status") or "").lower()
    if status == "reject":
        return "reject_increment"
    threshold_min = int(os.environ.get("REVALIDATE_THRESHOLD_MIN", "15"))
    ts = state.get("approval_timestamp")
    notif_ts = None
    for e in state.get("audit_log") or []:
        if e.get("node") == "slack_notification":
            notif_ts = e.get("ts")
            break
    if not (ts and notif_ts):
        return "finalize"
    try:
        delta_min = (
            datetime.fromisoformat(ts) - datetime.fromisoformat(notif_ts)
        ).total_seconds() / 60.0
    except ValueError:
        return "finalize"
    return "revalidate_context" if delta_min > threshold_min else "finalize"


def route_after_reject(state: AgentState) -> str:
    """3-strike rule. Rejection increment node has already incremented the
    count before this conditional runs."""
    max_rej = int(os.environ.get("MAX_HUMAN_REJECTIONS", "3"))
    return "manual_queue" if state.get("human_rejection_count", 0) >= max_rej else "draft_response"


def reject_increment_node(state: AgentState) -> dict[str, Any]:
    new_count = state.get("human_rejection_count", 0) + 1
    return {
        "human_rejection_count": new_count,
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "reject_increment",
                count=new_count,
                reason=state.get("rejection_reason"),
            )
        ],
    }


# ---------------------------------------------------------------------------
# 10. Revalidate Context (slow path)
# ---------------------------------------------------------------------------


async def revalidate_context_node(state: AgentState) -> dict[str, Any]:
    client = _client()
    customer_email = _customer_email_from_audit(state)
    profile = await client.read.get_crm_profile(customer_email)
    history = await client.read.get_customer_history(customer_email)
    kb = await client.read.get_kb_article(state["customer_message"])
    new_payload = {
        "profile": profile.model_dump(),
        "history": [h.model_dump() for h in history],
        "kb": kb.model_dump(),
    }
    new_hash = _hash_context(new_payload)
    return {
        "context_hash": new_hash,
        "_revalidate_changed": new_hash != state.get("context_hash", ""),
        "_revalidate_new_snapshot": new_payload,
        "audit_log": (state.get("audit_log") or [])
        + [_audit(state, "revalidate_context", changed=new_hash != state.get("context_hash", ""))],
    }


def route_after_revalidate(state: AgentState) -> str:
    return "summarize_changes" if state.get("_revalidate_changed") else "finalize"


# ---------------------------------------------------------------------------
# 11. Summarize Changes (LLM delta) — re-interrupts to wait for re-decision
# ---------------------------------------------------------------------------


async def summarize_changes_node(state: AgentState) -> dict[str, Any]:
    old_snapshot: dict[str, Any] = {
        "customer_tier": state.get("customer_tier", ""),
        "policy_matches": state.get("policy_matches") or [],
    }
    new_snapshot = state.get("_revalidate_new_snapshot") or {}
    delta = await summarize_context_changes(old_snapshot, new_snapshot, state=state)

    if state.get("slack_message_ts"):
        client = _client()
        kb_quotes = _last_kb_quotes_from_audit(state)
        # Re-render blocks with a "context changed" header on top.
        delta_blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⚠️ *Context changed during pause* — {delta.summary}",
                },
            },
            *_build_approval_blocks(state, kb_quote=kb_quotes[0] if kb_quotes else ""),
        ]
        await client.slack.update_message(
            SlackUpdateParams(
                channel=state["slack_channel"],
                ts=state["slack_message_ts"],
                blocks=delta_blocks,
                text=f"Context changed: {delta.summary}",
            )
        )
    return {
        # Reset to pending so the graph re-interrupts
        "approval_status": "pending",
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "summarize_changes",
                changed_fields=delta.changed_fields,
                summary=delta.summary,
            )
        ],
    }


# ---------------------------------------------------------------------------
# 12. Finalize Action (PII restore + payload assembly — pure, no I/O)
# ---------------------------------------------------------------------------


def finalize_action_node(state: AgentState) -> dict[str, Any]:
    """Restore PII tokens to their real values before SMTP send.

    Token map source order:
      1. Ephemeral pii vault (canonical, populated by pii_redact_node)
      2. audit_log entry (legacy compat for checkpoints written before vault landed)
    """
    ticket_id = state.get("ticket_id", "")
    token_map: dict[str, str] = {}
    if ticket_id:
        token_map = _pii_get_token_map(ticket_id)
    if not token_map:
        # Legacy fallback for state checkpointed before the vault refactor.
        for entry in state.get("audit_log") or []:
            if entry.get("node") == "pii_redact":
                token_map = entry.get("token_map") or {}
                break

    final_with_pii = restore(state.get("final_draft", ""), token_map)
    return {
        "final_draft": final_with_pii,
        "send_status": "in_flight",
        "audit_log": (state.get("audit_log") or [])
        + [_audit(state, "finalize", chars=len(final_with_pii))],
    }


# ---------------------------------------------------------------------------
# 13. Send Email (the only irreversible step in the graph)
# ---------------------------------------------------------------------------


async def send_email_node(state: AgentState) -> dict[str, Any]:
    # App-layer idempotency #1: state already says it's sent.
    if state.get("sent_message_id"):
        return {
            "send_status": "sent",
            "audit_log": (state.get("audit_log") or [])
            + [_audit(state, "send_email", skipped="already_sent_state")],
        }

    # Fail closed on unresolved recipient BEFORE touching the MCP client —
    # the sentinel value means we could not derive a trustworthy address
    # from the SMTP envelope or the redacted token map. Refusing to send
    # beats sending to a stranger, AND skipping the client lookup keeps
    # this branch testable without a live MCP router subprocess.
    customer_email = _customer_email_from_audit(state)
    if customer_email in ("unknown@example.com", "", None):
        return {
            "send_status": "failed_manual",
            "final_state": "failed_manual",
            "audit_log": (state.get("audit_log") or [])
            + [
                _audit(
                    state,
                    "send_email",
                    skipped="unknown_recipient",
                    reason="No trustworthy envelope-from or redacted address resolved.",
                )
            ],
        }

    client = _client()
    subject = "Re: " + _subject_from_state(state)

    try:
        # App-layer idempotency #2: the MCP server's SQLite idem-store will
        # short-circuit on duplicate idempotency_key (was_duplicate=True).
        result = await client.email.send(
            EmailSendParams(
                to=customer_email,
                subject=subject,
                body=state["final_draft"],
                in_reply_to=state.get("email_thread_id", ""),
                references=state.get("email_thread_id", ""),
                idempotency_key=state["send_idempotency_key"],
            )
        )
        return {
            "sent_message_id": result.sent_message_id,
            "send_status": "sent",
            "final_state": "sent",
            "audit_log": (state.get("audit_log") or [])
            + [
                _audit(
                    state,
                    "send_email",
                    sent_id=result.sent_message_id,
                    duplicate=result.was_duplicate,
                    server_status=result.status,
                )
            ],
        }
    except Exception as exc:
        new_count = state.get("send_retry_count", 0) + 1
        max_retries = int(os.environ.get("MAX_SEND_RETRIES", "3"))
        if new_count >= max_retries:
            return {
                "send_retry_count": new_count,
                "send_status": "failed_manual",
                "audit_log": (state.get("audit_log") or [])
                + [_audit(state, "send_email", error=str(exc), terminal=True)],
            }
        return {
            "send_retry_count": new_count,
            "send_status": "failed_retryable",
            "audit_log": (state.get("audit_log") or [])
            + [_audit(state, "send_email", error=str(exc), retry=new_count)],
        }


def _subject_from_state(state: AgentState) -> str:
    """Recover the original Subject from the customer_message header section
    that the email_listener prepended. Falls back to a generic subject."""
    msg = state.get("customer_message", "") or ""
    for line in msg.split("\n", 5):
        if line.lower().startswith("subject:"):
            sub = line.split(":", 1)[1].strip()
            if sub.lower().startswith("re:"):
                return sub[3:].strip()
            return sub
    return "your support request"


def route_after_send(state: AgentState) -> str:
    if state.get("send_status") == "sent":
        return "audit_log"
    if state.get("send_status") == "failed_retryable":
        return "send_email"  # retry; idempotency check at top of node short-circuits dupes
    return "manual_queue"  # failed_manual


# ---------------------------------------------------------------------------
# 14. Audit Log (terminal append) + Manual Queue (terminal escalate)
# ---------------------------------------------------------------------------


async def audit_log_node(state: AgentState) -> dict[str, Any]:
    """Final close-out. Append-only audit entry + best-effort Slack update.

    Clears PII vault entry for this ticket — the token map and envelope-from
    have done their job, no reason to keep real PII in process memory.
    """
    if state.get("slack_message_ts") and state.get("slack_channel"):
        try:
            client = _client()
            text = f"📤 Reply sent · approver: {state.get('approver_id') or 'auto-send'}"
            await client.slack.update_message(
                SlackUpdateParams(
                    channel=state["slack_channel"],
                    ts=state["slack_message_ts"],
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"✅ *{text}*"},
                        }
                    ],
                    text=text,
                )
            )
        except Exception:
            pass  # best-effort — not blocking the audit close

    ticket_id = state.get("ticket_id", "")
    if ticket_id:
        _pii_clear_ticket(ticket_id)

    return {
        "final_state": state.get("final_state") or "sent",
        "audit_log": (state.get("audit_log") or []) + [_audit(state, "audit_close")],
    }


async def manual_queue_node(state: AgentState) -> dict[str, Any]:
    max_rej = int(os.environ.get("MAX_HUMAN_REJECTIONS", "3"))
    if state.get("send_status") == "failed_manual":
        terminal = "failed_manual"
        notice = "🚦 Send failed after retries — manual queue."
    elif state.get("human_rejection_count", 0) >= max_rej:
        terminal = "rejected"
        notice = f"🚦 {max_rej} rejections — manual queue."
    else:
        terminal = "expired"
        notice = "🚦 SLA expired — manual queue."

    if state.get("slack_message_ts") and state.get("slack_channel"):
        try:
            client = _client()
            await client.slack.update_message(
                SlackUpdateParams(
                    channel=state["slack_channel"],
                    ts=state["slack_message_ts"],
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": notice},
                        }
                    ],
                    text=notice,
                )
            )
        except Exception:
            pass

    ticket_id = state.get("ticket_id", "")
    if ticket_id:
        _pii_clear_ticket(ticket_id)

    return {
        "final_state": terminal,
        "audit_log": (state.get("audit_log") or []) + [_audit(state, "manual_queue", terminal=terminal)],
    }


# ---------------------------------------------------------------------------
# 15. Auto-send fast path — when both gates pass and intent is in safe set,
#     skip Slack notification entirely. Conditional edge selector lives at
#     the post-confidence-check fork; this node here just stamps the audit
#     entry showing why we auto-sent (for transparency in the trace).
# ---------------------------------------------------------------------------


def auto_send_marker_node(state: AgentState) -> dict[str, Any]:
    return {
        "approval_status": "auto",
        "audit_log": (state.get("audit_log") or [])
        + [
            _audit(
                state,
                "auto_send",
                intent=state.get("intent", ""),
                intent_confidence=state.get("intent_confidence", 0.0),
                draft_confidence=state.get("draft_confidence", 0.0),
            )
        ],
    }


__all__ = [
    "auto_send_marker_node",
    "audit_log_node",
    "channel_router_node",
    "classify_intent_node",
    "draft_response_node",
    "enrich_context_node",
    "finalize_action_node",
    "interrupt_gate",
    "manual_queue_node",
    "pii_redact_node",
    "reject_increment_node",
    "revalidate_context_node",
    "route_after_action",
    "route_after_confidence_check",
    "route_after_draft",
    "route_after_policy_check",
    "route_after_reject",
    "route_after_revalidate",
    "route_after_send",
    "send_email_node",
    "slack_notification_node",
    "summarize_changes_node",
]
