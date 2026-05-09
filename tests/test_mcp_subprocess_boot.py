"""MCP subprocess boot test — proves all three servers spawn cleanly.

Without this test, a syntax error or import failure in any of the three MCP
server scripts would only surface at first real run (when the user has
provisioned credentials). This test catches it earlier.

Caveat: the email and slack servers may require env vars to fully initialize
clients — but module-level startup should not. We verify the subprocess can
be SPAWNED via the MCP stdio handshake; we do NOT call any tools.
"""

from __future__ import annotations

import pytest

from src.mcp_client import MCPClientRouter


@pytest.mark.asyncio
async def test_mcp_router_spawns_all_three_servers() -> None:
    """`async with MCPClientRouter()` must complete without raising.

    This proves:
      - `support_read.py` boots, registers tools, completes the MCP handshake
      - `support_email_write.py` boots ditto (no SMTP connection attempted)
      - `support_slack_write.py` boots ditto (no Slack API call attempted)
      - `mcp_client.MCPClientRouter` correctly enters all three async contexts

    Real tool calls are tested via the integration smoke (with mocked routers)
    and at demo time (with real credentials).
    """
    async with MCPClientRouter() as router:
        # All three sub-clients must be live after __aenter__.
        assert router.read is not None, "Read MCP server failed to spawn"
        assert router.email is not None, "Email MCP server failed to spawn"
        assert router.slack is not None, "Slack MCP server failed to spawn"
