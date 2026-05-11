# Candystore — Agent Guide

Durable event store + Dapr subscriber for the 33GOD platform. Receives
CloudEvents from Bloodbank's NATS-backed Dapr pub/sub, persists them to
Postgres, exposes a read-only query API.

## Stack

- **Language:** Python 3.12
- **Framework:** FastAPI + Uvicorn
- **ORM:** SQLAlchemy 2.0 (async, asyncpg)
- **Database:** PostgreSQL 16 (durable). SQLite is supported for local dev only.
- **Subscriber transport:** Dapr (HTTP) — the sidecar pulls from NATS and POSTs
  CloudEvents envelopes to `POST /events/claude`. No broker client lives in
  this process.
- **CLI:** Typer (`candystore serve`, `candystore init-db`, `candystore version`).
- **Logging:** structlog (JSON by default).
- **Metrics:** prometheus-client on `:9090`.

## Layout

| Path                          | Role                                              |
|-------------------------------|---------------------------------------------------|
| `src/candystore/api.py`       | FastAPI app: Dapr subscribe + query API           |
| `src/candystore/cli.py`       | Typer entry point                                 |
| `src/candystore/config.py`    | Pydantic settings                                 |
| `src/candystore/database.py`  | SQLAlchemy async engine + store/query operations  |
| `src/candystore/models.py`    | `StoredEvent` table                               |
| `src/candystore/metrics.py`   | Prometheus counters + histograms                  |
| `src/candystore/logging_config.py` | structlog wiring                             |
| `Dockerfile`                  | Multi-stage build (uv → distroless-ish runtime)   |
| `compose/docker-compose.yml`  | Postgres + candystore + daprd-candystore          |
| `compose/.env.example`        | Env override template                             |

## mise tasks

| Task | Purpose |
|------|---------|
| `mise run serve` | Boot the FastAPI app locally (no Docker) |
| `mise run db:init` | Run `candystore init-db` (create_all) |
| `mise run up` | Boot the full stack via Docker Compose |
| `mise run down` | Stop containers (volumes preserved) |
| `mise run down:wipe` | Stop + drop postgres volume |
| `mise run logs` | Tail container logs |
| `mise run ps` | List running services |
| `mise run test` | Run pytest with coverage |
| `mise run lint` | ruff |

## Subscribe surface

`GET /dapr/subscribe` advertises one subscription per CloudEvents `type`
in `settings.subscribe_topics` (default covers the full Claude Code
agent.* surface). All routes target `POST /events/claude`. The component
name is `bloodbank-pubsub` and is loaded from bloodbank's component
manifests via a read-only mount.

## Conventions

- Idempotency is enforced by the StoredEvent primary key (envelope `id`).
  Duplicate inserts fall through into a RETRY response, which Dapr
  redelivers; the next attempt sees the existing row and skips cleanly.
- Every persisted row keeps the **entire** CloudEvents envelope in
  `payload`. No field is dropped on the floor.
- The `routing_key` column carries the CloudEvents `type` (parity with
  the legacy schema; the column is kept for query-API back-compat).

## Anti-patterns

- No direct broker client. Dapr is the only ingress.
- No SQLite in production. The default is Postgres; SQLite exists only
  for unit tests and developer sandboxes.
- No synchronous DB calls.
- No envelopes invented locally; ingest is opaque pass-through.
