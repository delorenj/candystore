from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import psycopg2
import pytest

import candystore.main as main
from candystore.db import _validate_time, cursor, sanitize_envelope
from candystore.main import Handler

# --- pure helpers (no DB) --------------------------------------------------

def test_sanitize_envelope_strips_nul():
    env = {"id": "x", "data": {"out": "a\x00b", "nested": ["c\x00", {"k\x00": "v\x00"}]}}
    clean, changed = sanitize_envelope(env)
    assert changed is True
    assert clean["data"]["out"] == "ab"
    assert clean["data"]["nested"][0] == "c"
    assert clean["data"]["nested"][1] == {"k": "v"}
    # no NUL survives anywhere
    assert "\x00" not in json.dumps(clean)


def test_sanitize_envelope_noop_when_clean():
    env = {"id": "x", "data": {"out": "clean"}}
    clean, changed = sanitize_envelope(env)
    assert changed is False
    assert clean == env


def test_validate_time_accepts_common_forms():
    good_times = (
        "2026-07-09T01:07:54.337000Z",
        "2026-05-24T16:00:00Z",
        "2026-05-24T16:00:00+00:00",
    )
    for good in good_times:
        _validate_time(good)  # must not raise


def test_validate_time_rejects_garbage():
    with pytest.raises(ValueError, match="invalid time"):
        _validate_time("NOT-A-TIMESTAMP")


# --- HTTP ingest path ------------------------------------------------------

def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(host, port, method, path, payload=None, raw=None):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    if raw is not None:
        body = raw
    else:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, (json.loads(data) if data else None)


def _count(table):
    with cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_nul_event_is_sanitized_and_persisted(db, sample_event):
    server, thread = _serve()
    host, port = server.server_address
    try:
        env = sample_event(id="11111111-1111-1111-1111-111111111111",
                           data={"command": "echo a\x00b", "note": "tail\x00"})
        # A literal NUL in the JSON body is exactly what poison-loops today.
        status, body = _request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body == {"status": "SUCCESS", "inserted": True}

        with cursor() as cur:
            cur.execute(
                "SELECT sanitized, raw::text, data::text FROM events WHERE id=%s", (env["id"],)
            )
            row = cur.fetchone()
        assert row is not None, "NUL event must be persisted, not dropped"
        assert row[0] is True                      # sanitized flag set
        assert "\x00" not in row[1] and "\x00" not in row[2]
        assert _count("dead_letter") == 0          # sanitize uses the flag, not dead_letter
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_bad_time_drops_and_dead_letters(db, sample_event):
    server, thread = _serve()
    host, port = server.server_address
    try:
        env = sample_event(id="22222222-2222-2222-2222-222222222222", time="NOT-A-TIMESTAMP")
        status, body = _request(host, port, "POST", "/events/all", env)
        assert status == 200                       # never 500 → no poison loop
        assert body["status"] == "DROP"
        assert _count("events") == 0
        assert _count("dead_letter") == 1
        with cursor() as cur:
            cur.execute("SELECT reason FROM dead_letter LIMIT 1")
            assert cur.fetchone()[0] == "malformed"
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_missing_field_drops_and_dead_letters(db, sample_event):
    server, thread = _serve()
    host, port = server.server_address
    try:
        env = sample_event()
        del env["producer"]
        status, body = _request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body["status"] == "DROP"
        assert _count("events") == 0
        assert _count("dead_letter") == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_db_data_error_is_dropped_not_retried(db, sample_event, monkeypatch):
    def boom(body, topic=None):
        raise psycopg2.DataError("unsupported value")

    monkeypatch.setattr(main, "handle_event", boom)
    server, thread = _serve()
    host, port = server.server_address
    try:
        status, body = _request(host, port, "POST", "/events/all", sample_event())
        assert status == 200                       # DROP, so Dapr stops redelivering
        assert body["status"] == "DROP"
        assert _count("dead_letter") == 1
        with cursor() as cur:
            cur.execute("SELECT reason FROM dead_letter LIMIT 1")
            assert cur.fetchone()[0] == "db-data-error"
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_transient_error_retries_and_does_not_dead_letter(db, sample_event, monkeypatch):
    def boom(body, topic=None):
        raise psycopg2.OperationalError("could not connect")

    monkeypatch.setattr(main, "handle_event", boom)
    server, thread = _serve()
    host, port = server.server_address
    try:
        status, body = _request(host, port, "POST", "/events/all", sample_event())
        assert status == 500                       # RETRY: transient, let NATS redeliver
        assert body["status"] == "RETRY"
        assert _count("dead_letter") == 0
    finally:
        server.shutdown()
        thread.join(timeout=3)
