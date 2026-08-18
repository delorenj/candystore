from __future__ import annotations

import json

from candystore import ingest


def test_subscribe_response_defaults_to_wildcard(monkeypatch):
    monkeypatch.delenv("SUBSCRIBE_MODE", raising=False)

    assert ingest.subscribe_response() == [
        {
            "pubsubname": "bloodbank-pubsub",
            "topic": "bloodbank.evt.>",
            "route": "/events/all",
        }
    ]


def test_wildcard_subscription_covers_every_streamed_event_subject():
    """One filter subject must cover every subject BLOODBANK_EVENTS binds.

    A Dapr pubsub.jetstream component creates a single consumer and a JetStream
    consumer is tied to one filter subject, so this cannot be a list -- a second
    topic fails at startup with "nats: subject does not match consumer" and is
    left silently unsubscribed. `bloodbank.evt.v1.>` alone did exactly that to
    `bloodbank.evt.v2.repo.maintenance.failed` (pr-crusher's action-phase
    failure), which reached the stream and was never projected.
    """
    assert "," not in ingest.SUBSCRIBE_TOPIC, "must be one subject, not a list"

    streamed = ("bloodbank.evt.v1.agent.tool.completed", "bloodbank.evt.v2.repo.maintenance.failed")
    prefix = ingest.SUBSCRIBE_TOPIC.removesuffix(">")
    for subject in streamed:
        assert subject.startswith(prefix), f"{subject} not covered by {ingest.SUBSCRIBE_TOPIC}"


def test_subscribe_response_can_enumerate_explicit_topics(monkeypatch):
    monkeypatch.setenv("SUBSCRIBE_MODE", "explicit")

    routes = ingest.subscribe_response()

    assert routes
    assert all(route["pubsubname"] == "bloodbank-pubsub" for route in routes)
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
