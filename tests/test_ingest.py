from __future__ import annotations

import json

from candystore import ingest


def test_subscribe_response_defaults_to_wildcard(monkeypatch):
    monkeypatch.delenv("SUBSCRIBE_MODE", raising=False)

    assert ingest.subscribe_response() == [
        {
            "pubsubname": "bloodbank-pubsub",
            "topic": "bloodbank.evt.v1.>",
            "route": "/events/all",
        },
        {
            "pubsubname": "bloodbank-command-results",
            "topic": "bloodbank.rpy.v1.lifecycle.intent.submit",
            "route": "/events/lifecycle_intent_reply",
        },
    ]


def test_subscribe_response_can_enumerate_explicit_topics(monkeypatch):
    monkeypatch.setenv("SUBSCRIBE_MODE", "explicit")

    routes = ingest.subscribe_response()

    assert routes
    assert any(route["pubsubname"] == "bloodbank-pubsub" for route in routes)
    assert {route["route"] for route in routes} <= ingest.known_event_routes()


def test_handle_event_reports_idempotent_success(monkeypatch, sample_event):
    calls = []

    def fake_insert(envelope, sanitized=False):
        calls.append(envelope)
        return False

    monkeypatch.setattr(ingest, "insert_event", fake_insert)

    result = ingest.handle_event(json.dumps(sample_event()).encode("utf-8"))

    assert result == {"status": "SUCCESS", "inserted": False}
    assert len(calls) == 1
