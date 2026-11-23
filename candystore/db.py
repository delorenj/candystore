from __future__ import annotations

import logging
import multiprocessing
import os
import re
import shlex
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger("candystore.db")

# The deployed stack publishes its Postgres on 127.0.0.1:5434 (compose.yml and
# the platform compose). 5432 is another server on this host, so the old
# localhost:5432 default failed auth for every bare host-side run, including
# `mise run project:sync`.
DEFAULT_DATABASE_URL = "postgresql://candystore:candystore@127.0.0.1:5434/candystore"

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


DEFAULT_CONNECT_TIMEOUT_SECONDS = 3
DEFAULT_STATEMENT_TIMEOUT_MS = 5000
BOOT_MAX_ATTEMPTS = 4
BOOT_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0)
DEFAULT_BOOT_DEADLINE_SECONDS = 60


def _positive_int(value: str, name: str) -> int:
    if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 2_147_483_647:
        raise ValueError(f"{name} must be a positive integer <= 2147483647")
    return int(value)


class _BoundedCursor(psycopg2.extensions.cursor):
    """Keep context's transaction-local cap from relaxing the session budget."""

    def execute(self, query, vars=None):
        if query == "SET LOCAL statement_timeout = '2500ms'":
            # SHOW is a server round-trip, but no client-side estimate of the
            # current session setting can account for prior SET LOCAL calls.
            super().execute("SHOW statement_timeout")
            current = super().fetchone()
            current_ms = (
                _statement_option(f"-c statement_timeout={current[0]}") if current else None
            )
            if current_ms is not None and current_ms < 2500:
                return None
        return super().execute(query, vars)


def _statement_option(options: str) -> int | None:
    """Read a preexisting libpq statement_timeout; refuse values we cannot bound."""
    tokens = shlex.split(options)
    values = []
    for index, token in enumerate(tokens):
        if token == "-c" and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        elif token.startswith("-c") and token != "-c":
            values.append(token[2:])
        elif token.startswith("--"):
            values.append(token[2:])
    timeouts = []
    for value in values:
        key, sep, raw = value.partition("=")
        if key != "statement_timeout":
            continue
        if not sep:
            raise ValueError("statement_timeout option must have a value")
        match = re.fullmatch(r"([0-9]+)(ms|s|min|h|d)?", raw)
        factor = (
            {None: 1, "ms": 1, "s": 1000, "min": 60000, "h": 3600000, "d": 86400000}[match[2]]
            if match
            else 1
        )
        if not match or not 1 <= int(match[1]) * factor <= 2_147_483_647:
            raise ValueError("statement_timeout option must be a finite positive duration")
        timeouts.append(int(match[1]) * factor)
    return timeouts[-1] if timeouts else None


def _connect():
    # Parse rather than reconstructing a URL: sslmode, application_name, host,
    # credentials and any other libpq parameters survive unchanged.
    params = psycopg2.extensions.parse_dsn(database_url())
    timeout = _positive_int(
        os.environ.get("CANDYSTORE_CONNECT_TIMEOUT_SECONDS", str(DEFAULT_CONNECT_TIMEOUT_SECONDS)),
        "CANDYSTORE_CONNECT_TIMEOUT_SECONDS",
    )
    statement = _positive_int(
        os.environ.get("CANDYSTORE_STATEMENT_TIMEOUT_MS", str(DEFAULT_STATEMENT_TIMEOUT_MS)),
        "CANDYSTORE_STATEMENT_TIMEOUT_MS",
    )
    if "connect_timeout" in params:
        existing = _positive_int(params["connect_timeout"], "DSN connect_timeout")
        timeout = min(timeout, existing)
    if "PGCONNECT_TIMEOUT" in os.environ:
        timeout = min(timeout, _positive_int(os.environ["PGCONNECT_TIMEOUT"], "PGCONNECT_TIMEOUT"))
    # URL options override PGOPTIONS in libpq. Append our tighter cap last,
    # leaving every unrelated server option in its original order.
    options = params.get("options", os.environ.get("PGOPTIONS", ""))
    existing_statement = _statement_option(options)
    if existing_statement is not None:
        statement = min(statement, existing_statement)
    params["connect_timeout"] = timeout
    params["options"] = f"{options} -c statement_timeout={statement}ms".strip()
    return psycopg2.connect(**params)


