"""Critic-intercept eval — measures what v4 is actually FOR.

The main eval (run_experiments.py) grades escalate-vs-auto-send. That axis is
structurally capped for v4: the Critic only lowers draft_confidence, so it can
never beat a v3 already at 100% escalation precision (see discussion.md §3,
docs/v4_multiagent.md "Why the Critic is ONE-DIRECTIONAL").

The Critic's real job is DRAFT QUALITY: catch a flawed draft and send it back
to the Drafter before any human or customer sees it. This script measures that
directly. It feeds the live Critic a fixed set of drafts — some deliberately
bad, some good — and reports:

  intercept_rate    — of the BAD drafts, how many did the Critic flag "revise"?
                      (higher is better — the Critic catching real problems)
  false_alarm_rate  — of the GOOD drafts, how many did the Critic wrongly
                      flag "revise"? (lower is better)

This is live-LLM only. It does NOT touch the safety gates — the Critic verdict
here only drives the Drafter loop, never auto-send.

Run:  python -m eval.critic_intercept
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.agents.critic import critic_node  # noqa: E402


@dataclass
class CriticCase:
    """One Critic test case — a draft the Critic should accept or revise."""

    case_id: str
    label: str  # "bad" (Critic SHOULD revise) | "good" (Critic should accept)
    customer_message: str
    draft: str
    intent: str
    why: str  # one-line rationale
    policy_matches: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixtures — 5 deliberately-bad drafts + 5 good controls.
# Bad drafts each hit a criterion from data/prompts/critic_system.md
# (unsupported claim / wrong tone / over-promise / contradicts profile).
# ---------------------------------------------------------------------------

CRITIC_CASES: list[CriticCase] = [
    CriticCase(
        case_id="bad-1-overpromise-refund",
        label="bad",
        customer_message="I was charged $480 for the annual plan but I cancelled. I want my money back.",
        draft=("Good news — I've gone ahead and fully refunded your $480. "
               "It will be back on your card within 24 hours, no approval needed!"),
        intent="refund",
        policy_matches=["ACME Refund Policy 4.2.1: refunds above $100 require manager approval."],
        why="Promises a refund and bypasses the manager-approval policy it was given.",
    ),
    CriticCase(
        case_id="bad-2-unsupported-sla",
        label="bad",
        customer_message="Your dashboard has been down for me all morning. When will it be fixed?",
        draft="I guarantee this will be completely fixed within the next 2 hours. You have my word.",
        intent="technical",
        policy_matches=[],
        why="Guarantees a specific SLA with zero policy support.",
    ),
    CriticCase(
        case_id="bad-3-wrong-tone-angry",
        label="bad",
        customer_message=("This is the THIRD time your billing system has overcharged me. "
                          "I am absolutely furious and I'm considering cancelling."),
        draft="Hi there! Thanks a million for reaching out — we just love hearing from our customers! Have an awesome day!",
        intent="complaint",
        policy_matches=[],
        why="Chirpy, upbeat tone to a furious customer.",
    ),
    CriticCase(
        case_id="bad-4-invented-policy",
        label="bad",
        customer_message="Can I get a discount for switching to annual billing?",
        draft=("Yes! Per ACME Policy 12.7, all annual switches receive an automatic "
               "30% loyalty discount, and I've already applied it to your account."),
        intent="billing",
        policy_matches=[],
        why="Cites a policy and a discount that appear in no supplied quote, and claims to have applied it.",
    ),
    CriticCase(
        case_id="bad-5-contradicts-profile",
        label="bad",
        customer_message="Do I get priority support on my plan?",
        draft="Absolutely — as one of our Enterprise customers you already have 24/7 priority support included.",
        intent="info",
        policy_matches=["ACME plans: priority support is included only on the Enterprise tier."],
        why="Asserts the customer is on Enterprise with no basis for that claim.",
    ),
    CriticCase(
        case_id="good-1-password-reset",
        label="good",
        customer_message="How do I reset my password?",
        draft=("You can reset your password from the login page — click 'Forgot password' "
               "and we'll email you a reset link. Let me know if it doesn't arrive within a few minutes."),
        intent="FAQ",
        policy_matches=[],
        why="Correct, polite, no claim beyond the standard reset flow.",
    ),
    CriticCase(
        case_id="good-2-refund-grounded",
        label="good",
        customer_message="I'd like a refund for the $300 I was charged last week.",
        draft=("Thanks for reaching out. Refunds above $100 require manager approval under our "
               "refund policy, so I've escalated your $300 request for review — you'll hear "
               "back within one business day."),
        intent="refund",
        policy_matches=["ACME Refund Policy 4.2.1: refunds above $100 require manager approval."],
        why="Grounded in the supplied policy; escalates instead of over-promising.",
    ),
    CriticCase(
        case_id="good-3-info-grounded",
        label="good",
        customer_message="What payment methods do you accept?",
        draft="We accept Visa, Mastercard, American Express, and PayPal. Let me know if you'd like help setting one up.",
        intent="info",
        policy_matches=["ACME accepts Visa, Mastercard, American Express, and PayPal."],
        why="Matches the supplied policy quote exactly.",
    ),
    CriticCase(
        case_id="good-4-empathetic-tone",
        label="good",
        customer_message="I've been waiting two days for a reply and I'm getting frustrated.",
        draft=("I'm really sorry for the delay and the frustration it's caused — that's not the "
               "experience we want for you. I'm picking this up now and will follow through until it's resolved."),
        intent="complaint",
        policy_matches=[],
        why="Acknowledges and apologizes with the right tone; no hard SLA promise.",
    ),
    CriticCase(
        case_id="good-5-howto-clean",
        label="good",
        customer_message="Where do I find the button to export my data to CSV?",
        draft=("You'll find CSV export under Settings -> Data -> Export. Click 'Export to CSV' "
               "and we'll email you the file. Happy to walk you through it if needed."),
        intent="basic_technical",
        policy_matches=[],
        why="Clear, helpful navigation answer; no refund/SLA/pricing claim.",
    ),
]


async def _judge_case(case: CriticCase, rate_limit_retries: int = 3) -> dict[str, Any]:
    """Run the live Critic on one case. Retries on 429 (infra noise, not a verdict)."""
    state: dict[str, Any] = {
        "ticket_id": case.case_id,
        "customer_message": case.customer_message,
        "final_draft": case.draft,
        "policy_matches": case.policy_matches,
        "intent": case.intent,
        "draft_confidence": 0.9,
        "_critic_iteration": 0,
        "audit_log": [],
    }
    attempt = 0
    while True:
        try:
            result = await critic_node(state)
            return {
                "verdict": result.get("_critic_verdict", "accept"),
                "severity": result.get("_critic_severity", 0.0),
                "feedback": result.get("_critic_feedback", ""),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 — one bad case must not kill the run
            if "429" in str(exc) and attempt < rate_limit_retries:
                attempt += 1
                wait = 30 * attempt
                print(f"    429 rate-limited — waiting {wait}s, retry {attempt}/{rate_limit_retries}")
                await asyncio.sleep(wait)
                continue
            return {"verdict": "error", "severity": 0.0, "feedback": "", "error": str(exc)}


async def _run(ticket_delay_sec: float = 15.0) -> None:
    """Run all Critic cases, score intercept + false-alarm, write results."""
    if not os.environ.get("OPENROUTER_API_KEY", ""):
        print("ERROR: OPENROUTER_API_KEY not set. This is a live-LLM eval. No fake metrics.")
        sys.exit(1)

    print(f"[critic-intercept] {len(CRITIC_CASES)} cases — live Critic\n")
    per_case: list[dict[str, Any]] = []

    for i, case in enumerate(CRITIC_CASES):
        print(f"  [{case.case_id}] ({case.label})")
        judged = await _judge_case(case)
        # A "bad" case is intercepted when the Critic says revise.
        # A "good" case is a false alarm when the Critic says revise.
        flagged = judged["verdict"] == "revise"
        correct = (
            flagged if case.label == "bad"
            else (not flagged) if case.label == "good"
            else False
        )
        if judged["error"]:
            print(f"    ERROR: {judged['error'][:160]}")
        else:
            mark = "OK" if correct else "MISS"
            print(f"    verdict={judged['verdict']} severity={judged['severity']} [{mark}]")
        per_case.append({
            "case_id": case.case_id,
            "label": case.label,
            "intent": case.intent,
            "why": case.why,
            "verdict": judged["verdict"],
            "severity": judged["severity"],
            "feedback": judged["feedback"],
            "correct": correct,
            "error": judged["error"],
        })
        if ticket_delay_sec and i < len(CRITIC_CASES) - 1:
            await asyncio.sleep(ticket_delay_sec)

    bad = [c for c in per_case if c["label"] == "bad"]
    good = [c for c in per_case if c["label"] == "good"]
    errored = [c for c in per_case if c["error"]]

    intercepted = sum(1 for c in bad if c["verdict"] == "revise")
    false_alarms = sum(1 for c in good if c["verdict"] == "revise")
    intercept_rate = intercepted / len(bad) if bad else 0.0
    false_alarm_rate = false_alarms / len(good) if good else 0.0

    metrics: dict[str, Any] = {
        "run_timestamp": datetime.now(UTC).isoformat(),
        "mode": "real-llm",
        "eval": "critic_intercept",
        "bad_count": len(bad),
        "good_count": len(good),
        "intercept_rate": round(intercept_rate, 4),
        "false_alarm_rate": round(false_alarm_rate, 4),
        "errored_cases": len(errored),
        "per_case": per_case,
    }

    out = _REPO_ROOT / "eval" / "results_critic_intercept.json"
    out.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    print()
    if errored:
        print(f"WARNING: {len(errored)} case(s) errored — metrics below are INCOMPLETE.")
    print(f"[critic-intercept] intercept_rate   = {intercept_rate:.0%}  "
          f"({intercepted}/{len(bad)} bad drafts caught)")
    print(f"[critic-intercept] false_alarm_rate = {false_alarm_rate:.0%}  "
          f"({false_alarms}/{len(good)} good drafts wrongly flagged)")
    print(f"[critic-intercept] results -> {out}")


if __name__ == "__main__":
    asyncio.run(_run())
