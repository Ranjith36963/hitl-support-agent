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
import time
from typing import TYPE_CHECKING, Any

from langsmith import traceable
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.metrics import LLM_LATENCY, LLM_TOKENS

if TYPE_CHECKING:
    from src.state import AgentState

# ---------------------------------------------------------------------------
# Pricing — per-1K-token USD costs. Verified 2026-05-22 from each provider's
# pricing page. Treat reported eval $ as approximate; update on provider drift.
# Unknown model_ids fall back to (0.0, 0.0) — cost reports as zero rather than
# crashing the run.
# ---------------------------------------------------------------------------

_PRICING: dict[str, tuple[float, float]] = {
    # ---- OpenAI direct ----
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    # ---- OpenRouter (model_id verbatim) ----
    "deepseek/deepseek-chat": (0.00014, 0.00028),
    # :free variants are billed at $0 by OpenRouter — only entry kept here
    # since the paid v4-flash row was unverified against any OpenRouter
    # pricing page (ultrareview bug_004). Unknown models fall through to
    # (0.0, 0.0) anyway so cost telemetry stays sane regardless.
    "deepseek/deepseek-v4-flash:free": (0.0, 0.0),
    "anthropic/claude-3.5-haiku": (0.0008, 0.004),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
}


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost for one OpenAI-shape response. Unknown model → 0.0 (logged as such)."""
    in_per_1k, out_per_1k = _PRICING.get(model, (0.0, 0.0))
    return (prompt_tokens / 1000.0) * in_per_1k + (completion_tokens / 1000.0) * out_per_1k


def track_llm_usage(
    state: AgentState | None,
    label: str,
    model: str,
    usage: Any,
) -> None:
    """Accumulate token + cost into AgentState. Safe to call with state=None.

    Mutates state in-place when supplied so callers don't need to merge a dict
    back through the LangGraph node-return pattern (cost fields are observability,
    not control flow). Two call paths exist:
      1. v3: `_chat_json` calls this after every response.
      2. v4: `src/agents/drafter._llm_draft` and `src/agents/critic._llm_judge`
         call this after their direct `client.chat.completions.create(...)`.

    Args:
        state: AgentState dict, or None when called from a test/mock path.
        label: short tag for cost_breakdown (e.g. "classify", "drafter", "critic").
        model: model id string used for the request — looked up in _PRICING.
        usage: OpenAI usage object (has `prompt_tokens` + `completion_tokens`).
    """
    if usage is None:
        return
    # Coerce to int defensively. Test mocks pass MagicMock objects whose
    # `.usage.prompt_tokens` is another MagicMock; an `or 0` short-circuit
    # treats MagicMock as truthy. Anything that isn't a plain int/float falls
    # through to 0 — same outcome as "no usage info available".
    raw_prompt = getattr(usage, "prompt_tokens", 0)
    raw_completion = getattr(usage, "completion_tokens", 0)
    prompt_tokens: int = int(raw_prompt) if isinstance(raw_prompt, (int, float)) else 0
    completion_tokens: int = (
        int(raw_completion) if isinstance(raw_completion, (int, float)) else 0
    )

    # Prometheus token counters fire on every LLM call — independent of
    # whether a state dict was supplied (so observability works for the
    # cross-judge script, tests, smoke probes too).
    LLM_TOKENS.labels(call=label, kind="prompt").inc(prompt_tokens)
    LLM_TOKENS.labels(call=label, kind="completion").inc(completion_tokens)

    if state is None:
        return
    cost = _compute_cost(model, prompt_tokens, completion_tokens)
    # Dict mutations survive LangGraph's super-step merge (the dict object is
    # shared by reference). Scalar reassignments don't — they get reverted when
    # the framework reconstructs state from each node's partial-return dict.
    # So we accumulate into dicts only; callers compute totals at read time.
    breakdown = state.setdefault("cost_breakdown", {})
    breakdown[label] = breakdown.get(label, 0.0) + cost
    tokens = state.setdefault("tokens_breakdown", {})
    tokens[label] = tokens.get(label, 0) + prompt_tokens + completion_tokens


# ---------------------------------------------------------------------------
# Client + config
# ---------------------------------------------------------------------------


def _client() -> AsyncOpenAI:
    """Build an OpenAI-shaped client.

    Provider switch (LLM_PROVIDER):
      - "openai"    -> OpenAI direct (uses OPENAI_API_KEY, default base_url)
      - anything else -> OpenRouter (default; uses OPENROUTER_API_KEY)

    Lazy-constructed so importing this module doesn't fail when env vars are
    not set yet (matters for tests and for module-level imports during
    sub-agent work before secrets are provisioned).
    """
    if os.environ.get("LLM_PROVIDER", "").lower() == "openai":
        return AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    return AsyncOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _model() -> str:
    if os.environ.get("LLM_PROVIDER", "").lower() == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
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
- Sign off as the **ACME Support team** (the company is ACME SaaS Co). Never use placeholder names like "[Your Name]", "[Agent Name]", or "[Support Rep]" — those are leaks of an unfilled template, not real signatures.
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


async def _chat_json(
    messages: list[dict[str, str]],
    *,
    label: str = "other",
    state: AgentState | None = None,
) -> dict[str, Any]:
    """Call the model and parse a JSON object out of the response.

    DeepSeek follows OpenAI's response_format JSON-mode well. Falls back to
    raw .strip() parsing when response_format isn't honored.

    `state` is optional — when supplied, the cost+token telemetry is folded
    back into AgentState via `track_llm_usage`. Tests that mock the LLM call
    pass `state=None` and the helper short-circuits.
    """
    model = _model()
    start = time.monotonic()
    try:
        # The OpenAI SDK's overloads on `create` require TypedDict-shaped
        # messages and response_format. The plain-dict literals below are
        # what the API actually expects; mypy strict mode can't narrow
        # `dict[str, str]` to ResponseFormatJSONObjectParam without an
        # explicit cast or a typed param. The call-overload ignore is the
        # honest workaround — no behaviour change.
        resp = await _client().chat.completions.create(  # type: ignore[call-overload]
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    finally:
        LLM_LATENCY.labels(call=label).observe(time.monotonic() - start)
    track_llm_usage(state, label, model, getattr(resp, "usage", None))
    content = (resp.choices[0].message.content or "").strip()
    parsed: dict[str, Any] = json.loads(content)
    return parsed


@traceable(run_type="llm", name="classify_intent")
async def classify_intent(
    customer_message_redacted: str,
    state: AgentState | None = None,
) -> ClassificationResult:
    data = await _chat_json(
        [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": customer_message_redacted},
        ],
        label="classify",
        state=state,
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
    state: AgentState | None = None,
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
        ],
        label="draft",
        state=state,
    )
    return DraftResult.model_validate(data)


@traceable(run_type="llm", name="summarize_context_changes")
async def summarize_context_changes(
    old_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
    state: AgentState | None = None,
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
        ],
        label="summarize_changes",
        state=state,
    )
    return ContextDelta.model_validate(data)


__all__ = [
    "ClassificationResult",
    "ContextDelta",
    "DraftResult",
    "_compute_cost",
    "_ls_metadata",
    "classify_intent",
    "draft_response",
    "summarize_context_changes",
    "track_llm_usage",
]