@contextmanager
def cursor() -> Iterator[Any]:
    conn = _connect()
    try:
        cur = conn.cursor(cursor_factory=_BoundedCursor)
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


def _transient_boot_error(exc: psycopg2.OperationalError) -> bool:
    code = exc.pgcode
    if code is not None:
        return code.startswith("08") or code in {"57P01", "57P02", "57P03", "53300"}
    if isinstance(exc, psycopg2.errors.CrashShutdown):
        return True
    message = str(exc).lower()
    return any(
        fragment in message
        for fragment in (
            "connection refused",
            "could not connect",
            "connection timed out",
            "timeout expired",
            "server closed the connection",
            # libpq's local Unix socket path when PostgreSQL has not started.
            "no such file or directory",
        )
    ) and ("no such file or directory" not in message or "socket" in message)


def _boot_worker(pipe, deadline: int) -> None:
    try:
        # Also bound the server's own work: killing a libpq client does not
        # instantly interrupt an already-running PostgreSQL statement.
        configured = _positive_int(
            os.environ.get("CANDYSTORE_STATEMENT_TIMEOUT_MS", str(DEFAULT_STATEMENT_TIMEOUT_MS)),
            "CANDYSTORE_STATEMENT_TIMEOUT_MS",
        )
        os.environ["CANDYSTORE_STATEMENT_TIMEOUT_MS"] = str(min(configured, deadline * 1000))
        init_schema_with_retry()
        pipe.send((True, None))
    except BaseException as exc:
        # Transport the original exception where possible, including SQLSTATE.
        try:
            pipe.send((False, exc))
        except (TypeError, ValueError, OSError):
            pipe.send((False, RuntimeError(f"schema initialization failed: {exc}")))
    finally:
        pipe.close()


def init_schema_with_deadline() -> None:
    """Bound the whole boot transaction, including DNS, all SQL and network I/O.

    Boot runs in a disposable process: terminating it closes its libpq socket,
    making PostgreSQL abort any uncommitted migration transaction. A thread
    cannot safely interrupt libpq's blocking DNS or socket operations.
    """
    deadline = _positive_int(
        os.environ.get("CANDYSTORE_BOOT_DEADLINE_SECONDS", str(DEFAULT_BOOT_DEADLINE_SECONDS)),
        "CANDYSTORE_BOOT_DEADLINE_SECONDS",
    )
    if os.name != "posix" or "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("bounded database startup requires POSIX fork")
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_boot_worker, args=(child, deadline), name="candystore-schema-boot"
    )
    started = time.monotonic()
    try:
        process.start()
        child.close()
        remaining = max(0, deadline - (time.monotonic() - started))
        if not parent.poll(remaining):
            raise TimeoutError(f"database schema initialization exceeded {deadline}s boot deadline")
        try:
            success, error = parent.recv()
        except EOFError as exc:
            raise RuntimeError(
                "database schema initialization worker exited without a result"
            ) from exc
        if not success:
            raise error
        # A result is only successful when the worker has actually exited.
        process.join(timeout=max(0, deadline - (time.monotonic() - started)))
        if process.is_alive():
            raise TimeoutError(f"database schema initialization exceeded {deadline}s boot deadline")
        if process.exitcode != 0:
            raise RuntimeError(f"database schema initialization worker exited {process.exitcode}")
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
            process.join()


