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
```

## Runtime

Set `DATABASE_URL` to a PostgreSQL database. Duplicate CloudEvent IDs must be
handled idempotently and return HTTP 200 to prevent Dapr retry loops.
