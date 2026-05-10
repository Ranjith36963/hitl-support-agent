"""MCP Client Router — single interface routing calls to the three capability-isolated servers.

Implements spec.md §8 client side; see architecture.md "How this maps to the codebase".

Provides:
  MCPClientRouter  — async context-manager; manages stdio subprocesses for all three servers.
    .read.get_crm_profile(customer_email)         → CRMProfile
    .read.get_customer_history(customer_email)    → list[HistoryEntry]
    .read.get_kb_article(query)                   → KBResult
    .email.send(params)                           → EmailSendResult
    .slack.post_approval_request(params)          → SlackPostResult
    .slack.update_message(params)                 → SlackUpdateResult
    .slack.open_edit_modal(params)                → SlackModalResult

Design invariants:
  - Real stdio subprocess per server — capability isolation is a runtime boundary, not naming.
  - Lazy connect: import is free. Start subprocesses only inside `async with MCPClientRouter()`.
  - AsyncExitStack manages all three sessions; clean teardown on exception.
  - Pydantic models at the boundary: nodes.py never sees raw CallToolResult.
  - No bare except — specific exceptions propagate so LangSmith traces see real errors.

Usage:
    async with MCPClientRouter() as router:
        profile = await router.read.get_crm_profile("alice@example.com")
        result  = await router.email.send(EmailSendParams(...))
"""

from __future__ import annotations

import json
import pathlib
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Path to server scripts (Windows-friendly)
# ---------------------------------------------------------------------------
_MCP_DIR = pathlib.Path(__file__).parent.parent / "mcp_server"
_READ_SERVER   = _MCP_DIR / "support_read.py"
_EMAIL_SERVER  = _MCP_DIR / "support_email_write.py"
_SLACK_SERVER  = _MCP_DIR / "support_slack_write.py"


# ===========================================================================
# Pydantic I/O models — READ server
# ===========================================================================

class CRMProfile(BaseModel):
    """Salesforce-shape CRM profile returned by get_crm_profile."""
    email: str
    customer_tier: str = "Free"          # Free | SMB | Enterprise
    contract_value_usd: float = 0.0
    renewal_date: str = ""
    billing_status: str = "current"
    account_status: str = "Active"
    open_tickets: int = 0
    is_stub: bool = False                # True when real data file is absent


class HistoryEntry(BaseModel):
    """Single past-interaction record returned by get_customer_history."""
    ticket_id: str
    date: str
    intent: str = ""
    resolution: str = ""
    sentiment: str = "neutral"
    customer_email: str = ""
    is_stub: bool = False


class KBResult(BaseModel):
    """ACME policy search result returned by get_kb_article."""
    matched_sections: list[str] = Field(default_factory=list)
    verbatim_quote: str = ""
    policy_references: list[str] = Field(default_factory=list)


# ===========================================================================
# Pydantic I/O models — EMAIL WRITE server
# ===========================================================================

class EmailSendParams(BaseModel):
    """Input parameters for send_email tool."""
    to: str
    subject: str
    body: str
    in_reply_to: str
    idempotency_key: str
    references: str = ""


class EmailSendResult(BaseModel):
    """Output from send_email tool."""
    sent_message_id: str
    was_duplicate: bool
    status: str                          # "sent" | "duplicate_skipped"


# ===========================================================================
# Pydantic I/O models — SLACK WRITE server
# ===========================================================================

class SlackPostParams(BaseModel):
    """Input parameters for post_approval_request tool."""
    channel: str
    blocks: list[dict[str, Any]]
    text: str = "New support ticket requires approval"


class SlackPostResult(BaseModel):
    """Output from post_approval_request tool."""
    slack_message_ts: str
    channel: str
    ok: bool


class SlackUpdateParams(BaseModel):
    """Input parameters for update_message tool."""
    channel: str
    ts: str
    blocks: list[dict[str, Any]]
    text: str = "Message updated"


class SlackUpdateResult(BaseModel):
    """Output from update_message tool."""
    ok: bool
    ts: str
    channel: str


class SlackModalParams(BaseModel):
    """Input parameters for open_edit_modal tool."""
    trigger_id: str
    ticket_id: str
    current_draft: str


class SlackModalResult(BaseModel):
    """Output from open_edit_modal tool."""
    ok: bool
    view_id: str


# ===========================================================================
# Per-server sub-clients
# ===========================================================================

def _extract_result(raw: Any) -> dict[str, Any]:
    """Parse the first TextContent result from a CallToolResult into a dict.

    MCP tool results arrive as a list of content blocks. FastMCP encodes
    structured return values as JSON in a TextContent block.
    """
    content = raw.content
    if not content:
        return {}
    first = content[0]
    # structuredContent (dict) is the canonical FastMCP structured return channel
    if hasattr(raw, "structuredContent") and raw.structuredContent:
        sc = raw.structuredContent
        return sc if isinstance(sc, dict) else {}
    # Fall back to JSON parsing the TextContent block
    text = getattr(first, "text", None)
    if text is None:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": text}


def _extract_list_result(raw: Any) -> list[Any]:
    """Parse a list-type result from a CallToolResult.

    FastMCP wraps list-returning tools in structuredContent as {"result": [...]}.
    This function unwraps that convention transparently.
    """
    content = raw.content
    if hasattr(raw, "structuredContent") and raw.structuredContent:
        sc = raw.structuredContent
        # FastMCP list convention: {"result": [...]}
        if isinstance(sc, dict) and "result" in sc and isinstance(sc["result"], list):
            return sc["result"]
        if isinstance(sc, list):
            return sc
        return [sc]
    # Fall back to text content — FastMCP may emit each item as a separate block
    if not content:
        return []
    # Try parsing the first text block as a JSON list or single item
    text = getattr(content[0], "text", None)
    if text is None:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # May be a single item or {"result": [...]}
            if "result" in parsed and isinstance(parsed["result"], list):
                return parsed["result"]
            return [parsed]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