def init_schema_with_retry() -> None:
    """Retry only transient startup availability faults, never SQL/migration errors."""
    # PostgreSQL reports canceled statements as OperationalError (57014),
    # but retrying a broken migration would just rerun it four times.
    for attempt in range(BOOT_MAX_ATTEMPTS):
        try:
            init_schema()
            return
        except psycopg2.OperationalError as exc:
            if not _transient_boot_error(exc) or attempt == BOOT_MAX_ATTEMPTS - 1:
                raise
            delay = BOOT_RETRY_DELAYS_SECONDS[attempt]
            logger.warning(
                "database not ready (attempt %s/%s); retrying in %ss",
                attempt + 1,
                BOOT_MAX_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def check_connection() -> bool:
    with cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() == (1,)


def insert_event(envelope: dict[str, Any], sanitized: bool = False) -> bool:
    """Insert a CloudEvents envelope.

    Returns True when a new row was inserted and False when the event ID
    already existed. Duplicate IDs are intentionally treated as success so Dapr
    does not retry already-persisted messages.

    ``sanitized`` records that the envelope had a PostgreSQL-unstorable value
    (e.g. a NUL) stripped before insert (see ``sanitize_envelope``); the caller
    is responsible for stripping — this only sets the marker column.
    """
    _validate_envelope(envelope)

    sql = """
    INSERT INTO events (
        id, specversion, source, type, subject, time, datacontenttype,
        dataschema, correlationid, causationid, producer, service, domain,
        schemaref, traceparent, kind, actor, data, ordering_key, sanitized, raw
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        sanitized,
        Json(envelope),
    )

    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


def record_dead_letter(
    raw: bytes | str,
    *,
    reason: str,
    topic: str | None = None,
    error: str | None = None,
    event_id: str | None = None,
) -> bool:
    """Persist an event candystore refused to store, preserving the exact bytes.

    Best-effort: this MUST NOT raise. It runs on the DROP path, and a failure
    here (e.g. DB briefly down) must not turn a known-bad event back into a 500
    that Dapr redelivers forever. On failure it logs and returns False.
    """
    try:
        if isinstance(raw, bytes | bytearray):
            raw_bytes = bytes(raw)
        else:
            raw_bytes = str(raw).encode("utf-8", "replace")
        # The TEXT columns cannot hold a NUL either; strip it so recording a
        # NUL-bearing error message can never itself fail and lose the record.
        with cursor() as cur:
            cur.execute(
                "INSERT INTO dead_letter (event_id, topic, reason, error, raw) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    _no_nul(event_id),
                    _no_nul(topic),
                    _no_nul(reason),
                    _no_nul(error),
                    psycopg2.Binary(raw_bytes),
                ),
            )
        return True
    except Exception:
        logger.exception("failed to record dead_letter (reason=%s topic=%s)", reason, topic)
        return False


def _strip_nul(value: Any) -> tuple[Any, bool]:
    """Recursively strip U+0000 from strings. Postgres jsonb/text cannot store
    a NUL, so an event carrying one raises DataError and (pre-fix) poison-loops.
    Returns (clean_value, changed)."""
    if isinstance(value, str):
        return (value.replace("\x00", ""), True) if "\x00" in value else (value, False)
    if isinstance(value, dict):
        changed = False
        out: dict[Any, Any] = {}
        for key, val in value.items():
            clean_key, k_changed = _strip_nul(key)
            clean_val, v_changed = _strip_nul(val)
            out[clean_key] = clean_val
            changed = changed or k_changed or v_changed
        return out, changed
    if isinstance(value, list):
        changed = False
        out_list = []
        for item in value:
            clean_item, i_changed = _strip_nul(item)
            out_list.append(clean_item)
            changed = changed or i_changed
        return out_list, changed
    return value, False


def sanitize_envelope(envelope: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (clean_envelope, sanitized) with every NUL char removed."""
    clean, changed = _strip_nul(envelope)
    return clean, changed


def _no_nul(value: str | None) -> str | None:
    return value.replace("\x00", "") if isinstance(value, str) else value


def _validate_envelope(envelope: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_ENVELOPE_FIELDS if not envelope.get(field)]
    if missing:
        raise ValueError(f"missing CloudEvents fields: {', '.join(missing)}")
    uuid.UUID(str(envelope["id"]))
    _validate_time(envelope["time"])


def _validate_time(value: Any) -> None:
    """Reject an unparseable timestamp at validation (→ DROP + dead_letter) so a
    bad `time` surfaces with a clear reason instead of a raw DB DataError. A
    wrongly-rejected value is still recoverable from dead_letter, so strictness
    here never means silent loss."""
    text = str(value).strip()
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid time: {value!r}") from exc


def _uuid_or_none(val: Any) -> str | None:
    if not val:
        return None
    try:
        return str(uuid.UUID(str(val)))
    except (TypeError, ValueError, AttributeError):
        return None
