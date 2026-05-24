# Contributing

This is a portfolio project, but contributions / suggestions / bug reports
are welcome. The repo aims to stay honest: every claim in the docs should
match the code.

## Quick start (development)

```bash
git clone https://github.com/Ranjith36963/hitl-support-agent.git
cd hitl-support-agent
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -e .[dev]              # runtime + test/lint/security tooling
cp .env.example .env               # fill in real keys before running the server

pytest -q                          # 148 tests should pass
ruff check .
mypy
bandit -r src mcp_server -ll
pip-audit --strict
```

`pip install -e .[dev]` pulls in `pytest`, `mypy`, `ruff`, `pip-audit`, and
`bandit` from `[project.optional-dependencies].dev` in `pyproject.toml`.
The runtime install (`pip install -r requirements.txt`) deliberately omits
all of that so Docker images stay small.

> **No lock file.** `requirements.txt` uses `>=` lower-bounds; no `uv.lock`
> / `poetry.lock` / `requirements.lock` is committed. Transitive deps will
> resolve to whatever's current on PyPI at install time, so the exact
> dependency graph drifts across clones. Acceptable for a portfolio
> project; if you hit a "works on my machine" issue, snapshot the
> versions with `pip freeze > my-environment.txt` and attach to the
> issue. A real lock file is a future enhancement once the project has
> multiple contributors.

## Running the agent end-to-end

```bash
docker compose up -d --build       # hitl-agent + Prometheus + Grafana
open http://127.0.0.1:3000         # Grafana dashboard (anonymous viewer)
open http://127.0.0.1:9090         # Prometheus
open http://127.0.0.1:8000/health  # FastAPI health probe
open http://127.0.0.1:8000/metrics # raw Prometheus exposition
```

Send a test email to your configured `GMAIL_USER` from another address; the
agent classifies, drafts, gates, and either auto-sends or escalates to Slack.

## Commit style

Conventional Commits:

- `feat(scope): short summary` — new functionality
- `fix(scope): short summary` — bug fix
- `docs(scope): short summary` — docs only
- `chore(scope): short summary` — tooling, deps, CI
- `refactor(scope): short summary` — no behaviour change

Multi-line commit bodies explain the *why*, not the *what* (the diff shows
the what). For non-trivial changes, include test evidence in the body.

## Pull request checklist

- [ ] `pytest -q` passes (locally and in CI)
- [ ] `ruff check .` clean
- [ ] `mypy` strict clean (`pyproject.toml` enforces `strict = true`)
- [ ] `bandit -r src mcp_server -ll` clean
- [ ] `pip-audit --strict` clean (or new CVE documented + suppressed
      with explicit justification)
- [ ] Any new doc claim has a corresponding code / test / eval artifact —
      see the "honest claims" rule below
- [ ] If you touched the graph, you ran a manual end-to-end smoke against
      real Gmail + real Slack and reported the outcome in the PR body

## The honesty rule

The project's headline differentiator is honesty: every concrete claim
in `README.md`, `CLAUDE.md`, `HOW_IT_WORKS.md`, `docs/`, and the eval
findings docs must be backed by code, a passing test, or a real eval
artifact. PRs that move docs *or* code should keep the two in sync.

If a claim can't be backed up, mark it explicitly as deferred /
aspirational / TODO. Silent drift is the bug class this project most
wants to avoid.

## Reporting a security issue

See [`SECURITY.md`](./SECURITY.md). Do not open public GitHub issues
for security findings.

## License

By submitting a contribution you agree to license it under the
[MIT License](./LICENSE).
