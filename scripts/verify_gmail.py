"""Verify Gmail IMAP + SMTP credentials before building anything else.

Reads GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST, GMAIL_SMTP_HOST,
GMAIL_SMTP_PORT from `.env`. Tests:
  1. IMAP4_SSL connect → login → list folders → logout
  2. SMTP connect → STARTTLS → login → quit  (no email is actually sent)

Exit code:
  0 — both checks passed
  1 — any check failed (or env vars missing / placeholder)

Usage:
  python scripts/verify_gmail.py
"""

from __future__ import annotations

import os
import sys
from imaplib import IMAP4_SSL
from smtplib import SMTP

# Windows consoles default to cp1252 — reconfigure to UTF-8 so ✓ / ✗ render.
# No-op on POSIX where stdout is already UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

try:
    from dotenv import load_dotenv
except ImportError:
    print("[FAIL] python-dotenv is not installed.")
    print("  Run: pip install python-dotenv")
    sys.exit(1)


REQUIRED_ENV_VARS = (
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "GMAIL_IMAP_HOST",
    "GMAIL_SMTP_HOST",
    "GMAIL_SMTP_PORT",
)


def _load_and_validate() -> dict[str, str] | None:
    load_dotenv()
    missing: list[str] = []
    for k in REQUIRED_ENV_VARS:
        v = os.environ.get(k, "")
        if not v or v.startswith("TO_FILL"):
            missing.append(k)
    if missing:
        print(f"✗ Missing / placeholder env vars: {', '.join(missing)}")
        print("  Fill them in `.env` and re-run.")
        return None
    return {k: os.environ[k] for k in REQUIRED_ENV_VARS}


def _strip_password_spaces(raw: str) -> str:
    """Google displays App Passwords as 4×4 with spaces. The actual login
    expects 16 chars, no spaces. Strip defensively and warn if we did."""
    cleaned = raw.replace(" ", "")
    if cleaned != raw:
        print(
            f"  note: stripped spaces from GMAIL_APP_PASSWORD "
            f"(input {len(raw)} chars → cleaned {len(cleaned)} chars)"
        )
    return cleaned


def _hint_for(exc: Exception) -> str:
    """Map common Gmail auth exceptions to a one-liner the user can act on."""
    msg = str(exc)
    lower = msg.lower()
    if "application-specific password required" in lower:
        return (
            "2FA isn't enabled on this Gmail account. App Passwords only work "
            "after you turn on 2-Step Verification at "
            "myaccount.google.com/security."
        )
    if "authenticationfailed" in lower or "invalid credentials" in lower:
        return (
            "AUTHENTICATIONFAILED usually means: (a) the App Password is wrong, "
            "(b) you pasted spaces or extra characters, or (c) the password was "
            "regenerated since you copied it. Re-copy from "
            "myaccount.google.com/apppasswords."
        )
    if "5.7.8" in msg or "username and password not accepted" in lower:
        return (
            "Gmail SMTP returned 5.7.8 — same root cause as IMAP "
            "AUTHENTICATIONFAILED. Re-copy the App Password without spaces."
        )
    if "timed out" in lower or "timeout" in lower:
        return (
            "Connection timed out — could be a firewall or VPN blocking ports "
            "993 (IMAP) / 587 (SMTP). Try from a different network."
        )
    return ""


def verify_imap(user: str, password: str, host: str) -> bool:
    print(f"\n[IMAP] connecting to {host} as {user}...")
    try:
        client = IMAP4_SSL(host)
    except Exception as exc:
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False

    try:
        client.login(user, password)
        print("  ✓ login OK")
    except Exception as exc:
        print(f"  ✗ login failed: {type(exc).__name__}: {exc}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        try:
            client.logout()
        except Exception:
            pass
        return False

    try:
        typ, folders = client.list()
        if typ != "OK" or folders is None:
            print(f"  ✗ list returned {typ}")
            client.logout()
            return False
        print(f"  ✓ list returned {len(folders)} folder(s)")
        for f in folders[:3]:
            decoded = f.decode("utf-8", errors="replace") if isinstance(f, bytes) else str(f)
            print(f"    - {decoded}")
        if len(folders) > 3:
            print(f"    ... +{len(folders) - 3} more")
    except Exception as exc:
        print(f"  ✗ list failed: {type(exc).__name__}: {exc}")
        try:
            client.logout()
        except Exception:
            pass
        return False

    try:
        client.logout()
        print("  ✓ logout OK")
    except Exception as exc:
        print(f"  ✗ logout failed: {type(exc).__name__}: {exc}")
        return False

    return True


def verify_smtp(user: str, password: str, host: str, port: int) -> bool:
    print(f"\n[SMTP] connecting to {host}:{port} as {user}...")
    try:
        client = SMTP(host, port, timeout=30)
    except Exception as exc:
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False

    try:
        client.ehlo()
        client.starttls()
        client.ehlo()
        print("  ✓ STARTTLS OK")
    except Exception as exc:
        print(f"  ✗ STARTTLS failed: {type(exc).__name__}: {exc}")
        try:
            client.quit()
        except Exception:
            pass
        return False

    try:
        client.login(user, password)
        print("  ✓ login OK")
    except Exception as exc:
        print(f"  ✗ login failed: {type(exc).__name__}: {exc}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        try:
            client.quit()
        except Exception:
            pass
        return False

    try:
        client.quit()
        print("  ✓ quit OK")
    except Exception as exc:
        print(f"  ✗ quit failed: {type(exc).__name__}: {exc}")
        return False

    return True


def main() -> int:
    env = _load_and_validate()
    if env is None:
        return 1

    user = env["GMAIL_USER"]
    password = _strip_password_spaces(env["GMAIL_APP_PASSWORD"])
    imap_host = env["GMAIL_IMAP_HOST"]
    smtp_host = env["GMAIL_SMTP_HOST"]
    try:
        smtp_port = int(env["GMAIL_SMTP_PORT"])
    except ValueError:
        print(f"✗ GMAIL_SMTP_PORT is not an integer: {env['GMAIL_SMTP_PORT']!r}")
        return 1

    imap_ok = verify_imap(user, password, imap_host)
    smtp_ok = verify_smtp(user, password, smtp_host, smtp_port)

    print()
    if imap_ok and smtp_ok:
        print("✓ Gmail IMAP + SMTP both verified. Ready for Slack.")
        return 0
    if not imap_ok:
        print("✗ IMAP failed — fix above before continuing.")
    if not smtp_ok:
        print("✗ SMTP failed — fix above before continuing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
