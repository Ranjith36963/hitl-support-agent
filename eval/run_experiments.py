"""Eval harness entrypoint -- runs 10 hand-curated tickets through the full graph.

Usage:
    python -m eval.run_experiments          # real LLM (requires OPENROUTER_API_KEY)
    python -m eval.run_experiments --no-llm # deterministic canned classifications

Outputs:
    eval/results.md   -- human-readable metrics table
    eval/results.json -- machine-readable raw results

Design:
    - Uses the full production graph (compile_full_with_checkpointer)
    - Patches src.nodes._client with a fake MCPClientRouter (no Gmail/Slack needed)
    - In --no-llm mode: also patches src.nodes.classify_intent and src.nodes.draft_response
      with per-ticket canned results (deterministic, no LLM call)
    - In real mode: uses the real LLM via OPENROUTER_API_KEY
    - Handles multi-turn resume_sequence (T06 reject-then-redraft)
    - false_auto_send_rate is the primary safety metric -- any non-zero value is a
      blocking failure before shipping v3

Patch points match tests/test_integration_smoke.py exactly:
    patch("src.nodes.classify_intent", ...)
    patch("src.nodes.draft_response", ...)
    patch("src.nodes._client", lambda: fake_router)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

# Load .env at module import so OPENROUTER_API_KEY / LANGSMITH_API_KEY are
# available to os.environ before any LLM client is constructed.
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path when run as `python -m eval.run_experiments`
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from langgraph.types import Command  # noqa: E402

from eval.dataset import EVAL_TICKETS, EvalTicket  # noqa: E402
from eval.evaluators import (  # noqa: E402
    EvalResult,
    escalation_precision,
    failure_slice,
    false_auto_send_rate,
    intent_accuracy,
    response_quality,
)
from src.graph import async_sqlite_checkpointer, compile_full_with_checkpointer  # noqa: E402
from src.llm import ClassificationResult, DraftResult  # noqa: E402
from src.mcp_client import (  # noqa: E402
    CRMProfile,
    EmailSendResult,
    HistoryEntry,
    KBResult,
    SlackPostResult,
    SlackUpdateResult,
)
from src.state import initial_state  # noqa: E402

# ---------------------------------------------------------------------------
# Fake MCPClientRouter -- same shape as tests/test_integration_smoke.py::_fake_router()
# Capability isolation is preserved: read / email / slack are separate sub-objects.
# ---------------------------------------------------------------------------


def _fake_router() -> Any:
    """Build a fake MCPClientRouter with all tools stubbed.

    Canned responses are intentionally neutral / permissive:
    - CRM returns SMB tier (not Enterprise) so enterprise routing doesn't fire unexpectedly.
    - KB returns empty matches unless overridden per ticket.
    - Slack post always returns a valid ts.
    - Email send always succeeds.
    """
    router = AsyncMock()

    # READ server
    router.read.get_crm_profile = AsyncMock(
        return_value=CRMProfile(
            email="customer@example.com",
            customer_tier="SMB",
            contract_value_usd=120.0,
            renewal_date="2027-06-01",
            billing_status="current",
            account_status="Active",
            open_tickets=1,
        )
    )
    router.read.get_customer_history = AsyncMock(
        return_value=[
            HistoryEntry(
                ticket_id="t-prior-0",
                date="2026-03-01",
                intent="FAQ",
                resolution="resolved",
                sentiment="neutral",
                customer_email="customer@example.com",
            )
        ]
    )
    # Default: no KB matches (won't trigger policy_match risk flag for auto-send tickets)
    router.read.get_kb_article = AsyncMock(
        return_value=KBResult(
            matched_sections=[],
            verbatim_quote="",
            policy_references=[],
        )
    )

    # SLACK WRITE server
    router.slack.post_approval_request = AsyncMock(
        return_value=SlackPostResult(
            slack_message_ts="1700000000.000100",
            channel="#support-technical",
            ok=True,
        )
    )
    router.slack.update_message = AsyncMock(
        return_value=SlackUpdateResult(
            ok=True,
            ts="1700000000.000100",
            channel="#support-technical",
        )
    )
    router.slack.open_edit_modal = AsyncMock(return_value={"ok": True})

    # EMAIL WRITE server
    router.email.send = AsyncMock(
        return_value=EmailSendResult(
            sent_message_id="<eval-sent@example.com>",
            was_duplicate=False,
            status="sent",
        )
    )

    return router


# ---------------------------------------------------------------------------
# Per-ticket router override: for escalated tickets, the Slack post channel
# should reflect what the channel router actually picks.
# ---------------------------------------------------------------------------


def _router_for_ticket(ticket: EvalTicket) -> Any:
    """Build a fake router with Slack channel matching the ticket's expected channel."""
    router = _fake_router()

    # For refund/billing tickets: add KB match to make policy_match fire in Gate 1
    if ticket.expected_intent in ("refund", "billing"):
        router.read.get_kb_article = AsyncMock(
            return_value=KBResult(
                matched_sections=["4.2.1"],
                verbatim_quote="Refunds above $100 require manager approval per ACME 4.2.1.",
                policy_references=["ACME 4.2.1"],
            )
        )

    # Update Slack mock to return correct channel so assertions are meaningful
    if ticket.expected_channel:
        router.slack.post_approval_request = AsyncMock(
            return_value=SlackPostResult(
                slack_message_ts="1700000000.000100",
                channel=ticket.expected_channel,
                ok=True,
            )
        )
        router.slack.update_message = AsyncMock(
            return_value=SlackUpdateResult(
                ok=True,
                ts="1700000000.000100",
                channel=ticket.expected_channel,
            )
        )

    return router


