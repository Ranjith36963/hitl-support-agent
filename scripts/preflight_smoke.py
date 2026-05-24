"""Pre-flight check — verifies that every external credential the agent
needs is actually valid, BEFORE we boot the full server. Each check is
short, isolated, and read-only. Exits non-zero on any failure so a CI/Make
target can gate on it.

Run from repo root:  python -m scripts.preflight_smoke
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import os  # noqa: E402

from openai import AsyncOpenAI  # noqa: E402


async def _check_openai() -> tuple[bool, str]:
    """Verify the OpenAI API key + model id work — single tiny call."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return False, "OPENAI_API_KEY missing"
    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=5,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    content = (resp.choices[0].message.content or "").strip()
    return True, f"model={model} replied {content!r}"


def _check_gmail_imap() -> tuple[bool, str]:
    """Verify IMAP login works — connects, logs in, closes."""
    import imaplib

    user = os.environ.get("GMAIL_USER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    host = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
    if not user or not pw:
        return False, "GMAIL_USER or GMAIL_APP_PASSWORD missing"
    try:
        m = imaplib.IMAP4_SSL(host)
        m.login(user, pw)
        typ, data = m.list()
        m.logout()
        return True, f"logged in as {user}, list() returned {typ}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def _check_gmail_smtp() -> tuple[bool, str]:
    """Verify SMTP login works — connects, TLS, AUTHs, quits. No mail sent."""
    import smtplib

    user = os.environ.get("GMAIL_USER", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    host = os.environ.get("GMAIL_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("GMAIL_SMTP_PORT", "587"))
    if not user or not pw:
        return False, "GMAIL_USER or GMAIL_APP_PASSWORD missing"
    try:
        s = smtplib.SMTP(host, port, timeout=10)
        s.starttls()
        s.login(user, pw)
        s.quit()
        return True, f"AUTH succeeded against {host}:{port}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


async def _check_slack() -> tuple[bool, str]:
    """Verify Slack bot token by calling auth.test (no message sent)."""
    import httpx

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return False, "SLACK_BOT_TOKEN missing"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    if not data.get("ok"):
        return False, f"auth.test returned not-ok: {data.get('error', 'unknown')}"
    return True, f"bot={data.get('user')!r} team={data.get('team')!r}"


def _check_langsmith() -> tuple[bool, str]:
    """Verify LangSmith key by hitting the /info endpoint — read-only."""
    import urllib.request

    key = os.environ.get("LANGSMITH_API_KEY", "")
    if not key:
        return False, "LANGSMITH_API_KEY missing"
    try:
        req = urllib.request.Request(
            "https://api.smith.langchain.com/info",
            headers={"x-api-key": key},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            status = r.status
        return status == 200, f"GET /info -> {status}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


async def main() -> int:
    print("HITL agent — pre-flight credentials check")
    print("=" * 56)

    checks = [
        ("OpenAI",           await _check_openai()),
        ("Gmail IMAP",       _check_gmail_imap()),
        ("Gmail SMTP",       _check_gmail_smtp()),
        ("Slack bot token",  await _check_slack()),
        ("LangSmith",        _check_langsmith()),
    ]

    failed = 0
    for label, (ok, detail) in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label:<18} {detail}")
        if not ok:
            failed += 1

    print()
    if failed:
        print(f"{failed} check(s) failed — fix .env before booting the server.")
    else:
        print("All credentials verified. Safe to `python -m src.server`.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
