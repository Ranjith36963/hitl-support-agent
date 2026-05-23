# Observability stack — Prometheus + Grafana, docker-compose

> A demo-grade deploy of the HITL Customer Support Agent with live
> metrics dashboards. Bring it up with one command; the metrics rendering
> proves the wiring works without you having to push real customer
> traffic through the system.

## Quick start

```bash
docker compose up --build
```

Then open:

| URL | What it is |
|---|---|
| <http://localhost:3000> | Grafana (anonymous viewer; no login) — opens to the **HITL Agent — Overview** dashboard |
| <http://localhost:9090> | Prometheus — scrape config view + ad-hoc query |
| <http://localhost:8000/metrics> | Raw exposition format (what Prometheus scrapes) |
| <http://localhost:8000/health> | App health check |

## What's on the dashboard

Six panels covering the production-readiness signals:

1. **Ticket flow** — tickets/sec by intent × outcome
2. **End-to-end ticket latency** (p50, p95) — pre-pause + post-approval; human-wait time is not bridged
3. **LLM latency p95** by call site (`classify`, `draft`, `drafter`, `critic`, `summarize_changes`)
4. **LLM tokens/sec** — prompt vs completion, per call site
5. **Graph-node latency p95** — which node is the bottleneck
6. **Node errors/sec** — any non-zero line is a regression

## Generating metric data to see the dashboard light up

The container app boots fine without API keys but the `/metrics` endpoint
will be empty until traffic flows. Two options:

**Option 1 — run the eval harness from the host against your OpenAI key:**

```bash
# In a separate terminal, on the host (not in a container):
LLM_PROVIDER=openai OPENAI_API_KEY=... python -m eval.run_experiments --dataset curated --no-multiagent
```

This calls the *host* Python (not the containerized app), so it doesn't
populate the container's metrics. Useful for local dev; not for the
container dashboard.

**Option 2 — wire IMAP and let real emails flow:**

```bash
# Edit .env with GMAIL_USER / GMAIL_APP_PASSWORD / SLACK_BOT_TOKEN etc.
# Then `docker compose up` will boot the IMAP listener. Send an email
# to your support inbox → metrics light up.
```

For a portfolio reviewer who just wants to see the dashboard renders,
Option 2 is the truthful demo path.

## What this stack is NOT

- Not production-grade. No TLS termination, no Grafana auth, no
  Prometheus persistence beyond container lifetime, no alertmanager.
- Not Kubernetes. A real prod deploy would use a managed Prometheus
  (e.g. Grafana Cloud, Datadog) or a sidecar pattern.
- Not multi-tenant. Single Prometheus scraping a single app.
- See [`../docs/threat_model.md`](../docs/threat_model.md) row A5 + the
  cross-cutting threats section for what real production deployment
  needs in addition.

## Files in this directory

| File | Purpose |
|---|---|
| `../Dockerfile` | App image (python:3.11-slim base) |
| `../docker-compose.yml` | 3 services: hitl-agent + prometheus + grafana |
| `prometheus.yml` | Scrape config — pulls /metrics from `hitl-agent:8000` |
| `grafana/provisioning/datasources/prometheus.yml` | Auto-adds Prometheus as the default datasource |
| `grafana/provisioning/dashboards/dashboard.yml` | Auto-loads dashboards from the folder below |
| `grafana/dashboards/hitl-overview.json` | The Overview dashboard (6 panels) |

## Stop + clean up

```bash
docker compose down            # Stop containers
docker compose down -v         # Stop + remove volumes (Grafana state)
docker compose down --rmi all  # Also remove the built image
```