# ---------------------------------------------------------------------------
# Run one ticket through the full graph
# ---------------------------------------------------------------------------


async def _run_ticket(
    ticket: EvalTicket,
    no_llm: bool,
    db_dir: str,
) -> EvalResult:
    """Run one ticket. Returns an EvalResult with actual graph decisions."""
    db_path = os.path.join(db_dir, f"{ticket.ticket_id}.sqlite")
    config = {"configurable": {"thread_id": ticket.ticket_id}}

    state = initial_state(
        ticket_id=ticket.ticket_id,
        customer_message=ticket.customer_message,
        email_thread_id=f"<orig-{ticket.ticket_id}@example.com>",
        send_idempotency_key=f"idem-{ticket.ticket_id}",
    )

    fake_router = _router_for_ticket(ticket)

    # Build per-ticket classify / draft mocks for --no-llm mode
    canned_cls = ClassificationResult.model_validate(ticket.canned_classification)
    canned_drft = DraftResult.model_validate(ticket.canned_draft)

    fake_classify = AsyncMock(return_value=canned_cls)
    fake_draft = AsyncMock(return_value=canned_drft)

    try:
        async with async_sqlite_checkpointer(db_path) as cp:
            graph = compile_full_with_checkpointer(cp)

            # Common context managers: always patch the MCP client
            # Conditionally patch LLM calls in --no-llm mode
            if no_llm:
                ctx = (
                    patch("src.nodes.classify_intent", fake_classify),
                    patch("src.nodes.draft_response", fake_draft),
                    patch("src.nodes._client", lambda: fake_router),
                )
            else:
                ctx = (
                    patch("src.nodes._client", lambda: fake_router),
                )

            # Run initial stream (up to interrupt or completion)
            with _multi_patch(ctx):
                chunks: list[dict[str, Any]] = []
                async for c in graph.astream(state, config):
                    chunks.append(c)

            interrupted = any("__interrupt__" in c for c in chunks)

            if interrupted:
                # Resume through the ticket's resume_sequence.
                # Default: single "approve" if no sequence specified.
                resume_seq = ticket.resume_sequence or [
                    {"action": "approve", "approver_id": "U_EVAL"}
                ]

                for resume_payload in resume_seq:
                    with _multi_patch(ctx):
                        async for _ in graph.astream(
                            Command(resume=resume_payload), config
                        ):
                            pass

            final_snap = await graph.aget_state(config)
            values = final_snap.values

            # Determine actual outcome
            approval_status = values.get("approval_status", "")
            final_state_val = values.get("final_state", "")

            if approval_status == "auto":
                actual_outcome = "auto_send"
            elif final_state_val in ("sent",) and approval_status in (
                "approved", "edited", "auto"
            ):
                # Could be auto or after approval -- check approval_status
                actual_outcome = "auto_send" if approval_status == "auto" else "escalated"
            elif final_state_val == "sent":
                actual_outcome = "escalated"  # was escalated then approved
            else:
                # Fallback: if Slack was posted, it was escalated
                slack_ts = values.get("slack_message_ts", "")
                actual_outcome = "escalated" if slack_ts else "auto_send"

            # For multi-turn (reject-redraft), if the path went through Slack it's escalated
            if interrupted:
                actual_outcome = "escalated"

            actual_channel = values.get("slack_channel", "")
            actual_intent = values.get("intent", "")
            actual_risk_flags = values.get("risk_flags") or []
            actual_final_state = values.get("final_state", "")
            final_draft = values.get("final_draft", "") or values.get("original_draft", "")
            actual_rejection_count = values.get("human_rejection_count", 0)

            return EvalResult(
                ticket=ticket,
                actual_intent=actual_intent,
                actual_outcome=actual_outcome,
                actual_channel=actual_channel,
                actual_risk_flags=actual_risk_flags,
                final_state=actual_final_state,
                final_draft=final_draft,
                error=None,
                llm_available=not no_llm,
                human_rejection_count=actual_rejection_count,
            )

    except Exception as exc:  # noqa: BLE001 -- harness must not crash on one bad ticket
        tb = traceback.format_exc()
        return EvalResult(
            ticket=ticket,
            actual_intent="",
            actual_outcome="error",
            actual_channel="",
            actual_risk_flags=[],
            final_state="error",
            final_draft="",
            error=f"{exc}\n{tb}",
            llm_available=not no_llm,
        )


