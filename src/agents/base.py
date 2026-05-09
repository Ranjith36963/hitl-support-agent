"""Shared agent foundation for v4 multi-agent layer.

Three things live here so every agent uses the same plumbing:
1. LLM factory — single AsyncOpenAI client pointed at OpenRouter (consistent with src/llm.py).
2. Prompt loader — reads markdown system prompts from data/prompts/.
3. Handoff metadata builder — feeds LangSmith traces with the v4 tag set
   from docs/v4_multiagent.md "LangSmith observability" section.

Why AsyncOpenAI (not langchain_openai.ChatOpenAI):
v3 already uses openai.AsyncOpenAI directly in src/llm.py. Keeping the same
client type across v3 and v4 means one OpenRouter integration to debug, no
new dependency, no behavior drift between v3 and v4 LLM calls.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

PROMPT_DIR = Path(__file__).parent.parent.parent / "data" / "prompts"


def get_llm(model: str | None = None) -> AsyncOpenAI:
    """Return an AsyncOpenAI client pointed at OpenRouter.

    Lazy + per-call so A/B model swap experiments can override the model
    without touching a module-level singleton. Pass `model` to override
    the default for one call site (e.g., RESEARCHER_MODEL_OVERRIDE).
    """
    return AsyncOpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def get_model_id(override_env: str | None = None) -> str:
    """Resolve the model id, honoring an optional per-agent override env var.

    Order: explicit override env -> OPENROUTER_MODEL -> default deepseek.
    Lets the A/B model swap runner set RESEARCHER_MODEL_OVERRIDE without
    leaking into the Drafter/Critic.
    """
    if override_env:
        override = os.environ.get(override_env)
        if override:
            return override
    return os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")


def load_prompt(name: str) -> str:
    """Load a system prompt from data/prompts/<name>.md (without extension)."""
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip()


def build_handoff_metadata(
    agent_name: str,
    handoff_from: str,
    handoff_to: str,
    handoff_reason: str,
    loop_iteration: int = 0,
    tools_called: list[str] | None = None,
) -> dict[str, Any]:
    """Build the LangSmith metadata dict per docs/v4_multiagent.md.

    The exact tag set is fixed — adding new tags requires amending the
    spec amendment doc first. Keeps the dashboard schema stable across runs.
    """
    return {
        "agent_name": agent_name,
        "handoff_from": handoff_from,
        "handoff_to": handoff_to,
        "handoff_reason": handoff_reason,
        "loop_iteration": loop_iteration,
        "tools_called": ",".join(tools_called or []),
    }


__all__ = [
    "PROMPT_DIR",
    "build_handoff_metadata",
    "get_llm",
    "get_model_id",
    "load_prompt",
]
