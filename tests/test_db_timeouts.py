from __future__ import annotations

import time

import psycopg2
import pytest

from candystore import db, main


def test_connection_defaults_and_preserved_options(monkeypatch):
    calls = []
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@localhost:9999/test?sslmode=require&application_name=probe&connect_timeout=2&options=-c%20search_path%3Dpublic%20-c%20statement_timeout%3D1200ms",
    )
    monkeypatch.setattr(db.psycopg2, "connect", lambda **kw: calls.append(kw))
    db._connect()
    params = calls[0]
    assert params["sslmode"] == "require"
    assert params["application_name"] == "probe"
    assert params["connect_timeout"] == 2
    assert "search_path=public" in params["options"]
    assert params["options"].endswith("-c statement_timeout=1200ms")


@pytest.mark.parametrize(
    "name,value",
    [
        ("CANDYSTORE_CONNECT_TIMEOUT_SECONDS", "0"),
        ("CANDYSTORE_CONNECT_TIMEOUT_SECONDS", "-1"),
        ("CANDYSTORE_STATEMENT_TIMEOUT_MS", "nan"),
        ("CANDYSTORE_STATEMENT_TIMEOUT_MS", "0"),
        ("CANDYSTORE_STATEMENT_TIMEOUT_MS", "2147483648"),
    ],
)
def test_invalid_config_fails_before_connect(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setattr(db.psycopg2, "connect", lambda **kw: pytest.fail("connected"))
    with pytest.raises(ValueError):
        db._connect()


def test_unbounded_dsn_and_pgoptions_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test?connect_timeout=0")
    with pytest.raises(ValueError, match="connect_timeout"):
        db._connect()
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=0")
    with pytest.raises(ValueError, match="statement_timeout"):
        db._connect()


def test_pgoptions_and_env_tighter_bound(monkeypatch):
    calls = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "1")
    monkeypatch.setenv("PGOPTIONS", "-c application_name=probe -c statement_timeout=900ms")
    monkeypatch.setenv("CANDYSTORE_STATEMENT_TIMEOUT_MS", "1500")
    monkeypatch.setattr(db.psycopg2, "connect", lambda **kw: calls.append(kw))
    db._connect()
    assert calls[0]["connect_timeout"] == 1
    assert "application_name=probe" in calls[0]["options"]
    assert calls[0]["options"].endswith("statement_timeout=900ms")


def test_cursor_rolls_back_and_closes_on_sql_error(monkeypatch):
    class Connection:
        rolled_back = False
        closed = False

        def cursor(self, **kwargs):
            return object()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    conn = Connection()
    monkeypatch.setattr(db, "_connect", lambda: conn)
    with pytest.raises(RuntimeError):
        with db.cursor():
            raise RuntimeError("sql failed")
    assert conn.rolled_back and conn.closed


def test_boot_success_recovery_and_exhaustion(monkeypatch):
    attempts = []
    sleeps = []
    monkeypatch.setattr(db.time, "sleep", sleeps.append)

    def flaky():
        attempts.append(1)
        if len(attempts) <= 2:
            raise psycopg2.OperationalError("connection refused")

    monkeypatch.setattr(db, "init_schema", flaky)
    db.init_schema_with_retry()
    assert len(attempts) == 3 and sleeps == [0.25, 0.5]
    attempts.clear()
    sleeps.clear()
    monkeypatch.setattr(db, "init_schema", lambda: attempts.append(1))
    db.init_schema_with_retry()
    assert len(attempts) == 1 and sleeps == []
    monkeypatch.setattr(
        db,
        "init_schema",
        lambda: (_ for _ in ()).throw(psycopg2.OperationalError("connection refused")),
    )
    with pytest.raises(psycopg2.OperationalError):
        db.init_schema_with_retry()
    assert sleeps == [0.25, 0.5, 1.0]


