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
after the next `mise run project:sync`. `mise run project:sync:install` adds an
hourly user timer that runs it.

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
