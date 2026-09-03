# CandyStore - Agent Guide

Candystore is the Bloodbank durable event audit trail. Keep it aligned with
`PLAN.md` and `../docs/plans/candystore-architecture.md`.

## Architecture

- Python 3.11+ stdlib HTTP server in `candystore/main.py`
- Dapr programmatic subscription via `GET /dapr/subscribe`
- PostgreSQL persistence through `psycopg2`
- Plain SQL migrations in `migrations/`
- React/Vite/Tailwind UI in `web/`, built into `static/`
- No FastAPI, RabbitMQ consumer, SQLAlchemy, or Alembic in this implementation
- PLANNED: <https://tanstack.com/store/latest> as a state management library

## Commands

```bash
pip install -e '.[dev]'
pytest tests/ -v
ruff check candystore/ tests/
cd web && npm install && npm run build
python -m candystore.main
mise run up      # full stack: postgres + app + dapr sidecar (compose.yml)
mise run down    # stop stack, volumes preserved
```

> Mise Tasks are the entry point for all workflows, dev functions, scripts, etc.

## Runtime

Set `DATABASE_URL` to a PostgreSQL database. Duplicate CloudEvent IDs must be
handled idempotently and return HTTP 200 to prevent Dapr retry loops. The same
applies to malformed events: respond 200 with `{"status": "DROP"}` — Dapr
treats any non-2xx as retriable and will redeliver forever.

The ingest+query API has **no authentication** and holds the full org-wide
event history, so every host port is published on **127.0.0.1 only**. The app
binds 0.0.0.0 _inside_ the container (via `APP_HOST=0.0.0.0` in compose) purely
so the Dapr sidecar can reach it over `candystore-internal`; `main.py` defaults
`APP_HOST` to 127.0.0.1 for bare local runs. Remote/Candybar access must go
through an authenticating reverse proxy (Traefik + Cloudflare Access).

`compose.yml` is the deployment entry point (app on 127.0.0.1:8683, postgres on
127.0.0.1:5434, dapr HTTP on 127.0.0.1:3504). It joins the external
`bloodbank-network` for NATS/placement. Never run it alongside the legacy
`candystore` profile in `bloodbank/compose/docker-compose.yml` — both
sidecars share the JetStream durable `candystore-events` and would split
events between two databases.

## Built to support Agents

- Agents are the primary consumers of the CandyStore API. Always keep that in mind when designing and/or expanding functionality in the API, CLI, or UI.

- CandyStore should also provide a rich skill library that synergizes with the underlying CLI and API that advertise the capabilities the agents can tap into when working across the 33GOD pipeline.

## Things CandyStore Offers to Agents

- When switching between agents, a CandyStore query can return a list of every top-level (orchestrator) agent action instead of the full session history. When it finds an action of interest, it can dive deeper and query all subagents and/or tool calls bound by a parent action.

- Heat maps can be generated across multiple dimensions like project activity, agent clis, topic, etc.

- System-wide time-bound recap reports can be generated to help agents understand the state of the system at a given point in time.

- [TODO] SO many more things! Keep this list growing!
