"""Singleton compiled graph + start/resume helpers.

Long-running service holds one compiled graph + one SqliteSaver connection.
The IMAP listener calls `start_ticket()` on a new email; the Slack handler
calls `resume()` on a button click. Both bottom out in `graph.astream(...)`
with `thread_id == ticket_id`.

The Slack message_ts lives in state, so a server restart still resumes onto
the right Slack message.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langgraph.types import Command

from src.config import settings
from src.graph import async_sqlite_checkpointer, build_full_graph_builder
from src.state import AgentState

log = logging.getLogger(__name__)

_graph: Any = None
_checkpointer: Any = None
_checkpoint_stack: AsyncExitStack | None = None
_router: Any = None  # MCPClientRouter — lazy import to keep this module light
_router_stack: AsyncExitStack | None = None
_lock = asyncio.Lock()


def _ensure_db_dir() -> None:
    Path(settings.sqlite_checkpoint_path).parent.mkdir(parents=True, exist_ok=True)


def init() -> None:
    """No-op kept for back-compat. The production path uses async startup
    because the graph nodes are async and the checkpointer must be
    AsyncSqliteSaver. Use `await startup()` to actually compile."""
    _ensure_db_dir()


async def startup() -> None:
    """Compile the graph with AsyncSqliteSaver and spawn MCP subprocesses.
    Single async entrypoint — called from the FastAPI lifespan."""
    global _graph, _checkpointer, _checkpoint_stack, _router, _router_stack

    _ensure_db_dir()

    if _graph is None:
        _checkpoint_stack = AsyncExitStack()
        await _checkpoint_stack.__aenter__()
        _checkpointer = await _checkpoint_stack.enter_async_context(
            async_sqlite_checkpointer(settings.sqlite_checkpoint_path)
        )
        _graph = build_full_graph_builder().compile(checkpointer=_checkpointer)
        log.info(
            "Graph compiled with AsyncSqliteSaver at %s",
            settings.sqlite_checkpoint_path,
        )

    if _router is None:
        from src.mcp_client import MCPClientRouter

        _router_stack = AsyncExitStack()
        await _router_stack.__aenter__()
        _router = await _router_stack.enter_async_context(MCPClientRouter())
        log.info("MCP client router started (3 subprocesses: read / email / slack)")


async def shutdown_async() -> None:
    """Async shutdown: tear down MCP subprocesses AND the AsyncSqliteSaver."""
    global _router, _router_stack, _graph, _checkpointer, _checkpoint_stack
    if _router_stack is not None:
        try:
            await _router_stack.__aexit__(None, None, None)
        except Exception:
            log.exception("MCP router shutdown raised")
    if _checkpoint_stack is not None:
        try:
            await _checkpoint_stack.__aexit__(None, None, None)
        except Exception:
            log.exception("Checkpointer shutdown raised")
    _router = None
    _router_stack = None
    _graph = None
    _checkpointer = None
    _checkpoint_stack = None


def shutdown() -> None:
    """No-op back-compat shim — production path uses shutdown_async()."""
    pass


def graph() -> Any:
    if _graph is None:
        raise RuntimeError(
            "Graph not compiled. Call `await graph_runner.startup()` first "
            "(the FastAPI lifespan does this automatically)."
        )
    return _graph


def get_mcp_router() -> Any:
    """Return the live MCPClientRouter. Caller MUST be inside an async context
    (the router's stdio sessions live inside `async with`).

    Raises if `startup()` hasn't been called yet — fail loud rather than spawn
    new subprocesses per call.
    """
    if _router is None:
        raise RuntimeError(
            "MCP router is not started. Call `await graph_runner.startup()` first "
            "(the FastAPI lifespan does this automatically)."
        )
    return _router


# ---------------------------------------------------------------------------
# Public coroutines used by IMAP listener + Slack handler.
# ---------------------------------------------------------------------------


async def start_ticket(ticket_id: str, state: AgentState) -> None:
    """Kick off a new ticket. Runs until the graph either auto-sends or
    interrupts at the human-approval gate."""
    g = graph()
    config = {"configurable": {"thread_id": ticket_id}}
    async with _lock:
        async for chunk in g.astream(state, config):
            if "__interrupt__" in chunk:
                log.info("Ticket %s paused for approval", ticket_id)
                return
        log.info("Ticket %s completed without interrupt (auto-send path)", ticket_id)


async def resume(thread_id: str, value: dict[str, Any]) -> None:
    """Resume a paused graph with a human action."""
    g = graph()
    config = {"configurable": {"thread_id": thread_id}}
    async with _lock:
        async for chunk in g.astream(Command(resume=value), config):
            if "__interrupt__" in chunk:
                log.info("Ticket %s re-paused (likely revalidate-changed)", thread_id)
                return
    log.info("Ticket %s resumed and completed", thread_id)
