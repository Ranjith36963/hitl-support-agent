"""FastAPI app — wires the IMAP listener, Slack handler, and health endpoint.

Layout:
  - GET  /health                  — process liveness
  - POST /slack/events            — Slack webhook (only used when not in Socket Mode)
  - background task: IMAP listener pushing into graph_runner.start_ticket
  - background task: Slack Socket Mode loop (when SLACK_APP_TOKEN is set)

The Bolt SDK's AsyncSlackRequestHandler converts FastAPI request objects into
Bolt's internal Request shape and runs all the registered action/view handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# Load .env into os.environ BEFORE any src.* import. pydantic-settings reads
# .env into the Settings object but does NOT propagate to os.environ — and
# src/llm.py reads OPENROUTER_API_KEY directly from os.environ. Without
# this line the server boots fine then 401s the first LLM call.
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from prometheus_client import make_asgi_app  # noqa: E402
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler  # noqa: E402

from src import graph_runner  # noqa: E402

# Importing src.metrics at module load registers all Counters / Histograms with
# the default registry. Even modules that lazily import metrics (e.g. src.llm)
# work because they push into the same singletons.
from src import metrics as _metrics  # noqa: E402, F401
from src.config import settings  # noqa: E402
from src.email_listener import listen_forever  # noqa: E402
from src.slack_handler import get_app, run_socket_mode  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Sync init: SQLite checkpointer + compiled graph.
    graph_runner.init()
    # Async init: spawn MCP server subprocesses (read / email / slack).
    await graph_runner.startup()

    bg_tasks: list[asyncio.Task[Any]] = []

    # IMAP listener — feeds new emails into the graph.
    if settings.gmail_user and settings.gmail_app_password:
        bg_tasks.append(asyncio.create_task(listen_forever(graph_runner.start_ticket)))
        log.info("IMAP listener started for %s", settings.gmail_user)
    else:
        log.warning("IMAP listener NOT started (missing GMAIL_USER / GMAIL_APP_PASSWORD)")

    # Slack Socket Mode (preferred for dev — no public URL needed).
    if settings.slack_app_token and settings.slack_bot_token and settings.slack_signing_secret:
        bg_tasks.append(asyncio.create_task(run_socket_mode()))
        log.info("Slack Socket Mode started")
    else:
        log.warning(
            "Slack Socket Mode NOT started (missing SLACK_APP_TOKEN / SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET)"
        )

    try:
        yield
    finally:
        for t in bg_tasks:
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        await graph_runner.shutdown_async()
        graph_runner.shutdown()


app = FastAPI(title="HITL Support Agent", lifespan=lifespan)

# Prometheus scrape endpoint. Open by design (no auth) — see docs/threat_model.md
# row A5 for the rationale (loopback-only default host; scrape over localhost
# or a private network). For multi-tenant / public deploys, gate via reverse
# proxy + IP allowlist before exposing.
app.mount("/metrics", make_asgi_app())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "slack_configured": bool(settings.slack_bot_token),
        "gmail_configured": bool(settings.gmail_user),
        "graph": "compiled" if graph_runner._graph is not None else "uninitialized",
    }


# ---------------------------------------------------------------------------
# Slack webhook path (only used if Socket Mode is OFF). Bolt does its own
# signature verification when the AsyncSlackRequestHandler is in place.
# ---------------------------------------------------------------------------


_slack_request_handler: AsyncSlackRequestHandler | None = None


def _slack_handler() -> AsyncSlackRequestHandler:
    global _slack_request_handler
    if _slack_request_handler is None:
        _slack_request_handler = AsyncSlackRequestHandler(get_app())
    return _slack_request_handler


@app.post("/slack/events")
async def slack_events(req: Request) -> Any:
    if not settings.slack_signing_secret:
        return JSONResponse({"error": "Slack not configured"}, status_code=503)
    return await _slack_handler().handle(req)


def main() -> None:
    """Entry point: `python -m src.server` boots uvicorn."""
    import uvicorn

    uvicorn.run(
        "src.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
