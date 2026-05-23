"""List all `:free`-suffix models currently available on OpenRouter.

Run from repo root:  python -m eval._list_free_models

Prints every model whose ID ends in ":free", sorted by family. Helps pick a
recurring-free replacement after `deepseek/deepseek-chat:free` was retired.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

URL = "https://openrouter.ai/api/v1/models"


async def main() -> int:
    headers = {}
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    async with httpx.AsyncClient(timeout=30) as cx:
        resp = await cx.get(URL, headers=headers)
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}\n{resp.text[:400]}")
        return 1

    data = resp.json().get("data", [])
    free = [m for m in data if m.get("id", "").endswith(":free")]
    print(f"Total models listed: {len(data)}")
    print(f":free variants: {len(free)}\n")

    # Group by family (string before "/")
    by_family: dict[str, list[dict]] = {}
    for m in free:
        family = m["id"].split("/", 1)[0]
        by_family.setdefault(family, []).append(m)

    for family in sorted(by_family):
        print(f"=== {family} ===")
        for m in sorted(by_family[family], key=lambda x: x["id"]):
            ctx = m.get("context_length", "?")
            name = m.get("name", "")
            print(f"  {m['id']:<55} ctx={ctx} ({name})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
