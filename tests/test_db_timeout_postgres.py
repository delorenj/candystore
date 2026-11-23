"""Disposable PostgreSQL proof: set CANDYS31_TEST_DATABASE_URL to an isolated DB."""

import os
import time

import psycopg2
import pytest

from candystore import context, db


@pytest.mark.skipif(not os.getenv("CANDYS31_TEST_DATABASE_URL"), reason="isolated PG URL required")
def test_disposable_postgres_statement_budget_and_context(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["CANDYS31_TEST_DATABASE_URL"])
    monkeypatch.setenv("CANDYSTORE_STATEMENT_TIMEOUT_MS", "300")
    start = time.monotonic()
    with pytest.raises(psycopg2.errors.QueryCanceled):
        with db.cursor() as cur:
            cur.execute("SELECT pg_sleep(2)")
    elapsed = time.monotonic() - start
    assert 0.2 <= elapsed < 1.5
    with db.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "300ms"
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)

    # The analytics/context path uses the same cursor factory. Its existing
    # SET LOCAL 2500ms remains the tighter transaction-specific limit here.
    monkeypatch.setenv("CANDYSTORE_STATEMENT_TIMEOUT_MS", "5000")
    with db.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '2500ms'")
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "2500ms"
    with db.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "5s"
    # Exercise the actual analytics entrypoint against the isolated database.
    # Its SET LOCAL is observable through a recording cursor while real SQL runs.
    observed = []
    real_cursor = context.cursor

    def recording_cursor():
        from contextlib import contextmanager

        @contextmanager
        def wrapped():
            with real_cursor() as cur:

                class Recording:
                    def execute(self, sql, *args):
                        result = cur.execute(sql, *args)
                        if sql.startswith("SET LOCAL statement_timeout"):
                            cur.execute("SHOW statement_timeout")
                            observed.append(cur.fetchone()[0])
                        return result

                    def __getattr__(self, key):
                        return getattr(cur, key)

                yield Recording()

        return wrapped()

    monkeypatch.setattr(context, "cursor", recording_cursor)
    monkeypatch.setattr(context, "_project", lambda cur, project, cwd: "timeout-proof")
    result = context.latest_context(project="timeout-proof", sessions=1)
    assert result["sessions"] == []
    assert observed == ["2500ms"]
    observed.clear()
    monkeypatch.setenv("CANDYSTORE_STATEMENT_TIMEOUT_MS", "300")
    result = context.latest_context(project="timeout-proof", sessions=1)
    assert result["sessions"] == []
    assert observed == ["300ms"]
