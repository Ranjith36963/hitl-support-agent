"""AgentState TypedDict — full schema per spec.md §5.

The graph's single source of truth for ticket state. Every node reads and writes
through this dict; the SQLite checkpointer persists the whole structure at each
super-step boundary.

Field grouping mirrors spec.md §5 verbatim. Do not reorder without updating spec.
"""

from datetime import datetime
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ---- Identity ----
    ticket_id: str
    thread_id: str  # equals ticket_id (stable resume pointer for the checkpointer)
    graph_version: str  # "v3"

    # ---- Input ----
    customer_message: str
    customer_history: list[dict[str, Any]]
    context_hash: str  # hash of retrieved_context for stale-check during long pauses

    # ---- Classification ----
    intent: str  # refund | technical | billing | complaint | FAQ | other
    intent_confidence: float
    sentiment: str  # angry | neutral | positive
    risk_flags: list[str]

    # ---- Drafting ----
    original_draft: str
    final_draft: str
    draft_confidence: float

    # ---- Customer-tier-aware routing inputs ----
    customer_tier: str  # Free | SMB | Enterprise (from CRM)
    risk_level: str  # none | financial | legal | compliance
    policy_matches: list[str]  # e.g. ["ACME 4.2.1", "ACME 7.1"]

    # ---- Approval ----
    requires_approval: bool
    approval_status: str  # pending | approved | edited | rejected | expired | cancelled | superseded
    approver_id: str  # Slack user id
    approval_timestamp: str
    sla_deadline: datetime

    # ---- Real I/O channels ----
    email_thread_id: str  # original RFC-822 Message-ID — drives In-Reply-To threading
    slack_channel: str  # e.g. "#support-refunds"
    slack_message_ts: str  # used to update_message in place AND resume on the right msg

    # ---- Idempotency on send (app-layer, not SMTP-layer) ----
    send_idempotency_key: str  # set once at entry — lookup key for dedupe
    sent_message_id: str | None  # presence == "already sent" — Send node skips if set

    # ---- Execution ----
    send_status: str  # pending | in_flight | sent | failed_retryable | failed_manual

    # ---- Loop guards ----
    human_rejection_count: int  # >= MAX_HUMAN_REJECTIONS routes to manual_queue
    rejection_reason: str | None  # captured from Slack reject modal; carried into next Draft
    send_retry_count: int  # >= MAX_SEND_RETRIES routes to failed_manual

    # ---- Long-pause edge cases ----
    ticket_external_status: str  # open | closed_by_customer | superseded

    # ---- Terminal ----
    final_state: str  # sent | rejected | expired | cancelled | superseded | failed_manual

    # ---- Audit + observability ----
    audit_log: list[dict[str, Any]]  # APPEND-ONLY — never mutate prior entries
    cost_breakdown: dict[str, float]  # {"classify": 0.0001, "draft": 0.0008, ...}
    total_tokens: int
    total_cost_usd: float
    trace_url: str

    # ---- v4 multi-agent transient fields (Drafter↔Critic loop) ----
    # Underscore-prefix marks them as transient — not part of v3 contract.
    # Only used inside the drafter sub-graph; cleared on exit.
    _critic_iteration: int        # 0..MAX_CRITIC_ITERATIONS-1
    _critic_verdict: str          # accept | revise
    _critic_feedback: str         # short, actionable string
    _critic_severity: float       # [0, 1]


# Default factory — every required key has a sensible zero value so
# nodes don't need to defend against missing keys.
def initial_state(
    ticket_id: str,
    customer_message: str,
    email_thread_id: str,
    send_idempotency_key: str,
) -> AgentState:
    """Build a fresh AgentState for a new ticket. thread_id == ticket_id."""
    return AgentState(
        ticket_id=ticket_id,
        thread_id=ticket_id,
        graph_version="v3",
        customer_message=customer_message,
        customer_history=[],
        context_hash="",
        intent="",
        intent_confidence=0.0,
        sentiment="",
        risk_flags=[],
        original_draft="",
        final_draft="",
        draft_confidence=0.0,
        customer_tier="",
        risk_level="none",
        policy_matches=[],
        requires_approval=False,
        approval_status="",
        approver_id="",
        approval_timestamp="",
        email_thread_id=email_thread_id,
        slack_channel="",
        slack_message_ts="",
        send_idempotency_key=send_idempotency_key,
        sent_message_id=None,
        send_status="pending",
        human_rejection_count=0,
        rejection_reason=None,
        send_retry_count=0,
        ticket_external_status="open",
        final_state="",
        audit_log=[],
        cost_breakdown={},
        total_tokens=0,
        total_cost_usd=0.0,
        trace_url="",
    )