class _ReadClient:
    """Thin async wrapper over the support_read MCP server session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def get_crm_profile(self, customer_email: str) -> CRMProfile:
        """Fetch CRM profile for *customer_email*. Returns CRMProfile (may be stub)."""
        raw = await self._session.call_tool(
            "get_crm_profile",
            arguments={"customer_email": customer_email},
        )
        data = _extract_result(raw)
        return CRMProfile.model_validate(data)

    async def get_customer_history(self, customer_email: str) -> list[HistoryEntry]:
        """Fetch 90-day interaction history for *customer_email*."""
        raw = await self._session.call_tool(
            "get_customer_history",
            arguments={"customer_email": customer_email},
        )
        items = _extract_list_result(raw)
        return [HistoryEntry.model_validate(item) for item in items]

    async def get_kb_article(self, query: str) -> KBResult:
        """Search ACME policy corpus with *query*. Returns matched section + quote."""
        raw = await self._session.call_tool(
            "get_kb_article",
            arguments={"query": query},
        )
        data = _extract_result(raw)
        return KBResult.model_validate(data)


class _EmailClient:
    """Thin async wrapper over the support_email_write MCP server session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def send(self, params: EmailSendParams) -> EmailSendResult:
        """Send (or deduplicate) an email. Maps EmailSendParams → EmailSendResult."""
        raw = await self._session.call_tool(
            "send_email",
            arguments=params.model_dump(),
        )
        data = _extract_result(raw)
        return EmailSendResult.model_validate(data)


class _SlackClient:
    """Thin async wrapper over the support_slack_write MCP server session."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def post_approval_request(self, params: SlackPostParams) -> SlackPostResult:
        """Post Block Kit approval message to Slack channel."""
        raw = await self._session.call_tool(
            "post_approval_request",
            arguments=params.model_dump(),
        )
        data = _extract_result(raw)
        return SlackPostResult.model_validate(data)

    async def update_message(self, params: SlackUpdateParams) -> SlackUpdateResult:
        """Update an existing Slack message in-place."""
        raw = await self._session.call_tool(
            "update_message",
            arguments=params.model_dump(),
        )
        data = _extract_result(raw)
        return SlackUpdateResult.model_validate(data)

    async def open_edit_modal(self, params: SlackModalParams) -> SlackModalResult:
        """Open a Slack views.open edit modal for the Edit HITL flow."""
        raw = await self._session.call_tool(
            "open_edit_modal",
            arguments=params.model_dump(),
        )
        data = _extract_result(raw)
        return SlackModalResult.model_validate(data)


# ===========================================================================
# MCPClientRouter — the public interface
# ===========================================================================

class MCPClientRouter:
    """Async context manager that routes tool calls to three capability-isolated MCP servers.

    Usage:
        async with MCPClientRouter() as router:
            profile = await router.read.get_crm_profile("alice@example.com")
            result  = await router.email.send(EmailSendParams(...))
            post    = await router.slack.post_approval_request(SlackPostParams(...))

    Attributes:
        read:   _ReadClient   — CRM + KB retrieval (READ-ONLY capability)
        email:  _EmailClient  — Gmail SMTP send (EMAIL WRITE capability)
        slack:  _SlackClient  — Slack post/update/modal (SLACK WRITE capability)

    Three separate stdio subprocesses are spawned (one per server). All are
    torn down cleanly on exit, even on exception.
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.read: _ReadClient | None = None
        self.email: _EmailClient | None = None
        self.slack: _SlackClient | None = None

    async def __aenter__(self) -> MCPClientRouter:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        python = sys.executable  # same interpreter that's running the agent

        # --- READ server ---
        read_session = await self._stack.enter_async_context(
            _make_session(python, str(_READ_SERVER))
        )
        self.read = _ReadClient(read_session)

        # --- EMAIL WRITE server ---
        email_session = await self._stack.enter_async_context(
            _make_session(python, str(_EMAIL_SERVER))
        )
        self.email = _EmailClient(email_session)

        # --- SLACK WRITE server ---
        slack_session = await self._stack.enter_async_context(
            _make_session(python, str(_SLACK_SERVER))
        )
        self.slack = _SlackClient(slack_session)

        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(*exc_info)
            self._stack = None
        self.read = None
        self.email = None
        self.slack = None


# ---------------------------------------------------------------------------
# Internal helper — opens a stdio_client + ClientSession for one server script
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_session(python: str, script_path: str):
    """Open a stdio_client transport → ClientSession for *script_path*.

    Yields an initialized ClientSession ready for tool calls.
    """
    # CRITICAL: pass env=dict(os.environ) explicitly — by default the MCP
    # stdio_client spawns the subprocess with a SANITIZED env (no inheritance),
    # which means SLACK_BOT_TOKEN, GMAIL_APP_PASSWORD, etc. are missing in
    # the child process and the MCP servers raise "X must be set in
    # environment" at first tool call. The parent process has these via
    # load_dotenv() in src/server.py — pass them through explicitly.
    import os

    server_params = StdioServerParameters(
        command=python,
        args=[script_path],
        env=dict(os.environ),
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


# ===========================================================================
# Quick smoke-test
# ===========================================================================

if __name__ == "__main__":
    import asyncio

    async def _smoke() -> None:
        async with MCPClientRouter() as router:
            profile = await router.read.get_crm_profile("test@example.com")
            print("CRM profile:", profile)
            history = await router.read.get_customer_history("test@example.com")
            print("History entries:", len(history), history)
            kb = await router.read.get_kb_article("refund policy manager approval")
            print("KB result:", kb)

    asyncio.run(_smoke())
