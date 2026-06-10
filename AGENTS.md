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

## Runtime

Set `DATABASE_URL` to a PostgreSQL database. Duplicate CloudEvent IDs must be
handled idempotently and return HTTP 200 to prevent Dapr retry loops. The same
applies to malformed events: respond 200 with `{"status": "DROP"}` — Dapr
treats any non-2xx as retriable and will redeliver forever.

`compose.yml` is the deployment entry point (app on :8683, postgres on
127.0.0.1:5434, dapr HTTP on 127.0.0.1:3504). It joins the external
`bloodbank-network` for NATS/placement. Never run it alongside the legacy
`candystore` profile in `bloodbank/compose/docker-compose.yml` — both
sidecars share the JetStream durable `candystore-events` and would split
events between two databases.
