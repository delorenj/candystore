# Candystore

Candystore is the durable event audit trail for Bloodbank. It receives
CloudEvents from Dapr pub/sub, stores the full envelope in PostgreSQL, exposes
query and summary APIs, and serves a React UI from the same Python process.

## Run Locally

```bash
pip install -e '.[dev]'
python -m candystore.main  # DATABASE_URL defaults to the stack's 127.0.0.1:5434
```

The server listens on `APP_PORT` or `3001`.

## Port contract

`8683` is the canonical Candystore API/UI port on **every peer**, bound to
loopback only (`127.0.0.1:8683`, `compose.yml` maps it to the container's
`3001`). Each machine runs its own Candystore at that address; stores are never
federated or copied between machines. Consumers resolve the base URL as
`$CANDYSTORE_URL`, then `http://127.0.0.1:8683` (a blank value falls back), so
one default is correct everywhere and an override covers a non-default install
(a restore probe, the legacy bloodbank `candystore` profile on `3603`). The
other host ports are part of the same contract: Postgres `5434`, Dapr HTTP
`3504`. `CANDYSTORE_PORT`, `CANDYSTORE_POSTGRES_PORT` and
`CANDYSTORE_DAPR_HTTP_PORT` exist for one-off side-by-side runs; changing a
default is a cross-repo contract change. `tests/test_port_contract.py` guards it.

## Recover Session Context

Run `mise run cli:install`, then `candystore context latest` in any registered
project. It returns a handoff from the last three sessions across agent CLIs,
excluding the current session when its ID is available. Use `--project`,
`--since`, and `--json` for explicit scope or structured evidence.
See [the context and startup-hook guide](docs/context-handoff.md).

## Project Placement

Events record a working directory, not a project. `project_dir_map` maps one
to the other, and every project filter (`context latest`, the feed, heat maps)
reads it. The app places a directory the first time it sees one, resolved
against the `projects` table. That table is a projection of the pjangler
registry, which only the host can read, so a newly registered project appears
after the next `mise run project:sync`. Candybar runs it hourly
(`candystore-project-sync`; the old user timer is retired).

## Full-history rollups

A scheduled read model must never scan `events` (6.1M rows / 41 GB) to count
something across all history; its cost would grow with the trail, not with
what changed. A rollup is a small table that statement-level triggers on
`events` keep exact in the same transaction as every INSERT, DELETE, UPDATE
and TRUNCATE (`migrations/011_event_rollups.sql`). `work_dirs` (events per
working directory) feeds project:sync, which used to `GROUP BY` the whole
trail every hour (11-26 s) and now reads ~2k rows.

- `python -m candystore.rollups seed` adds the history that predates the
  triggers, once, exactly and without locking out ingest. project:sync seeds
  an unready rollup itself.
- `mise run rollups:verify` proves each rollup equals a full scan taken in the
  same snapshot. `mise run rollups:status` shows readiness and seed cost.
- A new rollup: copy 011's table + four triggers, register it in `ROLLUPS`
  (`candystore/rollups.py`) with the identical key expression.

## Index health

`mise run db:check` runs amcheck `bt_index_check` on every btree index
(`heapallindexed` on tables under 2 GB) and reports collation-version drift;
it exits 1 on either. The app also logs drift at boot. Why: this cluster was
initdb'd on musl (postgres:16-alpine) and moved to glibc
(pgvector/pgvector:pg16) on 2026-10-06 with no REINDEX, after which
`project_dir_map_pkey` admitted 63 duplicate keys. The Postgres image is
pinned to `pg16-bookworm` so a pull can never change the C library under the
data dir.

## API

- `GET /healthz`
- `GET /readyz`
- `GET /dapr/subscribe`
- `POST /events/all`
- `GET /events`
- `GET /events/:id`
- `GET /events/:id/summary`
- `GET /events/:id/raw`
- `GET /sessions/:correlationid`
- `GET /sessions/:correlationid/summary`
- `GET /context/latest`
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
