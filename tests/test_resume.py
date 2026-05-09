"""Durable execution test — the foundation contract.

Proves that LangGraph + SqliteSaver actually persist state across a simulated
process restart. The graph object is dropped and re-instantiated against the
same SQLite file; resume must work as if nothing happened.

If this test goes red, NOTHING else in the project is meaningful. Every node,
every demo, every eval is built on top of this contract holding.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest
from langgraph.types import Command

from src.graph import compile_with_checkpointer, sqlite_checkpointer
from src.state import initial_state


def _drain(stream: Any) -> list[dict]:
    """Collect every chunk a graph stream yields. Helps inspect __interrupt__."""
    return list(stream)


def test_pause_then_resume_in_one_process(tmp_path: Any) -> None:
    """Sanity check: same process, in-memory pause and resume.

    Verifies the interrupt/resume mechanics work at all before we test
    durability across a process boundary.
    """
    db_path = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": "ticket-sanity-001"}}
    state = initial_state(
        ticket_id="ticket-sanity-001",
        customer_message="hello world",
        email_thread_id="<msg-001@example.com>",
        send_idempotency_key="idem-001",
    )

    with sqlite_checkpointer(db_path) as checkpointer:
        graph = compile_with_checkpointer(checkpointer)
        chunks = _drain(graph.stream(state, config))
        # The last chunk must contain __interrupt__ — graph paused as expected.
        assert any("__interrupt__" in c for c in chunks), (
            f"Graph did not interrupt as expected. Chunks: {chunks}"
        )

        # Pre-interrupt node ran. Verify side-effect-from-before-interrupt landed.
        snapshot = graph.get_state(config)
        assert snapshot.values["slack_channel"] == "#support-refunds"
        assert snapshot.values["slack_message_ts"] == "1234567890.000100"
        assert snapshot.values["approval_status"] == "pending"

        # Resume with "approve". Graph proceeds through post_resume and ends.
        chunks_after = _drain(graph.stream(Command(resume="approve"), config))
        assert chunks_after, "Graph did not produce any chunks after resume"

        final = graph.get_state(config)
        assert final.values["final_state"] == "sent"
        assert final.values["send_status"] == "sent"


def test_durable_resume_across_process_restart(tmp_path: Any) -> None:
    """The real durability test. Drop the graph object mid-pause, re-create it
    against the same SQLite file, and prove resume still works.

    This is the closest you can get to "kill the server, restart it" without
    spawning subprocesses — and it's the right boundary, because the SQLite
    file IS the durability contract. The graph object is just a configuration
    shell; throw it away and rebuild it, the state had better survive.
    """
    db_path = str(tmp_path / "checkpoints.sqlite")
    thread_id = "ticket-durable-001"
    config = {"configurable": {"thread_id": thread_id}}
    state = initial_state(
        ticket_id=thread_id,
        customer_message="I want a $200 refund",
        email_thread_id="<msg-002@example.com>",
        send_idempotency_key="idem-002",
    )

    # ---- Process #1: run graph until interrupt, then drop everything. ----
    with sqlite_checkpointer(db_path) as checkpointer1:
        graph1 = compile_with_checkpointer(checkpointer1)
        chunks = _drain(graph1.stream(state, config))
        assert any("__interrupt__" in c for c in chunks), (
            "Graph #1 did not interrupt — checkpointer write may have failed"
        )

    # Graph object, checkpointer, connection — all gone. Only the SQLite file
    # on disk remains. This is the simulated process death.
    assert os.path.exists(db_path), "Checkpoint database disappeared"

    # ---- Process #2: rebuild graph against the same file, resume. ----
    with sqlite_checkpointer(db_path) as checkpointer2:
        graph2 = compile_with_checkpointer(checkpointer2)

        # State must be recoverable BEFORE we issue any resume command.
        recovered = graph2.get_state(config)
        assert recovered.values["ticket_id"] == thread_id
        assert recovered.values["customer_message"] == "I want a $200 refund"
        assert recovered.values["slack_channel"] == "#support-refunds"
        assert recovered.values["approval_status"] == "pending"

        # Resume on the same thread_id. Graph picks up at the interrupt gate.
        _drain(graph2.stream(Command(resume="approve"), config))

        final = graph2.get_state(config)
        assert final.values["final_state"] == "sent", (
            f"Graph did not complete after durable resume. "
            f"Final state: {final.values}"
        )
        assert final.values["send_status"] == "sent"


def test_reject_path_routes_correctly(tmp_path: Any) -> None:
    """Variant: resume with 'reject' produces a different terminal state.

    Confirms the post_resume node is actually reading the resume value (not
    just blindly setting final_state='sent' regardless).
    """
    db_path = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": "ticket-reject-001"}}
    state = initial_state(
        ticket_id="ticket-reject-001",
        customer_message="never mind",
        email_thread_id="<msg-003@example.com>",
        send_idempotency_key="idem-003",
    )

    with sqlite_checkpointer(db_path) as checkpointer:
        graph = compile_with_checkpointer(checkpointer)
        _drain(graph.stream(state, config))
        _drain(graph.stream(Command(resume="reject"), config))

        final = graph.get_state(config)
        assert final.values["final_state"] == "completed_reject"


if __name__ == "__main__":
    # Allow running directly: `python -m tests.test_resume`
    pytest.main([__file__, "-v"])
