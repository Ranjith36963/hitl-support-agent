"""LLM client + LangSmith tracing.

Single model: DeepSeek V3 via OpenRouter (OpenAI-compatible API). Every LLM
call is wrapped with `@traceable` so LangSmith captures inputs, outputs, cost,
and tokens for failure-slice analysis.

Three call sites in the graph:
- `classify_intent` — Step 4 of nodes
- `draft_response` — Step 6 of nodes
- `summarize_context_changes` — Revalidate slow path

Spec sources: spec.md §6 (Nodes) + §9 (Evals tag set) + architecture.md
"LangSmith tagging".
"""

from __future__ import annotations

import json
import os
from typing import Any

from langsmith import traceable
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Client + config
# ---------------------------------------------------------------------------


def _client() -> AsyncOpenAI:
    """Build an OpenAI-shaped client pointed at OpenRouter.

    Lazy-constructed so importing this module doesn't fail when env vars are
    not set yet (matters for tests and for module-level imports during
    sub-agent work before secrets are provisioned).
    """
    return AsyncOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _model() -> str:
    return os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")


# ---------------------------------------------------------------------------
# Pydantic response shapes — strict so we can rely on field types downstream.
# ---------------------------------------------------------------------------


class ClassificationResult(BaseModel):
    intent: str = Field(description="One of: refund | technical | billing | complaint | FAQ | other")
    intent_confidence: float = Field(ge=0.0, le=1.0)
    sentiment: str = Field(description="One of: angry | neutral | positive")
    risk_flags: list[str] = Field(default_factory=list)
    risk_level: str = Field(description="One of: none | financial | legal | compliance")


class DraftResult(BaseModel):
    draft: str
    draft_confidence: float = Field(ge=0.0, le=1.0)


class ContextDelta(BaseModel):
    has_changes: bool
    summary: str = ""
    changed_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts — kept inline so prompt iteration is one diff away from a graph
# change. Move to a prompts/ folder if they grow > 30 lines each.
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = """You are a strict classifier for customer support tickets.
Output ONLY a single JSON object matching this schema, no preamble or trailing text:
{
  "intent":  one of: "refund" | "billing" | "complaint" | "technical" | "basic_technical" | "FAQ" | "info" | "other",
  "intent_confidence": 0.0-1.0,
  "sentiment": "angry" | "neutral" | "positive",
  "risk_flags": ["refund", "billing", "angry", "legal", "compliance", ...],
  "risk_level": "none" | "financial" | "legal" | "compliance"
}

Real customer messages are often informal, with typos, missing words and poor
grammar ("ur", "cant", "hoow", "plz", "methoids"). Classify by INTENT, not by
spelling — do not lower confidence just because the wording is messy.

Intent label definitions (pick the most specific match):
- "refund"          — customer wants money BACK: refund, reimbursement, compensation, charge reversal
- "billing"         — a payment/charge PROBLEM: failed payment, "can't pay", card declined,
                      double / wrong charge, invoice issue, subscription or plan change
- "complaint"       — dissatisfaction, anger, or filing a claim / grievance
- "technical"       — something is BROKEN (crash, error, bug, not working)
- "basic_technical" — simple product-feature HOW-TO ("how do I export", "where is the X setting") — nothing broken
- "FAQ"             — common account-procedure question: password / PIN reset, login help,
                      account / subscription / newsletter management
- "info"            — general factual question about the product or company (pricing, hours,
                      which payment methods exist, policy summaries) — no change to the account
- "other"           — does not fit cleanly, or the message is genuinely unclear

Disambiguation rules — these labels overlap; apply in this order:
- A payment that FAILED or is WRONG (declined, double-charged) → "billing".
  A question about WHICH payment methods exist → "info". Money the customer
  wants BACK → "refund".
- A simple "how do I..." question: account / login / subscription procedure
  → "FAQ"; product-feature how-to → "basic_technical".
- "info" asks "what is X / do you offer X" with no account change. "FAQ" and
  "basic_technical" ask "how do I do X".
- Something broken → "technical". Nothing broken, just a question → FAQ /
  basic_technical / info.

Other rules:
- "refund" intent → risk_flags contains "refund", risk_level="financial".
- "billing" intent → risk_flags contains "billing", risk_level="financial".
- Mentions of lawyer / lawsuit / legal action → risk_flags contains "legal", risk_level="legal".
- Sentiment "angry" → risk_flags contains "angry".
- Be conservative with confidence — values under 0.85 force human review.

Examples (note the deliberate typos — classify by intent anyway):

Input: "how do i change the email adress on my acount?"
Output: {"intent":"FAQ","intent_confidence":0.93,"sentiment":"neutral","risk_flags":[],"risk_level":"none"}

Input: "cant find the buton to export my report to csv"
Output: {"intent":"basic_technical","intent_confidence":0.9,"sentiment":"neutral","risk_flags":[],"risk_level":"none"}

Input: "Your app crashes every time I open the dashboard."
Output: {"intent":"technical","intent_confidence":0.93,"sentiment":"neutral","risk_flags":[],"risk_level":"none"}

Input: "i think i got charged twice for last month, can u check"
Output: {"intent":"billing","intent_confidence":0.89,"sentiment":"neutral","risk_flags":["billing"],"risk_level":"financial"}

Input: "I want a $200 refund — the laptop arrived damaged."
Output: {"intent":"refund","intent_confidence":0.96,"sentiment":"neutral","risk_flags":["refund"],"risk_level":"financial"}

Input: "This is the third time! I'm calling my lawyer."
Output: {"intent":"complaint","intent_confidence":0.93,"sentiment":"angry","risk_flags":["angry","legal"],"risk_level":"legal"}

Input: "wat are ur support hours on weekends?"
Output: {"intent":"info","intent_confidence":0.91,"sentiment":"neutral","risk_flags":[],"risk_level":"none"}"""


