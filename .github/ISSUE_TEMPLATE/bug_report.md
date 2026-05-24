---
name: Bug report
about: Something broken or behaving unexpectedly in the agent / eval / observability stack
title: "[bug] "
labels: bug
---

## What you expected

<!-- One sentence. -->

## What actually happened

<!-- One sentence. Paste the error / unexpected behaviour. -->

## Repro

```bash
# minimal command(s) that reproduce the issue
```

If the bug involves a specific ticket / Slack interaction / email, include:
- the customer message (redacted if needed)
- the Slack approval card you saw (paste, not screenshot — easier to grep)
- the relevant lines from `docker compose logs hitl-agent`
- the LangSmith trace URL if you have one

## Environment

- OS:
- Python version (`python --version`):
- Install path: `pip install -r requirements.txt && pip install -e .[dev]` OR Docker?
- `MULTIAGENT_ENABLED`: `0` or `1`?
- LLM provider: OpenRouter / OpenAI (set by `LLM_PROVIDER` env)
- Commit hash (`git rev-parse HEAD`):

## Security note

If this is a security issue (auth bypass, PII leak, capability-isolation bypass, signature forgery), please **do not** file it here. Use the private flow in [`SECURITY.md`](../../SECURITY.md).
