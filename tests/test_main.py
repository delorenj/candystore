from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from candystore.bloodbank_contracts import SchemaRegistryError
from candystore.main import Handler


def test_http_ingest_and_query(db, sample_event):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        status, body = request(host, port, "GET", "/dapr/subscribe")
        assert status == 200
        assert body[0]["topic"] == "bloodbank.evt.v1.>"

        env = sample_event(id="550e8400-e29b-41d4-a716-446655440333")
        status, body = request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body == {"status": "SUCCESS", "inserted": True}

        status, body = request(host, port, "POST", "/events/all", env)
        assert status == 200
        assert body == {"status": "SUCCESS", "inserted": False}

        status, body = request(host, port, "GET", "/events?cli=claude")
        assert status == 200
        assert body["total"] == 1

        status, body = request(host, port, "GET", f"/events/{env['id']}/summary")
        assert status == 200
        assert body["summary"]["title"].startswith("Session ended")

        status, body = request(host, port, "GET", f"/sessions/{env['correlationid']}")
        assert status == 200
        assert len(body["events"]) == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_readyz_fails_when_projection_schema_registry_is_unavailable(monkeypatch):
    monkeypatch.setattr("candystore.main.check_connection", lambda: True)

    def unavailable():
        raise SchemaRegistryError("canonical schema registry unavailable")

    monkeypatch.setattr("candystore.main.check_projection_registry", unavailable)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        status, body = request(host, port, "GET", "/readyz")
        assert status == 503
        assert body == {"ready": False, "error": "canonical schema registry unavailable"}
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
