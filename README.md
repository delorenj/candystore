# Candystore

Candystore is the durable event audit trail for Bloodbank. It receives
CloudEvents from Dapr pub/sub, stores the full envelope in PostgreSQL, exposes
query and summary APIs, and serves a React UI from the same Python process.

Candystore also maintains a replay-safe, version-ordered **read projection** of
authoritative Lifecycle snapshot events and stable command replies. Lifecycle
remains the sole writer of operational lifecycle truth; Candystore exposes no
Lifecycle mutation endpoint. Snapshot events are consumed from the durable
`BLOODBANK_EVENTS` stream and replies from a dedicated durable consumer on
`BLOODBANK_COMMANDS`, so restart catch-up does not depend on core subscriptions.

## Run Locally

```bash
pip install -e '.[dev]'
DATABASE_URL=postgresql://candystore:candystore@localhost:5432/candystore \
  python -m candystore.main
```

The server listens on `APP_PORT` or `3001`.

## API

- `GET /healthz`
- `GET /readyz`
- `GET /dapr/subscribe`
- `POST /events/all`
- `POST /events/lifecycle_intent_reply` (Dapr transport callback only)
- `GET /lifecycles`
- `GET /lifecycles/:lifecycle_id`
- `GET /events`
- `GET /events/:id`
- `GET /events/:id/summary`
- `GET /events/:id/raw`
- `GET /sessions/:correlationid`
- `GET /sessions/:correlationid/summary`
- `GET /summary/heatmap`
- `GET /summary/daily`
- `GET /summary/by-cli`
- `GET /summary/by-project`

## Development

```bash
mise run install
mise run test
mise run lint
cd web && npm install && npm run build
```

Postgres-backed tests use `CANDYSTORE_TEST_DATABASE_URL` or `DATABASE_URL`.
Missing Lifecycle projections return explicit `status=unknown`,
`health=degraded`, and `projection_status=missing`. A projection whose
authoritative freshness window has expired retains the original state under
`authority_state` while its display health is degraded.
