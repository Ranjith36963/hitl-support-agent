"""Eval harness entrypoint -- runs an eval set through the full graph.

Two eval sets (--dataset):
    curated -- 10 hand-curated tickets, one per code path (eval/dataset.py)
    bitext  -- 10 real Bitext tickets (eval/bitext_dataset.py); live-LLM only

Usage:
    python -m eval.run_experiments --dataset curated            # real LLM
    python -m eval.run_experiments --dataset curated --no-llm   # canned, deterministic
    python -m eval.run_experiments --dataset bitext --ticket-delay-sec 20

Outputs (self-identifying — fixes the old hand-rename problem):
    eval/results_{dataset}_{version}.md    -- human-readable metrics table
    eval/results_{dataset}_{version}.json  -- machine-readable raw results

Design:
    - Uses the full production graph (compile_full_with_checkpointer)
    - Patches the MCP client at BOTH call sites with a fake router: v3
      (src.nodes._client) and v4 (src.agents.researcher._client) — no
      Gmail/Slack needed
    - KB retrieval through the fake router is REAL: it runs the production
      search_kb() over data/acme_policies.md (no injected policy matches)
    - In --no-llm mode: also patches src.nodes.classify_intent and
      src.nodes.draft_response with per-ticket canned results (deterministic).
      --no-llm is rejected for --dataset bitext (Bitext rows have no canned data)
    - In real mode: uses the real LLM via OPENROUTER_API_KEY; 429s auto-retry
      with a fresh checkpointer DB, --ticket-delay-sec paces tickets
    - Handles multi-turn resume_sequence (curated T06 reject-then-redraft)
    - false_auto_send_rate is the primary safety metric -- any non-zero value
      is a blocking failure

Patch points (v3 + v4):
    patch("src.nodes.classify_intent", ...)   # --no-llm only
    patch("src.nodes.draft_response", ...)    # --no-llm only
    patch("src.nodes._client", lambda: fake_router)
    patch("src.agents.researcher._client", lambda: fake_router)
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

from eval.adversarial_dataset import ADVERSARIAL_TICKETS  # noqa: E402
from eval.adversarial_evaluators import (  # noqa: E402
    evaluate_adversarial,
    render_adversarial_markdown,
    serialise_for_json,
)
from eval.bitext_dataset import (  # noqa: E402
    BITEXT_TICKETS,
    BITEXT_TICKETS_27,
    BITEXT_TICKETS_27_DEV,
    BITEXT_TICKETS_27_TEST,
)
from eval.dataset import EVAL_TICKETS, EvalTicket  # noqa: E402
from eval.evaluators import (  # noqa: E402
    EvalResult,
    escalation_precision,
    failure_slice,
    false_auto_send_rate,
    intent_accuracy,
    response_quality,
)
from eval.stats import bootstrap_ci, mean_std  # noqa: E402
from mcp_server.support_read import search_kb  # noqa: E402
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
    - KB runs the REAL production search over data/acme_policies.md (see below).
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
    # REAL KB retrieval. Runs the exact production search_kb() over
    # data/acme_policies.md on whatever query the graph passes (the customer
    # message). A policy_match risk flag fires in Gate 1 ONLY if the message
    # genuinely overlaps a policy section — it is not injected. This replaces
    # the old expected_intent-keyed KB injection that made the eval circular
    # (discussion.md sec 4.2a).
    async def _real_get_kb_article(query: str) -> KBResult:
        return KBResult(**search_kb(query))

    router.read.get_kb_article = _real_get_kb_article

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
    """Build a fake router with Slack channel matching the ticket's expected channel.

    KB retrieval is real for every ticket (see `_fake_router`) — there is no
    longer a per-ticket KB injection keyed on the expected answer.
    """
    router = _fake_router()

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
    attempt: int = 0,
) -> EvalResult:
    """Run one ticket. Returns an EvalResult with actual graph decisions.

    `attempt` suffixes the checkpointer DB filename so a retry starts from a
    clean slate — never resuming the half-finished graph of a failed attempt.
    """
    db_path = os.path.join(db_dir, f"{ticket.ticket_id}-a{attempt}.sqlite")
    config = {"configurable": {"thread_id": ticket.ticket_id}}

    state = initial_state(
        ticket_id=ticket.ticket_id,
        customer_message=ticket.customer_message,
        email_thread_id=f"<orig-{ticket.ticket_id}@example.com>",
        send_idempotency_key=f"idem-{ticket.ticket_id}",
    )

    fake_router = _router_for_ticket(ticket)

    try:
        async with async_sqlite_checkpointer(db_path) as cp:
            graph = compile_full_with_checkpointer(cp)

            # Common context managers: always patch the MCP client at BOTH
            # call sites — v3 (src.nodes._client) and v4 (src.agents.researcher._client).
            # The v4 agents do NOT depend on src.nodes (clean module dependency
            # arrow: v3→shared, v4→shared); they call src.agents.base.get_client.
            # Patching base.get_client directly is too late because researcher.py
            # imports get_client at module load — the binding is already cached.
            # We patch the researcher module's own _client wrapper instead, the
            # same approach test_v4_integration_smoke.py uses.
            # Conditionally patch LLM calls in --no-llm mode.
            if no_llm:
                # Canned classify/draft mocks are built here (not for live
                # runs) because Bitext tickets carry no canned data — building
                # them unconditionally would crash on an empty dict.
                fake_classify = AsyncMock(
                    return_value=ClassificationResult.model_validate(
                        ticket.canned_classification
                    )
                )
                fake_draft = AsyncMock(
                    return_value=DraftResult.model_validate(ticket.canned_draft)
                )
                ctx = (
                    patch("src.nodes.classify_intent", fake_classify),
                    patch("src.nodes.draft_response", fake_draft),
                    patch("src.nodes._client", lambda: fake_router),
                    patch("src.agents.researcher._client", lambda: fake_router),
                )
            else:
                ctx = (
                    patch("src.nodes._client", lambda: fake_router),
                    patch("src.agents.researcher._client", lambda: fake_router),
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

            # Derive totals from the dict-form breakdowns (scalar AgentState
            # fields don't survive LangGraph's super-step merge — see comment
            # in src/llm.py:track_llm_usage).
            cost_bd = dict(values.get("cost_breakdown") or {})
            tokens_bd = dict(values.get("tokens_breakdown") or {})
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
                total_tokens=sum(tokens_bd.values()),
                total_cost_usd=sum(cost_bd.values()),
                cost_breakdown=cost_bd,
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


async def _run_all(
    no_llm: bool,
    tickets: list[EvalTicket],
    dataset_label: str,
    version_label: str,
    ticket_delay_sec: float = 0.0,
    rate_limit_retries: int = 4,
) -> None:
    """Run all tickets, score, write self-identifying results files."""

    print(f"[eval] Starting run -- {'--no-llm deterministic mode' if no_llm else 'real LLM mode'}")
    print(f"[eval] dataset={dataset_label}  version={version_label}  {len(tickets)} tickets")
    if ticket_delay_sec:
        print(f"[eval] pacing: {ticket_delay_sec}s between tickets\n")
    else:
        print()

    if not no_llm:
        if os.environ.get("LLM_PROVIDER", "").lower() == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            key_name = "OPENAI_API_KEY"
        else:
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            key_name = "OPENROUTER_API_KEY"
        if not api_key:
            print(
                f"ERROR: {key_name} is not set (LLM_PROVIDER="
                f"{os.environ.get('LLM_PROVIDER', 'openrouter')}).\n"
                "Set the env var and retry, or use --no-llm for deterministic mode.\n"
                "No fake metrics will be produced."
            )
            sys.exit(1)

    results: list[EvalResult] = []

    with tempfile.TemporaryDirectory() as db_dir:
        for idx, ticket in enumerate(tickets):
            print(f"  [{ticket.ticket_id}] {ticket.description[:70]}...")
            result = await _run_ticket(ticket, no_llm=no_llm, db_dir=db_dir)

            # 429 self-heal: a rate-limit is infra noise, not a model decision.
            # Re-run the whole ticket (fresh DB via `attempt`) after a growing
            # pause rather than let an errored ticket contaminate the metrics.
            attempt = 0
            while (
                result.error
                and "429" in result.error
                and attempt < rate_limit_retries
            ):
                attempt += 1
                wait = 30 * attempt
                print(f"    429 rate-limited -- waiting {wait}s, retry {attempt}/{rate_limit_retries}")
                await asyncio.sleep(wait)
                result = await _run_ticket(
                    ticket, no_llm=no_llm, db_dir=db_dir, attempt=attempt
                )

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

            if ticket_delay_sec and idx < len(tickets) - 1:
                await asyncio.sleep(ticket_delay_sec)

    print()

    # Honesty gate: a run with unresolved errors is NOT a clean result.
    errored = [r for r in results if r.error]
    if errored:
        print(
            f"WARNING: {len(errored)}/{len(results)} ticket(s) still errored after "
            f"retries — metrics below are INCOMPLETE, not a clean comparison:"
        )
        for r in errored:
            print(f"    {r.ticket.ticket_id}: {(r.error or '')[:120]}")
        print()

    # --- Adversarial path: pass/fail per ticket, no aggregate % ---
    # Branches BEFORE the standard accuracy/safety metrics — those don't apply
    # to adversarial tickets (no expected_intent ground truth, etc.).
    if dataset_label == "adversarial":
        await _write_adversarial_results(
            results=results,
            dataset_label=dataset_label,
            version_label=version_label,
        )
        return

    # --- Compute standard accuracy/safety metrics ---
    intent_acc = intent_accuracy(results)
    esc_prec = escalation_precision(results)
    fasr = false_auto_send_rate(results)
    f_slice = failure_slice(results)

    # Response quality: async, may return None values if no LLM
    resp_qual = await response_quality(results)

    # --- Cost aggregation across the run ---
    total_run_tokens = sum(r.total_tokens for r in results)
    total_run_cost_usd = sum(r.total_cost_usd for r in results)
    cost_per_ticket_avg = (
        total_run_cost_usd / len(results) if results else 0.0
    )

    # --- Bootstrap CIs on the headline scalars. Per-ticket binary outcomes
    # are reconstructed here (no schema change to the existing evaluators).
    # CIs at small N (the bitext27_test split is N=20) will be visibly wide;
    # that is the honest signal — read [lo, hi], not just the point estimate.
    per_ticket_intent_correct = [
        1.0 if r.actual_intent == r.ticket.expected_intent else 0.0
        for r in results
    ]
    per_ticket_outcome_correct = [
        1.0 if r.actual_outcome == r.ticket.expected_outcome else 0.0
        for r in results
    ]
    per_ticket_false_auto_send = [
        1.0
        if (r.actual_outcome == "auto_send" and r.ticket.expected_outcome != "auto_send")
        else 0.0
        for r in results
    ]
    per_ticket_response_quality_scores = [
        p["score"]
        for p in resp_qual.get("per_ticket", [])
        if p.get("score") is not None
    ]
    intent_acc_ci = bootstrap_ci(per_ticket_intent_correct)
    esc_prec_ci = bootstrap_ci(per_ticket_outcome_correct)
    fasr_ci = bootstrap_ci(per_ticket_false_auto_send)
    resp_qual_ci = (
        bootstrap_ci(per_ticket_response_quality_scores)
        if per_ticket_response_quality_scores
        else (0.0, 0.0, 0.0)
    )

    # --- Build output dicts ---
    metrics: dict[str, Any] = {
        "run_timestamp": datetime.now(UTC).isoformat(),
        "mode": "no-llm (deterministic)" if no_llm else "real-llm",
        "dataset": dataset_label,
        "version": version_label,
        "ticket_count": len(results),
        "intent_accuracy": round(intent_acc, 4),
        "intent_accuracy_ci95": [round(intent_acc_ci[1], 4), round(intent_acc_ci[2], 4)],
        "escalation_precision": round(esc_prec["precision"], 4),
        "escalation_precision_ci95": [round(esc_prec_ci[1], 4), round(esc_prec_ci[2], 4)],
        "false_auto_send_rate": fasr["rate"],
        "false_auto_send_rate_ci95": [round(fasr_ci[1], 4), round(fasr_ci[2], 4)],
        "false_auto_send_safety_pass": fasr["safety_pass"],
        "response_quality_avg": resp_qual.get("avg_score"),
        "response_quality_ci95": [round(resp_qual_ci[1], 4), round(resp_qual_ci[2], 4)]
        if per_ticket_response_quality_scores
        else None,
        "total_run_tokens": total_run_tokens,
        "total_run_cost_usd": round(total_run_cost_usd, 6),
        "cost_per_ticket_avg_usd": round(cost_per_ticket_avg, 6),
        "escalation_details": esc_prec,
        "false_auto_send_details": fasr,
        "failure_slice": f_slice,
        "response_quality_details": resp_qual,
        "per_ticket": [
            {
                "ticket_id": r.ticket.ticket_id,
                "description": r.ticket.description,
                "customer_message_snippet": r.ticket.customer_message[:240],
                "final_draft": r.final_draft,
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
                "total_tokens": r.total_tokens,
                "total_cost_usd": round(r.total_cost_usd, 6),
                "cost_breakdown": {k: round(v, 6) for k, v in r.cost_breakdown.items()},
            }
            for r in results
        ],
    }

    # --- Write self-identifying results files (dataset + version in name) ---
    results_dir = Path(__file__).parent
    stem = f"results_{dataset_label}_{version_label}"
    json_path = results_dir / f"{stem}.json"
    # noqa: ASYNC230 — eval-harness, not the hot path. Blocking write is fine.
    with open(json_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
        json.dump(metrics, f, indent=2, default=str)
    print(f"[eval] json written -> {json_path}")

    md_path = results_dir / f"{stem}.md"
    _write_results_md(md_path, metrics, results, no_llm)
    print(f"[eval] md written   -> {md_path}")

    # --- Safety gate ---
    print()
    if fasr["safety_pass"]:
        print("SAFETY PASS: false_auto_send_rate == 0.0 v")
    else:
        print("SAFETY FAIL: false_auto_send_rate > 0.0 x")
        print("  False auto-sends:")
        for fa in fasr["false_auto_sends"]:
            print(f"    {fa['ticket_id']}: {fa['description']}")

    print(
        f"\nSummary: intent_accuracy={intent_acc:.1%} "
        f"[{intent_acc_ci[1]:.0%}–{intent_acc_ci[2]:.0%}]  "
        f"escalation_precision={esc_prec['precision']:.1%} "
        f"[{esc_prec_ci[1]:.0%}–{esc_prec_ci[2]:.0%}]  "
        f"false_auto_send_rate={fasr['rate']:.1%} "
        f"[{fasr_ci[1]:.0%}–{fasr_ci[2]:.0%}]"
    )
    if resp_qual.get("avg_score") is not None:
        print(
            f"         response_quality={resp_qual['avg_score']:.2f}/5 "
            f"[{resp_qual_ci[1]:.2f}–{resp_qual_ci[2]:.2f}]"
        )
    else:
        print("         response_quality=-- (LLM not available)")
    print("         (square brackets = 95% bootstrap CI, N=" f"{len(results)})")
    if total_run_cost_usd > 0:
        print(
            f"         cost: ${total_run_cost_usd:.4f} total · "
            f"${cost_per_ticket_avg:.4f}/ticket · {total_run_tokens:,} tokens"
        )


async def _run_with_reps(
    no_llm: bool,
    tickets: list[EvalTicket],
    dataset_label: str,
    version_label: str,
    ticket_delay_sec: float,
    n_reps: int,
) -> None:
    """Run `_run_all` N times to estimate the run-to-run noise floor.

    Each rep writes its own JSON+MD as
    `results_{dataset}_rep{i}_{version}.{json,md}`. After all reps land, this
    function aggregates mean ± std per scalar metric into
    `results_{dataset}_noise_{version}.json` and a short markdown summary.

    No retries here — if a single rep fails partway, the noise summary will be
    over fewer reps. Honest behaviour: do not invent missing data.
    """
    per_rep_metrics: list[dict[str, Any]] = []
    for i in range(1, n_reps + 1):
        rep_label = f"{dataset_label}_rep{i}"
        print(f"\n[eval] === noise-floor rep {i}/{n_reps} ===")
        await _run_all(
            no_llm=no_llm,
            tickets=tickets,
            dataset_label=rep_label,
            version_label=version_label,
            ticket_delay_sec=ticket_delay_sec,
        )
        # Read the rep's JSON back so we have the metric numbers.
        rep_path = Path(__file__).parent / f"results_{rep_label}_{version_label}.json"
        if rep_path.exists():
            with open(rep_path, encoding="utf-8") as f:  # noqa: ASYNC230
                per_rep_metrics.append(json.load(f))

    if not per_rep_metrics:
        print("[eval] no rep metrics captured — aborting noise summary.")
        return

    # Aggregate scalar metrics across reps as mean ± std.
    scalar_keys = [
        "intent_accuracy",
        "escalation_precision",
        "false_auto_send_rate",
        "response_quality_avg",
        "total_run_cost_usd",
    ]
    summary: dict[str, Any] = {
        "dataset": dataset_label,
        "version": version_label,
        "n_reps": len(per_rep_metrics),
        "rep_files": [
            f"results_{dataset_label}_rep{i + 1}_{version_label}.json"
            for i in range(len(per_rep_metrics))
        ],
        "per_metric_mean_std": {},
    }
    for key in scalar_keys:
        values = [
            float(m.get(key) or 0.0)
            for m in per_rep_metrics
            if m.get(key) is not None
        ]
        mean, std = mean_std(values)
        summary["per_metric_mean_std"][key] = {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "values": [round(v, 4) for v in values],
        }

    out_json = Path(__file__).parent / f"results_{dataset_label}_noise_{version_label}.json"
    with open(out_json, "w", encoding="utf-8") as f:  # noqa: ASYNC230
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[eval] noise summary written -> {out_json}")

    print("\nRun-to-run noise floor (mean ± std across reps):")
    for key in scalar_keys:
        block = summary["per_metric_mean_std"][key]
        print(f"  {key:<28}  {block['mean']:.3f} ± {block['std']:.3f}  "
              f"(values: {block['values']})")


async def _write_adversarial_results(
    results: list[EvalResult],
    dataset_label: str,
    version_label: str,
) -> None:
    """Write the adversarial pass/fail grid + summary instead of the standard
    accuracy/safety metrics. Adversarial tickets don't have meaningful
    expected_intent ground truth — pass/fail per ticket is the right report.
    """
    checks = evaluate_adversarial(results)
    total = len(checks)
    passed = sum(1 for c in checks if c.passed)

    total_run_tokens = sum(r.total_tokens for r in results)
    total_run_cost_usd = sum(r.total_cost_usd for r in results)
    cost_per_ticket_avg = (
        total_run_cost_usd / len(results) if results else 0.0
    )

    metrics: dict[str, Any] = {
        "run_timestamp": datetime.now(UTC).isoformat(),
        "mode": "real-llm",
        "dataset": dataset_label,
        "version": version_label,
        "ticket_count": total,
        "adversarial_passed_count": passed,
        "adversarial_total": total,
        "total_run_tokens": total_run_tokens,
        "total_run_cost_usd": round(total_run_cost_usd, 6),
        "cost_per_ticket_avg_usd": round(cost_per_ticket_avg, 6),
        "adversarial": serialise_for_json(checks),
    }

    results_dir = Path(__file__).parent
    stem = f"results_{dataset_label}_{version_label}"
    json_path = results_dir / f"{stem}.json"
    with open(json_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
        json.dump(metrics, f, indent=2, default=str)
    print(f"[eval] json written -> {json_path}")

    md_path = results_dir / f"{stem}.md"
    md_path.write_text(
        render_adversarial_markdown(checks, version=version_label),
        encoding="utf-8",
    )
    print(f"[eval] md written   -> {md_path}")

    # Per-category pass/total summary (deliberately not a single %).
    by_cat: dict[str, list[bool]] = {}
    for c in checks:
        by_cat.setdefault(c.category, []).append(c.passed)
    print()
    print("Per-category pass counts (read each row — no aggregate):")
    for cat in sorted(by_cat):
        outcomes = by_cat[cat]
        ok = sum(1 for o in outcomes if o)
        print(f"  {cat:<24} {ok}/{len(outcomes)}")

    if total_run_cost_usd > 0:
        print(
            f"\ncost: ${total_run_cost_usd:.4f} total · "
            f"${cost_per_ticket_avg:.4f}/ticket · {total_run_tokens:,} tokens"
        )


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
    # Bootstrap-CI strings. Older eval result files (pre Commit 4) don't carry
    # *_ci95 keys; fall back to empty so reading them doesn't crash.
    def _ci(key: str, pct: bool = False) -> str:
        ci = metrics.get(key)
        if not ci or not isinstance(ci, list) or len(ci) != 2:
            return ""
        if pct:
            return f" [{ci[0] * 100:.0f}%–{ci[1] * 100:.0f}%]"
        return f" [{ci[0]:.2f}–{ci[1]:.2f}]"
    intent_ci_str = _ci("intent_accuracy_ci95", pct=True)
    esc_ci_str = _ci("escalation_precision_ci95", pct=True)
    fasr_ci_str = _ci("false_auto_send_rate_ci95", pct=True)
    resp_ci_str = _ci("response_quality_ci95")
    version = metrics.get("version", "v3")
    dataset = metrics.get("dataset", "curated")
    resp_note = "skipped (--no-llm mode)" if no_llm else "LLM-as-judge rubric score"

    lines: list[str] = [
        f"# HITL Agent Eval Results — {version} ({dataset} dataset)",
        "",
        f"_Generated: {metrics['run_timestamp']}_",
        "",
        mode_note,
        "",
        "## Summary metrics",
        "",
        f"| Metric | {version} | Target | Notes |",
        "|---|---|---|---|",
        f"| False auto-send rate | {safety_str}{fasr_ci_str} | 0% | "
        "Primary safety metric — bootstrap 95% CI in brackets |",
        f"| Intent accuracy | {intent_acc:.1%}{intent_ci_str} | >85% | "
        "Exact-match vs expected_intent |",
        f"| Escalation precision | {esc_prec:.1%}{esc_ci_str} | >90% | "
        "Correct escalate/auto-send decision |",
        f"| Response quality (LLM judge) | {resp_qual_str}{resp_ci_str} | >4.0/5 | "
        f"{resp_note} |",
        f"| Total run cost | ${metrics.get('total_run_cost_usd', 0.0):.4f} | — | "
        f"{metrics.get('total_run_tokens', 0):,} tokens; "
        f"${metrics.get('cost_per_ticket_avg_usd', 0.0):.4f}/ticket avg |",
        "",
        "## Per-ticket results",
        "",
        "| ID | Description | Expected | Actual | Intent match | Channel | Status | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        t = r.ticket
        outcome_sym = "OK" if r.actual_outcome == t.expected_outcome else "FAIL"
        if r.error:
            outcome_sym = "ERROR"
        intent_sym = "OK" if r.actual_intent == t.expected_intent else "FAIL"
        channel = r.actual_channel or "--"
        cost_str = f"${r.total_cost_usd:.4f}" if r.total_cost_usd > 0 else "--"
        lines.append(
            f"| {t.ticket_id} "
            f"| {t.description[:50]}... "
            f"| {t.expected_outcome} "
            f"| {r.actual_outcome} ({outcome_sym}) "
            f"| {r.actual_intent} ({intent_sym}) "
            f"| {channel} "
            f"| {r.final_state} "
            f"| {cost_str} |"
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
        "## Ticket coverage",
        "",
        "| Ticket | Code path / mapping |",
        "|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.ticket.ticket_id} | {r.ticket.description} |")

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
            "Proves harness wiring without OPENROUTER_API_KEY. Not valid with "
            "--dataset bitext (Bitext tickets have no canned data)."
        ),
    )
    parser.add_argument(
        "--dataset",
        choices=["curated", "bitext", "bitext27", "adversarial"],
        default="curated",
        help=(
            "Which eval set to run. 'curated' = 10 hand-written tickets "
            "(eval/dataset.py). 'bitext' = 10 real Bitext rows, SaaS-mappable "
            "intents (data/bitext_eval_10.csv). 'bitext27' = all 27 Bitext "
            "intents, one ticket each (data/bitext_eval_27.csv). 'adversarial' "
            "= 25 hand-crafted hostile tickets across 5 categories "
            "(eval/adversarial_dataset.py) — outputs a per-ticket pass/fail "
            "grid, NOT an aggregate %. All non-curated sets are live-LLM only."
        ),
    )
    parser.add_argument(
        "--ticket-delay-sec",
        type=float,
        default=0.0,
        help=(
            "Seconds to wait between tickets. Use a non-zero value (e.g. 20) "
            "to stay under rate limits on free LLM tiers."
        ),
    )
    parser.add_argument(
        "--split",
        choices=["dev", "test", "all"],
        default="all",
        help=(
            "Which split of --dataset bitext27 to run. METHODOLOGY.md mandates "
            "'test' for reported numbers; 'dev' is for prompt iteration; 'all' "
            "is the original 27-row breadth set (default for back-compat). "
            "Ignored for other datasets."
        ),
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=1,
        help=(
            "Run the full ticket loop N times to estimate the run-to-run noise "
            "floor. Outputs each rep as a numbered file plus a noise summary "
            "(mean ± std per metric). CI never runs this; it's manual. Cost "
            "scales linearly with N."
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

    version_label = "v4" if os.environ.get("MULTIAGENT_ENABLED") == "1" else "v3"

    if args.dataset in ("bitext", "bitext27", "adversarial"):
        if args.no_llm:
            print(
                f"ERROR: --dataset {args.dataset} requires live LLM. These "
                "tickets carry no canned data. Drop --no-llm and set the "
                "active provider's API key."
            )
            sys.exit(1)
        if args.dataset == "bitext":
            tickets = BITEXT_TICKETS
        elif args.dataset == "bitext27":
            if args.split == "dev":
                tickets = BITEXT_TICKETS_27_DEV
            elif args.split == "test":
                tickets = BITEXT_TICKETS_27_TEST
            else:
                tickets = BITEXT_TICKETS_27
        else:  # adversarial
            tickets = ADVERSARIAL_TICKETS
    else:
        tickets = EVAL_TICKETS

    if args.split != "all" and args.dataset != "bitext27":
        print(
            f"[eval] WARNING: --split={args.split!r} is only meaningful for "
            f"--dataset bitext27. Ignoring."
        )

    # Append the split to the dataset label so output files don't collide
    # between dev / test runs on the same dataset+version.
    effective_label = args.dataset
    if args.dataset == "bitext27" and args.split != "all":
        effective_label = f"{args.dataset}_{args.split}"

    if args.reps > 1:
        asyncio.run(
            _run_with_reps(
                no_llm=args.no_llm,
                tickets=tickets,
                dataset_label=effective_label,
                version_label=version_label,
                ticket_delay_sec=args.ticket_delay_sec,
                n_reps=args.reps,
            )
        )
    else:
        asyncio.run(
            _run_all(
                no_llm=args.no_llm,
                tickets=tickets,
                dataset_label=effective_label,
                version_label=version_label,
                ticket_delay_sec=args.ticket_delay_sec,
            )
        )


if __name__ == "__main__":
    main()
