"""Probe OpenRouter for current account's rate-limit caps + free-tier policy.

Two endpoints worth checking:
  GET /auth/key   — returns rate_limit, limit, limit_remaining, usage
  GET /credits    — returns total_credits, total_usage

Run:  python -m eval._check_or_limits
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


async def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("FAIL: OPENROUTER_API_KEY not set")
        return 1
    headers = {"Authorization": f"Bearer {key}"}

    async with httpx.AsyncClient(timeout=15) as cx:
        for ep in ("/auth/key", "/credits"):
            url = f"https://openrouter.ai/api/v1{ep}"
            try:
                r = await cx.get(url, headers=headers)
                print(f"\n=== GET {ep}  ->  HTTP {r.status_code} ===")
                try:
                    print(json.dumps(r.json(), indent=2))
                except Exception:
                    print(r.text[:500])
            except Exception as exc:
                print(f"{ep} failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
