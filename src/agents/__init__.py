"""Multi-agent layer (v4) — Researcher, Drafter, Critic.

Each agent is a compiled LangGraph sub-graph slotted into the parent graph
in place of an existing v3 node. Hard invariants preserved per
docs/v4_multiagent.md.

Activation: set MULTIAGENT_ENABLED=1 in env. Default 0 (v3 single-agent).
"""
