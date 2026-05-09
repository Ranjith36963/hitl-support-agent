"""A/B agent-model swap experiment — v4 portfolio set-piece.

Runs the v4 graph twice — once with Researcher on DeepSeek V3 (default), once
with Researcher on a cheaper model (e.g., Haiku-tier or Llama 3.1 8B). Drafter
and Critic stay on the default model in both arms. Compares:
  - tool_selection_precision (does the swap still pick the right MCP tools?)
  - agent_cost_breakdown (does it save real money?)

Output: a markdown table for the README's v4 section.

Usage:
    python -m eval.ab_model_swap                    # real LLM, both arms
    python -m eval.ab_model_swap --no-llm           # deterministic dry run

This module is intentionally thin — the real work is in eval/run_experiments.py
and eval/multiagent_evaluators.py. This script is the shape of the
experiment, not a re-implementation of the harness.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from eval.multiagent_evaluators import (
    agent_cost_breakdown,
    tool_selection_precision,
)

# Default model arms — swap to whatever your OpenRouter account has enabled.
# Picked DeepSeek (default) vs Llama 3.1 8B as a realistic cheap-vs-expensive
# pair. Override via ARM_DEFAULT_MODEL / ARM_CHEAP_MODEL env vars.
ARMS = {
    "default": os.environ.get("ARM_DEFAULT_MODEL", "deepseek/deepseek-chat"),
    "cheap": os.environ.get("ARM_CHEAP_MODEL", "meta-llama/llama-3.1-8b-instruct"),
}


def run_arm(arm_name: str, model_id: str, no_llm: bool) -> int:
    """Invoke the existing eval harness as a subprocess with arm-specific env.

    Subprocess isolates env-var mutation per arm — safer than mutating
    os.environ in-process when LangGraph caches modules across runs.
    """
    env = os.environ.copy()
    env["MULTIAGENT_ENABLED"] = "1"
    env["RESEARCHER_MODEL_OVERRIDE"] = model_id
    env["AB_ARM_NAME"] = arm_name  # for tagging in LangSmith
    cmd = [sys.executable, "-m", "eval.run_experiments"]
    if no_llm:
        cmd.append("--no-llm")
    print(f"\n=== Arm: {arm_name} ({model_id}) ===")
    return subprocess.call(cmd, env=env, cwd=str(Path(__file__).parent.parent))


async def main(no_llm: bool) -> None:
    """Run both arms sequentially — parallel would race on results.json."""
    rc_default = run_arm("default", ARMS["default"], no_llm)
    rc_cheap = run_arm("cheap", ARMS["cheap"], no_llm)

    print("\n## Researcher A/B Model Swap — v4\n")
    print("| Arm | Model | Exit code |")
    print("|---|---|---|")
    print(f"| default | {ARMS['default']} | {rc_default} |")
    print(f"| cheap | {ARMS['cheap']} | {rc_cheap} |")
    print("\n_Real metrics: read eval/results.json after each arm. Wire into a "
          "single combined report in v4.1 if needed._")
    print(f"\nEvaluator stubs ready: {tool_selection_precision.__name__}, "
          f"{agent_cost_breakdown.__name__}")


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="Deterministic mode")
    args = parser.parse_args()
    asyncio.run(main(args.no_llm))


if __name__ == "__main__":
    cli()
