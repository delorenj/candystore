from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json

DEFAULT_DATABASE_URL = "postgresql://candystore:candystore@localhost:5432/candystore"

REQUIRED_ENVELOPE_FIELDS = (
    "id",
    "source",
    "type",
    "time",
    "producer",
    "service",
    "domain",
    "kind",
)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _connect():
    return psycopg2.connect(database_url())


@contextmanager
def cursor() -> Iterator[Any]:
    conn = _connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """Run migrations/*.sql in lexical order."""
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    if not migrations_dir.exists():
        return

    with cursor() as cur:
        for path in sorted(migrations_dir.glob("*.sql")):
            cur.execute(path.read_text(encoding="utf-8"))


def check_connection() -> bool:
    with cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)


def insert_event(envelope: dict[str, Any]) -> bool:
    """Insert a CloudEvents envelope.

    Returns True when a new row was inserted and False when the event ID
    already existed. Duplicate IDs are intentionally treated as success so Dapr
    does not retry already-persisted messages.
    """
    _validate_envelope(envelope)

    sql = """
    INSERT INTO events (
        id, specversion, source, type, subject, time, datacontenttype,
        dataschema, correlationid, causationid, producer, service, domain,
        schemaref, traceparent, kind, actor, data, ordering_key, raw
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
    RETURNING id
    """
    actor = envelope.get("actor")
    data = envelope.get("data")
    params = (
        str(uuid.UUID(str(envelope["id"]))),
        envelope.get("specversion", "1.0"),
        envelope.get("source"),
        envelope.get("type"),
        envelope.get("subject"),
        envelope.get("time"),
        envelope.get("datacontenttype"),
        envelope.get("dataschema"),
        _uuid_or_none(envelope.get("correlationid")),
        _uuid_or_none(envelope.get("causationid")),
        envelope.get("producer"),
        envelope.get("service"),
        envelope.get("domain"),
        envelope.get("schemaref"),
        envelope.get("traceparent"),
        envelope.get("kind"),
        Json(actor) if actor is not None else None,
        Json(data) if data is not None else None,
        envelope.get("ordering_key"),
        Json(envelope),
    )

    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


def _validate_envelope(envelope: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_ENVELOPE_FIELDS if not envelope.get(field)]
    if missing:
        raise ValueError(f"missing CloudEvents fields: {', '.join(missing)}")
    uuid.UUID(str(envelope["id"]))


def _uuid_or_none(val: Any) -> str | None:
    if not val:
        return None
    try:
        return str(uuid.UUID(str(val)))
    except (TypeError, ValueError, AttributeError):
        return None
