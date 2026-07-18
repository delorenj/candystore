# Candystore

Candystore is the durable event audit trail for Bloodbank. It receives
CloudEvents from Dapr pub/sub, stores the full envelope in PostgreSQL, exposes
query and summary APIs, and serves a React UI from the same Python process.

Candystore also maintains a replay-safe, version-ordered **read projection** of
authoritative Lifecycle v3 snapshot events and stable command replies. The
projection preserves the authority-owned `capability_version` on every grant,
each obligation's `obligation_instance_id` and `activated_at`, and the exact
snapshot event/correlation/causation lineage used by command clients.
Lifecycle remains the sole writer of operational lifecycle truth; Candystore exposes no
Lifecycle mutation endpoint. Snapshot events are consumed from the durable
`BLOODBANK_EVENTS` stream and replies from a dedicated durable consumer on
`BLOODBANK_COMMANDS`, so restart catch-up does not depend on core subscriptions.

## Run Locally

```bash
pip install -e '.[dev]'
DATABASE_URL=postgresql://candystore:candystore@localhost:5432/candystore \
  BLOODBANK_SCHEMAS_DIR=../bloodbank/schemas \
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

Event insertion and projection share one PostgreSQL transaction. After an
insert attempt, including an ID conflict, Candystore locks and projects the
canonical `events.raw` row already stored for that ID. It never applies the
conflicting incoming body. Snapshot and reply candidates must validate against
the exact mounted Bloodbank schema and Lifecycle's canonical source, producer,
service, actor, subject/type/kind/domain, schema version, and provenance before
a projection receipt is claimed. A contract-invalid or spoofed candidate stays
in append-only audit history but is excluded from the read model and receives
no receipt. An operational projection error aborts a new audit insert and its
receipt together; for a pre-existing audit row it leaves that row unchanged and
commits no receipt, so durable redelivery retries the same canonical body.
