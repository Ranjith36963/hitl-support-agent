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
import time as _time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langgraph.types import Command

from src.config import settings
from src.graph import async_sqlite_checkpointer, build_full_graph_builder
from src.metrics import E2E_LATENCY, TICKETS_TOTAL
from src.state import AgentState

log = logging.getLogger(__name__)

_graph: Any = None
_checkpointer: Any = None
_checkpoint_stack: AsyncExitStack | None = None
_router: Any = None  # MCPClientRouter — lazy import to keep this module light
_router_stack: AsyncExitStack | None = None
_lock = asyncio.Lock()
# Graph version is stamped at compile time and frozen for the process lifetime.
# Reading env at every ticket arrival would let env drift produce metric chaos
# (tickets compiled under v4 mode getting stamped as v3 if env flipped between
# compile and arrival). Compile-time is the single source of truth.
_compiled_mode: str = "v3"


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
    global _graph, _checkpointer, _checkpoint_stack, _router, _router_stack, _compiled_mode

    _ensure_db_dir()

    if _graph is None:
        # Snapshot env BEFORE build_full_graph_builder() reads it, so the mode
        # we stamp on tickets and the mode the graph was actually compiled in
        # are guaranteed identical (they're both reading the same env value
        # microseconds apart). Frozen for the lifetime of this graph object.
        import os as _os
        _compiled_mode = "v4" if _os.environ.get("MULTIAGENT_ENABLED", "0") == "1" else "v3"

        _checkpoint_stack = AsyncExitStack()
        await _checkpoint_stack.__aenter__()
        _checkpointer = await _checkpoint_stack.enter_async_context(
            async_sqlite_checkpointer(settings.sqlite_checkpoint_path)
        )
        _graph = build_full_graph_builder().compile(checkpointer=_checkpointer)
        log.info(
            "Graph compiled in %s mode (AsyncSqliteSaver at %s)",
            _compiled_mode,
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


def get_graph_version() -> str:
    """The graph_version stamped on tickets compiled under this process.

    Frozen at startup() time — reflects what was actually compiled, NOT
    whatever MULTIAGENT_ENABLED says now. Used by audit / LangSmith tagging
    to disambiguate v3 vs v4 metrics.
    """
    return _compiled_mode


# ---------------------------------------------------------------------------
# Public coroutines used by IMAP listener + Slack handler.
# ---------------------------------------------------------------------------


async def start_ticket(ticket_id: str, state: AgentState) -> None:
    """Kick off a new ticket. Runs until the graph either auto-sends or
    interrupts at the human-approval gate.

    Stamps `graph_version` from compile-time mode so the trace tag and the
    actually-running graph code are guaranteed consistent. `initial_state()`
    defaults to `v3` for back-compat; this override is the source of truth.
    """
    state["graph_version"] = _compiled_mode
    g = graph()
    config = {"configurable": {"thread_id": ticket_id}}
    _start = _time.monotonic()
    async with _lock:
        async for chunk in g.astream(state, config):
            if "__interrupt__" in chunk:
                log.info("Ticket %s paused for approval", ticket_id)
                # Don't observe E2E_LATENCY here — the ticket isn't done yet,
                # human approval is pending. E2E fires on the resume() side
                # of the pause, or when the graph completes without interrupt.
                return
        log.info("Ticket %s completed without interrupt (auto-send path)", ticket_id)
        # Auto-send path — terminal state reached without human approval.
        await _emit_terminal_metrics(g, config, _start)


async def _emit_terminal_metrics(
    g: Any, config: dict[str, Any], start_monotonic: float
) -> None:
    """Observe E2E_LATENCY and increment TICKETS_TOTAL when a ticket reaches
    its terminal state. Called from both start_ticket (auto-send path) and
    resume (post-approval path).

    Reads the final values via aget_state — best-effort so metric
    emission can't crash the graph runner.
    """
    E2E_LATENCY.observe(_time.monotonic() - start_monotonic)
    try:
        snap = await g.aget_state(config)
        values = snap.values or {}
    except Exception:  # noqa: BLE001 — metrics must never crash the runner
        values = {}
    intent = str(values.get("intent") or "unknown")
    outcome = str(values.get("final_state") or "unknown")
    TICKETS_TOTAL.labels(intent=intent, outcome=outcome).inc()


async def resume(thread_id: str, value: dict[str, Any]) -> None:
    """Resume a paused graph with a human action.

    Cross-version guard: if the ticket was checkpointed under one mode but
    the server is now running another (operator flipped MULTIAGENT_ENABLED
    between server restarts), refuse to resume — the v3/v4 transient field
    sets and node behaviors don't match. Route to manual queue with a clear
    audit trail rather than produce silent metric drift.
    """
    g = graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = await g.aget_state(config)
    persisted_version = snapshot.values.get("graph_version") if snapshot.values else None
    if persisted_version and persisted_version != _compiled_mode:
        log.warning(
            "Ticket %s checkpointed under %s but server is %s — refusing resume. "
            "Operator likely flipped MULTIAGENT_ENABLED between restarts.",
            thread_id, persisted_version, _compiled_mode,
        )
        # Append-only audit so the manual-queue handoff is traceable.
        await g.aupdate_state(
            config,
            {
                "final_state": "failed_manual",
                "send_status": "failed_manual",
                "audit_log": (snapshot.values.get("audit_log") or []) + [
                    {
                        "node": "graph_runner",
                        "ticket_id": thread_id,
                        "error": "graph_version_mismatch",
                        "persisted_version": persisted_version,
                        "current_mode": _compiled_mode,
                    }
                ],
            },
        )
        return

    _resume_start = _time.monotonic()
    async with _lock:
        async for chunk in g.astream(Command(resume=value), config):
            if "__interrupt__" in chunk:
                log.info("Ticket %s re-paused (likely revalidate-changed)", thread_id)
                return
    log.info("Ticket %s resumed and completed", thread_id)
    # Approved/edited/rejected path — terminal state reached after human.
    await _emit_terminal_metrics(g, config, _resume_start)
