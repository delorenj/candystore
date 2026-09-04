from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from candystore.main import Handler


def test_http_ingest_and_query(db, sample_event):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        status, body = request(host, port, "GET", "/dapr/subscribe")
        assert status == 200
        assert body[0]["topic"] == "bloodbank.evt.>"

        env = sample_event(id="550e8400-e29b-41d4-a716-446655440333")
        status, body = request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body == {"status": "SUCCESS", "inserted": True}

        status, body = request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body == {"status": "SUCCESS", "inserted": False}

        # The endpoint bounds an unbounded browse to the last 24 h, and the
        # fixture's event is dated 2026-05-24 -- so the default view does not
        # show it, and the applied window is echoed back rather than left for
        # the caller to infer.
        status, body = request(host, port, "GET", "/events?cli=claude")
        assert status == 200
        assert body["events"] == []
        assert body["window"]["from"] is not None
        # The count is opt-in: rendering one costs a scan, so a caller that
        # does not ask gets null rather than a number it did not pay for.
        assert body["total"] is None
        assert body["total_capped"] is False

        status, body = request(
            host, port, "GET", "/events?cli=claude&from=2026-01-01T00:00:00Z&total=1"
        )
        assert status == 200
        assert body["total"] == 1
        assert body["total_capped"] is False
        assert body["window"] == {"from": "2026-01-01T00:00:00Z", "to": None}

        # A pasted event id must reach the whole trail. It resolves through the
        # primary key, so windowing it would only break the lookup.
        status, body = request(host, port, "GET", f"/events?q={env['id']}&total=1")
        assert status == 200
        assert body["total"] == 1
        assert body["window"] == {"from": None, "to": None}

        status, body = request(host, port, "GET", f"/events/{env['id']}/summary")
        assert status == 200
        assert body["summary"]["title"].startswith("Session ended")

        status, body = request(host, port, "GET", f"/sessions/{env['correlationid']}")
        assert status == 200
        assert len(body["events"]) == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_http_search(db, sample_event):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        env = sample_event(id="550e8400-e29b-41d4-a716-4466554cc111")
        assert request(host, port, "POST", "/events/all", env)[0] == 200

        # Free-text search is windowed like any other browse (a trigram scan
        # over the whole table is exactly what the window prevents), so these
        # name a floor that covers the fixture's 2026-05-24 event.
        early = "&from=2026-01-01T00:00:00Z&total=1"

        status, body = request(host, port, "GET", f"/events?q=candystore{early}")
        assert status == 200
        assert body["total"] == 1

        # A term the trigram index cannot serve is a client error, not a 500 and
        # not a minutes-long scan.
        status, body = request(host, port, "GET", "/events?q=ab")
        assert status == 400
        assert "at least 3 characters" in body["error"]

        status, body = request(host, port, "GET", f"/events?q=candystore&cli=copilot{early}")
        assert status == 200
        assert body["total"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=3)


def request(host: str, port: int, method: str, path: str, payload: dict | None = None):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    if not raw:
        return response.status, None
    return response.status, json.loads(raw.decode("utf-8"))