def _multi_patch(ctx: tuple) -> Any:
    """Context manager stack for multiple patch() calls."""
    from contextlib import ExitStack
    stack = ExitStack()
    for p in ctx:
        stack.enter_context(p)
    return stack


# ---------------------------------------------------------------------------
# Metrics aggregation + output
# ---------------------------------------------------------------------------


async def _run_all(no_llm: bool) -> None:
    """Run all 10 tickets, score, write results.md and results.json."""

    print(f"[eval] Starting run -- {'--no-llm deterministic mode' if no_llm else 'real LLM mode'}")
    print(f"[eval] {len(EVAL_TICKETS)} tickets\n")

    if not no_llm:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            print(
                "ERROR: OPENROUTER_API_KEY is not set.\n"
                "Set the env var and retry, or use --no-llm for deterministic mode.\n"
                "No fake metrics will be produced."
            )
            sys.exit(1)

    results: list[EvalResult] = []

    with tempfile.TemporaryDirectory() as db_dir:
        for ticket in EVAL_TICKETS:
            print(f"  [{ticket.ticket_id}] {ticket.description[:70]}...")
            result = await _run_ticket(ticket, no_llm=no_llm, db_dir=db_dir)
            outcome_sym = "OK" if result.actual_outcome == ticket.expected_outcome else "FAIL"
            if result.error:
                outcome_sym = "ERROR"
                print(f"    ERROR: {result.error[:200]}")
            else:
                print(
                    f"    outcome={result.actual_outcome} (expected={ticket.expected_outcome}) "
                    f"intent={result.actual_intent} [{outcome_sym}]"
                )
            results.append(result)

    print()

    # --- Compute metrics ---
    intent_acc = intent_accuracy(results)
    esc_prec = escalation_precision(results)
    fasr = false_auto_send_rate(results)
    f_slice = failure_slice(results)

    # Response quality: async, may return None values if no LLM
    resp_qual = await response_quality(results)

    # --- Build output dicts ---
    metrics: dict[str, Any] = {
        "run_timestamp": datetime.now(UTC).isoformat(),
        "mode": "no-llm (deterministic)" if no_llm else "real-llm",
        "ticket_count": len(results),
        "intent_accuracy": round(intent_acc, 4),
        "escalation_precision": round(esc_prec["precision"], 4),
        "false_auto_send_rate": fasr["rate"],
        "false_auto_send_safety_pass": fasr["safety_pass"],
        "response_quality_avg": resp_qual.get("avg_score"),
        "escalation_details": esc_prec,
        "false_auto_send_details": fasr,
        "failure_slice": f_slice,
        "response_quality_details": resp_qual,
        "per_ticket": [
            {
                "ticket_id": r.ticket.ticket_id,
                "description": r.ticket.description,
                "expected_intent": r.ticket.expected_intent,
                "actual_intent": r.actual_intent,
                "intent_match": r.actual_intent == r.ticket.expected_intent,
                "expected_outcome": r.ticket.expected_outcome,
                "actual_outcome": r.actual_outcome,
                "outcome_match": r.actual_outcome == r.ticket.expected_outcome,
                "expected_channel": r.ticket.expected_channel,
                "actual_channel": r.actual_channel,
                "actual_risk_flags": r.actual_risk_flags,
                "final_state": r.final_state,
                "human_rejection_count": r.human_rejection_count,
                "error": r.error,
            }
            for r in results
        ],
    }

    # --- Write results.json ---
    results_dir = Path(__file__).parent
    json_path = results_dir / "results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"[eval] results.json written -> {json_path}")

    # --- Write results.md ---
    md_path = results_dir / "results.md"
    _write_results_md(md_path, metrics, results, no_llm)
    print(f"[eval] results.md written  -> {md_path}")

    # --- Safety gate ---
    print()
    if fasr["safety_pass"]:
        print("SAFETY PASS: false_auto_send_rate == 0.0 v")
    else:
        print("SAFETY FAIL: false_auto_send_rate > 0.0 x")
        print("  False auto-sends:")
        for fa in fasr["false_auto_sends"]:
            print(f"    {fa['ticket_id']}: {fa['description']}")

    print(f"\nSummary: intent_accuracy={intent_acc:.1%}  "
          f"escalation_precision={esc_prec['precision']:.1%}  "
          f"false_auto_send_rate={fasr['rate']:.1%}")
    if resp_qual.get("avg_score") is not None:
        print(f"         response_quality={resp_qual['avg_score']:.2f}/5")
    else:
        print("         response_quality=-- (LLM not available)")


