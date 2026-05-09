"""Test the shared agent foundation: LLM factory, prompt loader, handoff metadata."""
from src.agents.base import build_handoff_metadata, get_llm, load_prompt


def test_handoff_metadata_includes_required_tags() -> None:
    md = build_handoff_metadata(
        agent_name="researcher",
        handoff_from="classify_intent",
        handoff_to="drafter",
        handoff_reason="delegation",
        loop_iteration=0,
        tools_called=["get_kb_article"],
    )
    assert md["agent_name"] == "researcher"
    assert md["handoff_from"] == "classify_intent"
    assert md["handoff_to"] == "drafter"
    assert md["handoff_reason"] == "delegation"
    assert md["loop_iteration"] == 0
    assert md["tools_called"] == "get_kb_article"


def test_handoff_metadata_joins_multiple_tools() -> None:
    md = build_handoff_metadata(
        agent_name="researcher",
        handoff_from="classify_intent",
        handoff_to="drafter",
        handoff_reason="delegation",
        tools_called=["get_kb_article", "get_crm_profile", "get_customer_history"],
    )
    assert md["tools_called"] == "get_kb_article,get_crm_profile,get_customer_history"


def test_handoff_metadata_handles_empty_tools() -> None:
    md = build_handoff_metadata(
        agent_name="critic",
        handoff_from="drafter",
        handoff_to="drafter",
        handoff_reason="revision_requested",
        loop_iteration=1,
    )
    assert md["tools_called"] == ""
    assert md["loop_iteration"] == 1


def test_load_prompt_reads_markdown_file(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "test_system.md").write_text("You are a test agent.\n")
    monkeypatch.setattr("src.agents.base.PROMPT_DIR", prompt_dir)
    assert load_prompt("test_system") == "You are a test agent."


def test_get_llm_returns_async_client():
    """get_llm() returns an AsyncOpenAI client (consistent with src/llm.py)."""
    from openai import AsyncOpenAI
    client = get_llm()
    assert isinstance(client, AsyncOpenAI)
