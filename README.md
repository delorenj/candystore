# Candystore

Durable event store + Dapr subscriber for the 33GOD platform. Receives
CloudEvents envelopes from Bloodbank's NATS-backed Dapr pub/sub, persists
them to Postgres, and exposes a read-only query API.

The primary real-world target is **Claude Code hook auditability**: every
`agent.*` event emitted by `33GOD/.claude/hooks/bloodbank-publisher.sh`
lands in Candystore with full envelope retention and indexable metadata.

## Architecture

```
Claude Code hooks (host)
      │
      │  POST /v1.0/publish/bloodbank-pubsub/event.agent.*
      ▼
bloodbank's daprd-claude-events sidecar
      │  publishes to NATS JetStream (BLOODBANK_EVENTS stream)
      ▼
bloodbank-pubsub (Dapr component, fronts NATS)
      │  routes by topic to subscribers
      ▼
candystore-daprd sidecar (this repo)
      │  POSTs CloudEvents to candystore:8683/events/claude
      ▼
candystore (FastAPI)
      │  validates + persists to Postgres
      ▼
candystore_postgres.events table
```

No broker client lives in the Candystore process. Dapr is the only ingress.

## Quick start

```bash
# 1. Bring up bloodbank with the claude-events profile.
cd ../bloodbank
docker compose --project-name bloodbank \
  --profile claude-events \
  -f compose/docker-compose.yml \
  up -d nats nats-init dapr-placement \
        claude-events-recorder daprd-claude-events

# 2. Bring up candystore.
cd ../candystore
mise run up

# 3. Fire a synthetic Claude hook event.
echo '{"session_id":"test-session-1","tool":"Read","input":{"file_path":"/tmp/x"}}' \
  | ../33GOD/.claude/hooks/bloodbank-publisher.sh tool-action

# 4. Confirm it landed.
curl -s http://localhost:8683/events?session_id=test-session-1 | jq .
```

## Repository layout

| Path                          | Contents                                          |
|-------------------------------|---------------------------------------------------|
| `src/candystore/`             | Python package                                    |
| `compose/`                    | docker-compose.yml + .env.example                 |
| `tests/`                      | pytest suite (api + database)                     |
| `scripts/`                    | Local helpers                                     |
| `Dockerfile`                  | Multi-stage build                                 |
| `pyproject.toml`              | uv-managed dependencies + build config            |
| `mise.toml`                   | Task runner                                       |

## Configuration

All settings load from environment variables (or `.env`). Defaults are
chosen so `mise run up` works on a sibling-checkout of bloodbank with
no further setup.

| Variable                 | Default                              | Purpose                                  |
|--------------------------|--------------------------------------|------------------------------------------|
| `DATABASE_URL`           | postgresql+asyncpg://…/candystore    | Async SQLAlchemy DSN                     |
| `APP_HOST`               | 0.0.0.0                              | FastAPI bind host                        |
| `APP_PORT`               | 8683                                 | FastAPI bind port                        |
| `PUBSUB_NAME`            | bloodbank-pubsub                     | Dapr pub/sub component                   |
| `SUBSCRIBE_TOPICS`       | event.agent.tool.invoked,…           | Comma-separated CloudEvents types        |
| `SUBSCRIBE_ROUTE`        | /events/claude                       | App route Dapr POSTs to                  |
| `LOG_LEVEL`              | INFO                                 | structlog level                          |
| `LOG_FORMAT`             | json                                 | `json` or `console`                      |

## Conventions

- Every envelope is stored verbatim in `events.payload` (JSONB on
  Postgres). No fields are dropped.
- `events.id` is the CloudEvents `id`. Inserts are idempotent by
  primary key; Dapr redelivers transparently.
- `routing_key` holds the CloudEvents `type` (parity with the legacy
  schema, kept for query-API stability).

## Anti-patterns

- No direct broker client. Dapr is the only ingress.
- No SQLite in production.
- No synchronous DB calls.
- No envelopes invented locally; ingest is opaque pass-through.
