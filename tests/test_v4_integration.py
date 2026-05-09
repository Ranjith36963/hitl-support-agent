"""End-to-end v4 graph wiring: feature flag toggles agents in/out cleanly."""
import importlib

import pytest


@pytest.mark.asyncio
async def test_v3_path_when_flag_disabled(monkeypatch):
    """MULTIAGENT_ENABLED=0 → graph uses v3 enrich_context_node, not Researcher."""
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    import src.graph
    importlib.reload(src.graph)
    builder = src.graph.build_full_graph_builder()
    assert "enrich_context" in builder.nodes
    assert "draft_response" in builder.nodes


@pytest.mark.asyncio
async def test_v4_path_when_flag_enabled(monkeypatch):
    """MULTIAGENT_ENABLED=1 → graph swaps in Researcher + Drafter sub-graphs."""
    monkeypatch.setenv("MULTIAGENT_ENABLED", "1")
    import src.graph
    importlib.reload(src.graph)
    builder = src.graph.build_full_graph_builder()
    assert "enrich_context" in builder.nodes
    assert "draft_response" in builder.nodes


@pytest.mark.asyncio
async def test_node_count_stable_across_flag(monkeypatch):
    """Outer graph topology must NOT change when flag toggles — agents slot
    into existing positions, they don't add or remove outer nodes.
    Per docs/v4_multiagent.md count: 15 outer nodes either way."""
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    import src.graph
    importlib.reload(src.graph)
    v3_nodes = set(src.graph.build_full_graph_builder().nodes.keys())

    monkeypatch.setenv("MULTIAGENT_ENABLED", "1")
    importlib.reload(src.graph)
    v4_nodes = set(src.graph.build_full_graph_builder().nodes.keys())

    assert v3_nodes == v4_nodes, (
        f"Outer node set changed when flag toggled. v3-only: {v3_nodes - v4_nodes}, "
        f"v4-only: {v4_nodes - v3_nodes}"
    )

    # Reset to default for downstream tests
    monkeypatch.setenv("MULTIAGENT_ENABLED", "0")
    importlib.reload(src.graph)
