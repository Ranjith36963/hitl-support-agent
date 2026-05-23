"""Verify Slack credentials before booting the agent.

Reads SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_APP_TOKEN, and the three
SLACK_CHANNEL_* values from `.env`. Tests:
  1. `auth.test`                    — bot token is valid, prints bot identity
  2. `users.conversations`          — bot can list its channel memberships
  3. channel membership check       — bot is in all 3 configured channels
  4. `chat.postMessage` round-trip  — posts a self-labeled VERIFY message,
                                      then immediately `chat.update`s it
                                      (proves both write paths used by the
                                      production graph)
  5. signing-secret round-trip      — HMAC-SHA256 verify against a known body
  6. `apps.connections.open`        — App-Level Token can open Socket Mode
                                      WebSocket (call only, doesn't connect)

Exit code:
  0 — every check passed
  1 — any check failed (env missing, scope missing, channel uninvited, etc.)

Usage:
  python scripts/verify_slack.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from pathlib import Path

# Windows cp1252 console can't encode ✓/✗/→ — force UTF-8 at startup.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Make `src.*` importable when this script runs as `python scripts/verify_slack.py`.
# Without this, the signing-secret check falls back to a tautological inline
# verifier instead of using the real `src.slack_handler.verify_slack_signature`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    print("[FAIL] python-dotenv is not installed.")
    print("  Run: pip install python-dotenv")
    sys.exit(1)

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("[FAIL] slack-sdk is not installed.")
    print("  Run: pip install slack-sdk")
    sys.exit(1)


REQUIRED_ENV = (
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_APP_TOKEN",
    "SLACK_CHANNEL_REFUNDS",
    "SLACK_CHANNEL_TECHNICAL",
    "SLACK_CHANNEL_COMPLAINTS",
)


def _load() -> dict[str, str] | None:
    load_dotenv()
    missing: list[str] = []
    for k in REQUIRED_ENV:
        v = os.environ.get(k, "")
        if not v or v.startswith("TO_FILL"):
            missing.append(k)
    if missing:
        print(f"✗ Missing / placeholder env vars: {', '.join(missing)}")
        print("  Fill them in `.env` and re-run.")
        return None
    return {k: os.environ[k] for k in REQUIRED_ENV}


def _hint_for(exc: SlackApiError) -> str:
    err = exc.response.get("error", "") if hasattr(exc, "response") else ""
    if err == "invalid_auth":
        return (
            "Bot Token is rejected by Slack. Re-copy from your app's "
            "OAuth & Permissions page (must start with xoxb-)."
        )
    if err == "missing_scope":
        needed = exc.response.get("needed", "")
        return (
            f"Bot Token is missing scope: {needed!r}. Add it under OAuth & "
            f"Permissions → Bot Token Scopes, then reinstall the app."
        )
    if err == "not_in_channel":
        return (
            "Bot is not a member of this channel. In Slack: type "
            "`/invite @<bot-name>` inside the channel and re-run."
        )
    if err == "channel_not_found":
        return (
            "Slack does not recognize this channel name. Make sure the "
            "channel exists, is public, and the name in `.env` matches "
            "exactly (with the leading `#`)."
        )
    if err == "token_revoked":
        return "The token was revoked. Generate a fresh one in the app config."
    return ""


def _strip_hash(name: str) -> str:
    """Slack APIs accept channel names without the leading '#'."""
    return name[1:] if name.startswith("#") else name


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_auth(client: WebClient) -> tuple[bool, dict | None]:
    print("\n[1/6] auth.test — bot token + identity")
    try:
        resp = client.auth_test()
    except SlackApiError as exc:
        print(f"  ✗ auth.test failed: {exc.response.get('error', exc)}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False, None
    print(
        f"  ✓ team={resp['team']!r}  bot_user={resp['user']!r}  "
        f"bot_id={resp['bot_id']!r}"
    )
    return True, dict(resp.data)


def check_channel_membership(
    client: WebClient, channels: list[str]
) -> tuple[bool, dict[str, str]]:
    """Verify bot is invited to every configured channel.

    Returns (ok, name_to_id_map). The id map is used by the post test so we
    can post by ID even when channel names contain '#'.
    """
    print(f"\n[2/6] channel membership — bot must be in {len(channels)} channel(s)")
    name_to_id: dict[str, str] = {}
    cursor = None
    seen: dict[str, str] = {}
    try:
        while True:
            resp = client.users_conversations(
                types="public_channel,private_channel", limit=200, cursor=cursor
            )
            for ch in resp.get("channels", []):
                seen[ch["name"]] = ch["id"]
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as exc:
        print(f"  ✗ users.conversations failed: {exc.response.get('error', exc)}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False, name_to_id

    print(f"  ↳ bot is currently a member of {len(seen)} channel(s)")
    all_ok = True
    for raw in channels:
        name = _strip_hash(raw)
        if name in seen:
            channel_id = seen[name]
            print(f"  ✓ {raw}  (id={channel_id})")
            name_to_id[raw] = channel_id
        else:
            print(f"  ✗ {raw}  — bot is NOT a member")
            print(
                f"    hint: in Slack, open {raw} and run `/invite @<bot-name>`. "
                f"Bot can only post to channels it has been invited to."
            )
            all_ok = False
    return all_ok, name_to_id


def check_post_and_update(
    client: WebClient, channel_id: str, channel_name: str
) -> bool:
    """Post a self-labeled VERIFY message, then immediately update it.

    Mirrors the production graph's two write paths: post_approval_request
    (chat.postMessage) and update_message (chat.update).
    """
    print(f"\n[3/6] chat.postMessage — posting VERIFY message to {channel_name}")
    pid = os.getpid()
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    init_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":mag: *VERIFY {pid} · {when}*\n"
                    f"Smoke test from `scripts/verify_slack.py` — proves the "
                    f"bot can post Block Kit. Will update in place in a moment, "
                    f"then you can delete this message."
                ),
            },
        }
    ]
    try:
        post = client.chat_postMessage(
            channel=channel_id,
            blocks=init_blocks,
            text=f"VERIFY {pid} (Block Kit fallback)",
        )
    except SlackApiError as exc:
        print(f"  ✗ chat.postMessage failed: {exc.response.get('error', exc)}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False
    ts = post["ts"]
    print(f"  ✓ posted (ts={ts})")

    print("\n[4/6] chat.update — updating message in place (mirrors graph audit-close)")
    updated_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":white_check_mark: *VERIFY {pid} · update OK*\n"
                    f"Both `chat.postMessage` and `chat.update` work. "
                    f"You can delete this message now (3-dot menu → Delete)."
                ),
            },
        }
    ]
    try:
        client.chat_update(
            channel=channel_id, ts=ts, blocks=updated_blocks, text=f"VERIFY {pid} OK"
        )
    except SlackApiError as exc:
        print(f"  ✗ chat.update failed: {exc.response.get('error', exc)}")
        if h := _hint_for(exc):
            print(f"    hint: {h}")
        return False
    print("  ✓ updated")
    return True


def check_signing_secret_round_trip(secret: str) -> bool:
    """Verify the signing secret is usable for HMAC-SHA256 — calls the exact
    production verifier `src.slack_handler.verify_slack_signature` so this
    test would catch any drift between sign-side and verify-side code.
    """
    print("\n[5/6] signing secret — HMAC-SHA256 round-trip")
    try:
        from src.slack_handler import verify_slack_signature
    except Exception as exc:
        print(f"  ✗ couldn't import production verifier: {type(exc).__name__}: {exc}")
        print("    hint: run this script from the repo root, not from inside scripts/")
        return False

    body = b"payload=%7B%22action%22%3A%22approve%22%7D"
    ts = str(int(time.time()))
    base = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

    # Positive: a correctly-signed request must verify.
    if not verify_slack_signature(
        body=body, timestamp=ts, signature=sig, signing_secret=secret
    ):
        print("  ✗ correctly-signed request was REJECTED — signing secret broken")
        return False

    # Negative: a tampered body must NOT verify (anti-tamper sanity).
    tampered = body + b"&tampered=1"
    if verify_slack_signature(
        body=tampered, timestamp=ts, signature=sig, signing_secret=secret
    ):
        print("  ✗ tampered body was ACCEPTED — verifier is broken")
        return False

    print("  ✓ valid sig accepted, tampered sig rejected (via src.slack_handler)")
    return True


def check_app_token(app_token: str) -> bool:
    """Open a Socket Mode WebSocket URL (without actually connecting).

    `apps.connections.open` is the call the SocketModeHandler makes at
    startup. If this 200s, the App-Level Token has the connections:write
    scope and can drive Socket Mode.
    """
    print("\n[6/6] apps.connections.open — App-Level Token (Socket Mode)")
    # `apps.connections.open` requires `app_token` as an explicit kwarg
    # in slack-sdk; the WebClient instance token is ignored for this call.
    socket_client = WebClient()
    try:
        resp = socket_client.apps_connections_open(app_token=app_token)
    except SlackApiError as exc:
        err = exc.response.get("error", "") if hasattr(exc, "response") else ""
        print(f"  ✗ apps.connections.open failed: {err or exc}")
        if err == "invalid_auth":
            print(
                "    hint: SLACK_APP_TOKEN is rejected. Must start with "
                "xapp- and have connections:write scope. Regenerate under "
                "Basic Information → App-Level Tokens."
            )
        elif err == "missing_scope":
            print(
                "    hint: App-Level Token is missing connections:write. "
                "Regenerate the token with that scope."
            )
        return False
    url = resp.get("url", "")
    if not url.startswith("wss://"):
        print(f"  ✗ unexpected response (no wss URL): {resp}")
        return False
    # Don't print the full URL — it's a one-time WebSocket auth string.
    print(f"  ✓ Socket Mode URL granted ({url[:30]}...)")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    env = _load()
    if env is None:
        return 1

    bot = WebClient(token=env["SLACK_BOT_TOKEN"])

    auth_ok, _ = check_auth(bot)
    if not auth_ok:
        # Without a working bot token, every other check fails noisily.
        # Bail early with a clean summary.
        print("\n✗ Slack auth failed — fix bot token and re-run.")
        return 1

    channels = [
        env["SLACK_CHANNEL_REFUNDS"],
        env["SLACK_CHANNEL_TECHNICAL"],
        env["SLACK_CHANNEL_COMPLAINTS"],
    ]
    members_ok, name_to_id = check_channel_membership(bot, channels)

    # Post test only if the refunds channel is reachable.
    refunds_name = env["SLACK_CHANNEL_REFUNDS"]
    refunds_id = name_to_id.get(refunds_name)
    if refunds_id is not None:
        post_ok = check_post_and_update(bot, refunds_id, refunds_name)
    else:
        print(f"\n[3-4/6] SKIPPED — bot not in {refunds_name}")
        post_ok = False

    sign_ok = check_signing_secret_round_trip(env["SLACK_SIGNING_SECRET"])
    app_ok = check_app_token(env["SLACK_APP_TOKEN"])

    print()
    print("─" * 60)
    summary = [
        ("auth.test", auth_ok),
        ("channel membership", members_ok),
        ("post + update", post_ok),
        ("signing secret HMAC", sign_ok),
        ("Socket Mode App Token", app_ok),
    ]
    for label, ok in summary:
        print(f"  {'✓' if ok else '✗'}  {label}")
    print("─" * 60)
    if all(ok for _, ok in summary):
        print("\n✓ Slack ready. Boot the agent with: python -m src.server")
        return 0
    print("\n✗ Slack not ready — fix the failures above before booting the agent.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