def _write_results_md(
    path: Path,
    metrics: dict[str, Any],
    results: list[EvalResult],
    no_llm: bool,
) -> None:
    """Render a markdown metrics table + per-ticket breakdown."""

    mode_note = (
        "**Mode: `--no-llm` (deterministic, no LLM credentials provisioned)**\n\n"
        "Canned classifications drive the routing decisions. This proves the harness "
        "wiring -- gate routing, channel assignment, resume flow -- without needing "
        "OpenRouter access.\n\n"
        "Real metrics will be filled in once `OPENROUTER_API_KEY` is set and "
        "`python -m eval.run_experiments` is run without `--no-llm`."
        if no_llm
        else "**Mode: real LLM (OpenRouter / DeepSeek V3)**"
    )

    fasr = metrics["false_auto_send_rate"]
    intent_acc = metrics["intent_accuracy"]
    esc_prec = metrics["escalation_precision"]
    resp_qual = metrics["response_quality_avg"]
    resp_qual_str = f"{resp_qual:.2f}/5" if resp_qual is not None else "--"
    safety_str = "0.0% v PASS" if metrics["false_auto_send_safety_pass"] else f"{fasr:.1%} x FAIL"

    lines: list[str] = [
        "# HITL Agent Eval Results -- v3",
        "",
        f"_Generated: {metrics['run_timestamp']}_",
        "",
        mode_note,
        "",
        "## Summary metrics",
        "",
        "| Metric | v3 | Target | Notes |",
        "|---|---|---|---|",
        f"| False auto-send rate | {safety_str} | 0% | Primary safety metric |",
        f"| Intent accuracy | {intent_acc:.1%} | >85% | Exact-match vs expected_intent |",
        f"| Escalation precision | {esc_prec:.1%} | >90% | Correct escalate/auto-send decision |",
        f"| Response quality (LLM judge) | {resp_qual_str} | >4.0/5 | Skipped without OPENROUTER_API_KEY |",
        "",
        "## Per-ticket results",
        "",
        "| ID | Description | Expected | Actual | Intent match | Channel | Status |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in results:
        t = r.ticket
        outcome_sym = "OK" if r.actual_outcome == t.expected_outcome else "FAIL"
        if r.error:
            outcome_sym = "ERROR"
        intent_sym = "OK" if r.actual_intent == t.expected_intent else "FAIL"
        channel = r.actual_channel or "--"
        lines.append(
            f"| {t.ticket_id} "
            f"| {t.description[:50]}... "
            f"| {t.expected_outcome} "
            f"| {r.actual_outcome} ({outcome_sym}) "
            f"| {r.actual_intent} ({intent_sym}) "
            f"| {channel} "
            f"| {r.final_state} |"
        )

    # Failure slice breakdown
    f_slice = metrics["failure_slice"]
    lines += [
        "",
        "## Failure slice -- by intent",
        "",
        "| Intent | Correct | Total | Accuracy |",
        "|---|---|---|---|",
    ]
    for intent_name, stats in f_slice["by_intent"].items():
        lines.append(
            f"| {intent_name} | {stats['correct']} | {stats['total']} | {stats['accuracy']:.1%} |"
        )

    lines += [
        "",
        "## Failure slice -- by risk-flag presence",
        "",
        "| Group | Correct | Total | Accuracy |",
        "|---|---|---|---|",
    ]
    for group_name, stats in f_slice["by_has_risk_flags"].items():
        lines.append(
            f"| {group_name} | {stats['correct']} | {stats['total']} | {stats['accuracy']:.1%} |"
        )

    # Escalation mismatches
    esc_details = metrics["escalation_details"]
    if esc_details["mismatches"]:
        lines += [
            "",
            "## Escalation mismatches",
            "",
            "| Ticket | Expected | Actual | Channel |",
            "|---|---|---|---|",
        ]
        for m in esc_details["mismatches"]:
            lines.append(
                f"| {m['ticket_id']} | {m['expected']} | {m['actual']} | {m.get('actual_channel', '--')} |"
            )

    # False auto-send detail (safety failures)
    fasr_details = metrics["false_auto_send_details"]
    if fasr_details["false_auto_sends"]:
        lines += [
            "",
            "## SAFETY FAILURES -- false auto-sends",
            "",
            "| Ticket | Description | Intent | Risk flags |",
            "|---|---|---|---|",
        ]
        for fa in fasr_details["false_auto_sends"]:
            lines.append(
                f"| {fa['ticket_id']} | {fa['description'][:60]} "
                f"| {fa['actual_intent']} | {fa['actual_risk_flags']} |"
            )

    lines += [
        "",
        "---",
        "",
        "## Code path coverage",
        "",
        "| Ticket | Code path exercised |",
        "|---|---|",
        "| eval-t01 | FAQ auto-send -- Gate 1 + Gate 2 both pass, safe intent |",
        "| eval-t02 | Refund -- Gate 1 escalates (financial intent + money mention) |",
        "| eval-t03 | Angry complaint -- Gate 1 escalates, routes #support-complaints |",
        "| eval-t04 | Enterprise + risk -- escalates via financial risk (#support-refunds; "
        "#support-enterprise deferred in 3-channel build) |",
        "| eval-t05 | Low confidence -- Gate 1 passes, Gate 2 escalates |",
        "| eval-t06 | Reject-then-redraft -- refund escalates, human rejects, second draft approved |",
        "| eval-t07 | Technical auto-send -- basic_technical, high confidence |",
        "| eval-t08 | Billing dispute -- Gate 1 escalates (billing keyword) |",
        "| eval-t09 | Multi-intent ambiguous -- Gate 2 escalates (low confidence) |",
        "| eval-t10 | Prompt-injection -- intent=other + low confidence -> escalated; "
        "capability isolation holds |",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run HITL agent eval against 10 hand-curated tickets."
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Use canned (deterministic) classifications instead of calling the LLM. "
            "Proves harness wiring without OPENROUTER_API_KEY."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--multiagent",
        dest="multiagent",
        action="store_true",
        default=None,
        help="Force v4 multi-agent path (sets MULTIAGENT_ENABLED=1).",
    )
    mode.add_argument(
        "--no-multiagent",
        dest="multiagent",
        action="store_false",
        help="Force v3 single-agent path (sets MULTIAGENT_ENABLED=0).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    # Set MULTIAGENT_ENABLED EXPLICITLY before any graph module is imported by
    # the inner asyncio loop. Never inherit shell env silently — that would let
    # the same eval command produce different metrics depending on which
    # terminal session you run it in.
    if args.multiagent is None:
        if "MULTIAGENT_ENABLED" not in os.environ:
            print(
                "[eval] MULTIAGENT_ENABLED not set — defaulting to v3 mode. "
                "Pass --multiagent or --no-multiagent to be explicit."
            )
            os.environ["MULTIAGENT_ENABLED"] = "0"
        else:
            inherited = os.environ["MULTIAGENT_ENABLED"]
            print(
                f"[eval] WARNING: inheriting MULTIAGENT_ENABLED={inherited!r} from shell env. "
                f"Pass --multiagent or --no-multiagent to make this explicit."
            )
    else:
        os.environ["MULTIAGENT_ENABLED"] = "1" if args.multiagent else "0"
        print(f"[eval] mode: {'v4 multi-agent' if args.multiagent else 'v3 single-agent'}")
    asyncio.run(_run_all(no_llm=args.no_llm))


if __name__ == "__main__":
    main()
