<!-- Short PR description: what changed and why (the diff shows how). -->

## Summary

<!-- 1-2 sentences. Lead with the user-visible / system-visible change. -->

## Why

<!-- The motivation. What problem does this solve? Link an issue if applicable. -->

## Test evidence

- [ ] `pytest -q` passes locally (148 tests baseline; add new tests if you added new code)
- [ ] `ruff check .` clean
- [ ] `mypy` strict clean
- [ ] `bandit -r src mcp_server -ll` clean
- [ ] `pip-audit --strict -r requirements.txt` clean

## Manual smoke (if you touched the graph)

- [ ] Sent a real email through `docker compose up` and watched it flow end-to-end
- [ ] Result captured in PR body (which channel, what the agent did, terminal state)

## Honesty rule

- [ ] Every new / changed concrete claim in `README.md`, `CLAUDE.md`, `HOW_IT_WORKS.md`, `docs/`, `eval/*.md` is backed by code, a passing test, or a real eval artifact
- [ ] If a claim is deferred / aspirational, it's marked as such

## Anything else reviewers should know

<!-- Trade-offs, known gaps, follow-ups. -->