def test_boot_nontransient_does_not_retry_or_start_server(monkeypatch):
    sleeps = []
    monkeypatch.setattr(db.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        db,
        "init_schema",
        lambda: (_ for _ in ()).throw(psycopg2.errors.QueryCanceled("statement timeout")),
    )
    with pytest.raises(psycopg2.errors.QueryCanceled):
        db.init_schema_with_retry()
    monkeypatch.setattr(
        db,
        "init_schema",
        lambda: (_ for _ in ()).throw(psycopg2.errors.SyntaxError("bad migration")),
    )
    with pytest.raises(psycopg2.errors.SyntaxError):
        db.init_schema_with_retry()
    assert sleeps == []
    monkeypatch.setattr(
        main, "init_schema_with_deadline", lambda: (_ for _ in ()).throw(RuntimeError("db failed"))
    )
    monkeypatch.setattr(main, "ThreadingHTTPServer", lambda *args: pytest.fail("server started"))
    with pytest.raises(RuntimeError):
        main.run("127.0.0.1", 0)


def test_boot_starts_one_server_after_recovery(monkeypatch):
    calls = []

    class Server:
        def serve_forever(self):
            calls.append("serve")

        def server_close(self):
            calls.append("close")

    monkeypatch.setattr(main, "init_schema_with_deadline", lambda: calls.append("schema"))
    monkeypatch.setattr(
        main, "ThreadingHTTPServer", lambda *args: (calls.append("server"), Server())[1]
    )
    main.run("127.0.0.1", 0)
    assert calls == ["schema", "server", "serve", "close"]


@pytest.mark.parametrize(
    "error,transient",
    [
        (psycopg2.errors.CrashShutdown("crash shutdown"), True),
        (
            psycopg2.OperationalError(
                'connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: '
                "No such file or directory"
            ),
            True,
        ),
        (psycopg2.OperationalError("No such file or directory: migration.sql"), False),
        (psycopg2.errors.QueryCanceled("statement timeout"), False),
    ],
)
def test_boot_classification_and_no_sleep_on_nontransient(monkeypatch, error, transient):
    attempts = []
    sleeps = []
    monkeypatch.setattr(db.time, "sleep", sleeps.append)

    def fail():
        attempts.append(1)
        raise error

    monkeypatch.setattr(db, "init_schema", fail)
    with pytest.raises(type(error)):
        db.init_schema_with_retry()
    assert len(attempts) == (4 if transient else 1)
    assert sleeps == ([0.25, 0.5, 1.0] if transient else [])


def test_boot_deadline_terminates_worker_without_leaking(monkeypatch):
    import multiprocessing

    monkeypatch.setenv("CANDYSTORE_BOOT_DEADLINE_SECONDS", "1")
    monkeypatch.setattr(db, "init_schema_with_retry", lambda: time.sleep(30))
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="boot deadline"):
        db.init_schema_with_deadline()
    assert time.monotonic() - started < 5
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_boot_deadline_success_and_error_propagation(monkeypatch):
    monkeypatch.setenv("CANDYSTORE_BOOT_DEADLINE_SECONDS", "2")
    monkeypatch.setattr(db, "init_schema_with_retry", lambda: None)
    db.init_schema_with_deadline()
    monkeypatch.setattr(
        db, "init_schema_with_retry", lambda: (_ for _ in ()).throw(ValueError("bad config"))
    )
    with pytest.raises(ValueError, match="bad config"):
        db.init_schema_with_deadline()


def test_boot_deadline_invalid_and_unsupported_fail_before_spawn(monkeypatch):
    monkeypatch.setenv("CANDYSTORE_BOOT_DEADLINE_SECONDS", "0")
    with pytest.raises(ValueError, match="CANDYSTORE_BOOT_DEADLINE_SECONDS"):
        db.init_schema_with_deadline()
    monkeypatch.setenv("CANDYSTORE_BOOT_DEADLINE_SECONDS", "2")
    monkeypatch.setattr(db.os, "name", "nt")
    with pytest.raises(RuntimeError, match="POSIX fork"):
        db.init_schema_with_deadline()