DRAFT_SYSTEM = """You write customer-support replies that sound human and are policy-grounded.

Inputs you'll see:
- The customer message (PII tokens like [EMAIL_1] are placeholders that will be restored later — leave them as-is)
- Customer profile + history
- Relevant policy quotes from the company knowledge base
- A prior rejection reason if the human reviewer rejected an earlier draft

Rules:
- Ground concrete claims (refund eligibility, SLA times) in the supplied policy quotes. If a claim isn't in the supplied policy, don't make it.
- Tone: warm, concise, no boilerplate.
- If a prior rejection_reason is supplied, address it directly.
- Output ONLY a single JSON object: {"draft": "...", "draft_confidence": 0.0-1.0}."""


SUMMARIZE_CHANGES_SYSTEM = """You compute a structured delta between two customer context snapshots.
Output ONLY a single JSON object: {"has_changes": bool, "summary": "...", "changed_fields": [...]}.
Only flag changes that meaningfully affect a support reply (subscription tier, account status, billing state, ticket counts). Ignore noise."""


# ---------------------------------------------------------------------------
# Calls — each `@traceable` so LangSmith captures the trace tree.
# ---------------------------------------------------------------------------


def _ls_metadata(state: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build LangSmith metadata dict from AgentState. Aligns with the tag
    table in architecture.md but ships only the v3 subset per adviserplan.md."""
    md: dict[str, Any] = {
        "graph_version": state.get("graph_version", "v3"),
        "ticket_id": state.get("ticket_id", ""),
        "intent": state.get("intent", ""),
    }
    if extra:
        md.update(extra)
    return md


async def _chat_json(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Call the model and parse a JSON object out of the response.

    DeepSeek follows OpenAI's response_format JSON-mode well. Falls back to
    raw .strip() parsing when response_format isn't honored.
    """
    resp = await _client().chat.completions.create(
        model=_model(),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = (resp.choices[0].message.content or "").strip()
    return json.loads(content)


@traceable(run_type="llm", name="classify_intent")
async def classify_intent(customer_message_redacted: str) -> ClassificationResult:
    data = await _chat_json(
        [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": customer_message_redacted},
        ]
    )
    return ClassificationResult.model_validate(data)


@traceable(run_type="llm", name="draft_response")
async def draft_response(
    customer_message_redacted: str,
    intent: str,
    customer_profile: dict[str, Any],
    customer_history: list[dict[str, Any]],
    policy_quotes: list[str],
    rejection_reason: str | None = None,
) -> DraftResult:
    user_block: dict[str, Any] = {
        "customer_message": customer_message_redacted,
        "intent": intent,
        "customer_profile": customer_profile,
        "customer_history": customer_history,
        "policy_quotes": policy_quotes,
    }
    if rejection_reason:
        user_block["prior_rejection_reason"] = rejection_reason

    data = await _chat_json(
        [
            {"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": json.dumps(user_block, ensure_ascii=False)},
        ]
    )
    return DraftResult.model_validate(data)


@traceable(run_type="llm", name="summarize_context_changes")
async def summarize_context_changes(
    old_snapshot: dict[str, Any], new_snapshot: dict[str, Any]
) -> ContextDelta:
    data = await _chat_json(
        [
            {"role": "system", "content": SUMMARIZE_CHANGES_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {"old": old_snapshot, "new": new_snapshot}, ensure_ascii=False
                ),
            },
        ]
    )
    return ContextDelta.model_validate(data)


__all__ = [
    "ClassificationResult",
    "ContextDelta",
    "DraftResult",
    "_ls_metadata",
    "classify_intent",
    "draft_response",
    "summarize_context_changes",
]
