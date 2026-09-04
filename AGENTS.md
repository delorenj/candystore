# CandyStore - Agent Guide

Candystore is the Bloodbank durable event audit trail. Keep it aligned with
`PLAN.md` and `../docs/plans/candystore-architecture.md`.

## Architecture

- Python 3.11+ stdlib HTTP server in `candystore/main.py`
- Dapr programmatic subscription via `GET /dapr/subscribe`
- PostgreSQL persistence through `psycopg2`
- Plain SQL migrations in `migrations/`
- React/Vite/Tailwind UI in `web/`, built into `static/`
- Free-text search over a generated `search_text` column indexed with a
  `pg_trgm` GIN index (`migrations/003_search.sql`, re-capped by `004`)
- No FastAPI, RabbitMQ consumer, SQLAlchemy, or Alembic in this implementation
- Frontend state: `useReducer` + one page-level context. **TanStack Store was evaluated and rejected** (2026-09-04, CANDYS-52) — the hard part here is server-state lifecycle (cancellation, keep-previous, append-not-refetch, several derived views off one row array), which a client-state store solves none of. `@tanstack/react-virtual` *is* planned, for the live feed's fixed-height rows (CANDYS-57).
- The live project feed is designed in `_bmad-output/candystore-live-feed-plan.md` (epics E8–E14, CANDYS-32…68), with its evidence in `_bmad-output/candystore-live-feed-measurements.md`. Read it before touching `web/src/pages/EventList.jsx`, `query.py:PROJECT_EXPR`, or adding any route under `/events/`.

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

- Free-text search across the whole trail: `GET /events?q=<terms>`. It matches
  file paths, tool names, branches, commands, models, statuses and error text,
  and it composes with every other filter (`cli`, `project`, `scope`, `from`,
  `to`). Whitespace separates terms that must ALL match, so `q=holocene+traefik`
  means "traefik work in holocene" and not one literal phrase. Pasting an event
  or session UUID resolves it exactly. Terms under 3 characters are refused with
  a 400 -- the trigram index cannot serve them and the query would degrade to a
  full scan of the trail.

  **What is in the haystack was measured, not guessed** (see `004_search_caps.sql`).
  The trail is 95% tool calls: `arguments` is on 89.4% of rows and `prompt_text`
  on 0.69%, so tool arguments are the most valuable field in the index, not the
  noisy one -- dropping them takes a search for `query.py` from 291 hits to 2.
  Two fields were removed for being dead weight: `input_preview` (present on 0 of
  871,438 rows) and `payload` (85.5% UUID, 71% a restatement of `arguments`).
  Net: a 273 MB index, 14% smaller than the first attempt, that searches more.

  Two bounds remain, and they are the reason a search can miss:
  - **`arguments` is capped at 256 characters.** 361,298 rows carry a longer one,
    so a flag or path late in a long shell pipeline is invisible. Measured cost
    of widening it to 512: +62.6 MB of text, ~+55 MB of index. Not applied.
  - **`prompt_text` is capped at 4000**, which fully indexes 88.5% of the 6,012
    prompt-bearing rows. The rest are enormous (max 287 KB).

- [TODO] SO many more things! Keep this list growing!
