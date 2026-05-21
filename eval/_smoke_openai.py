"""Smoke test: does the OpenAI key + chosen model work for our JSON-mode call?

Probes one classify-style request against `OPENAI_MODEL` (defaults to
`gpt-4o-mini`) using the key from `.env`. Prints HTTP status, parsed JSON,
and token usage. Cheap — costs fractions of a cent.

Run from repo root:  python -m eval._smoke_openai
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

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
API_KEY = os.environ.get("OPENAI_API_KEY", "")


async def main() -> int:
    if not API_KEY:
        print("FAIL: OPENAI_API_KEY not set in .env")
        return 2

    client = AsyncOpenAI(api_key=API_KEY)
    print(f"Calling {MODEL} on OpenAI direct")
    print(f"API key tail: ...{API_KEY[-6:]}\n")

    try:
        resp = await client.chat.completions.create(
            model=MODEL,
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
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    content = (resp.choices[0].message.content or "").strip()
    print(f"Response: {content}")
    try:
        parsed = json.loads(content)
        print(f"Parsed JSON OK: {parsed}")
    except json.JSONDecodeError:
        print("WARN: response was not valid JSON")
        return 1

    if resp.usage:
        print(f"\nTokens: prompt={resp.usage.prompt_tokens} "
              f"completion={resp.usage.completion_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
