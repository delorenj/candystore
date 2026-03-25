# CandyStore — Agent Guide

Event persistence and audit trail service for the 33GOD ecosystem.

## Tech Stack

- **Language:** Python 3.11+
- **Framework:** FastAPI + Uvicorn
- **ORM:** SQLAlchemy 2.0 (async)
- **Database:** PostgreSQL (asyncpg) / SQLite (aiosqlite)
- **Migrations:** Alembic
- **Messaging:** aio-pika (RabbitMQ consumer)
- **Logging:** structlog
- **Metrics:** prometheus-client
- **CLI:** Typer
- **Package Manager:** uv (hatchling build backend)

## Commands (mise)

| Task | Command |
|------|---------|
| Start Server | `mise run start` (`candystore serve`) |

## Testing

```bash
uv run pytest tests/ --cov=candystore --cov-report=term-missing
```

- asyncio_mode = auto
- Test fixtures: httpx (FastAPI), faker (test data)
- Strict markers, short tracebacks

## Conventions

- Async-first for all database and message operations
- Idempotent event processing (dedup by event ID)
- Alembic for all schema migrations — never raw SQL in production
- ruff for linting (line-length=100, target py311)

## Anti-Patterns

- Never lose events — always persist before acknowledging
- Never use synchronous database calls
- Never skip migrations for schema changes
