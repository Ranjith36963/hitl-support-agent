"""Smoke test: can we call an OpenRouter `:free`-suffix DeepSeek variant right now?

The original DeepSeek V3 `:free` tier (deepseek/deepseek-chat:free) was
retired by OpenRouter; the live probe in this script targets the newer
`deepseek/deepseek-v4-flash:free` variant — verify the current id with
`python -m eval._list_free_models` before pinning.

Two things this answers in one shot:
  1. Does the call route at all (200) when our paid balance is negative?
  2. What are the current free-tier rate limits (X-RateLimit-* headers)?

Single classify-style call. No graph, no checkpointer, no eval harness side
effects. Run from repo root:

    python -m eval._smoke_free

The model ID is hard-coded here — this script is deliberately a one-off
probe and does NOT mutate `.env` or `OPENROUTER_MODEL`. If it works, the
next step is a normal eval run with `OPENROUTER_MODEL` exported in the shell.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).parent.parent / ".env")

FREE_MODEL = "deepseek/deepseek-v4-flash:free"
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


async def main() -> int:
    if not API_KEY:
        print("FAIL: OPENROUTER_API_KEY not set in .env")
        return 2

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    print(f"Calling {FREE_MODEL} via {BASE_URL}")
    print(f"API key tail: ...{API_KEY[-6:]}\n")

    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=FREE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return a JSON object {\"intent\": \"FAQ\"|\"other\", "
                        "\"intent_confidence\": 0.0-1.0}. No other text."
                    ),
                },
                {"role": "user", "content": "how do I reset my password?"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=80,
        )
    except Exception as exc:  # broad on purpose — surface any failure mode
        print(f"FAIL: request raised {type(exc).__name__}: {exc}")
        return 1

    resp = raw.parse()
    status = raw.http_response.status_code
    headers = dict(raw.http_response.headers)

    print(f"HTTP status: {status}")
    content = (resp.choices[0].message.content or "").strip()
    print(f"Response body (first 200 chars): {content[:200]}")
    try:
        parsed = json.loads(content)
        print(f"Parsed JSON OK: {parsed}")
    except json.JSONDecodeError:
        print("WARN: response was not valid JSON")

    print("\nRate-limit-relevant headers (if present):")
    for key in sorted(headers):
        lk = key.lower()
        if "ratelimit" in lk or "x-ratelimit" in lk or lk in {
            "x-requests-remaining",
            "x-tokens-remaining",
            "retry-after",
        }:
            print(f"  {key}: {headers[key]}")
    if resp.usage:
        print(f"\nTokens used: prompt={resp.usage.prompt_tokens} "
              f"completion={resp.usage.completion_tokens}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
