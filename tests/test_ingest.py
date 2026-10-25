from __future__ import annotations

import json

from candystore import ingest


def test_subscribe_response_is_a_single_wildcard():
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

    The same trap is why there is no enumerated topic list here at all: any
    per-subject enumeration is a second copy of the stream config that goes
    stale in silence. The wildcard must cover the legacy versioned subjects AND
    the current version-free grammar.
    """
    assert "," not in ingest.SUBSCRIBE_TOPIC, "must be one subject, not a list"

    streamed = (
        "bloodbank.evt.v1.agent.tool.completed",
        "bloodbank.evt.v2.repo.maintenance.failed",
        "bloodbank.evt.agent.tool.completed",
        "bloodbank.evt.repo.maintenance.failed",
    )
    prefix = ingest.SUBSCRIBE_TOPIC.removesuffix(">")
    for subject in streamed:
        assert subject.startswith(prefix), f"{subject} not covered by {ingest.SUBSCRIBE_TOPIC}"


def test_no_enumerated_topic_list_survives():
    """A duplicate encoding of the stream config is what goes stale silently."""
    assert not hasattr(ingest, "EXPLICIT_TOPICS")
    assert ingest.known_event_routes() == {ingest.SUBSCRIBE_ROUTE}


def test_handle_event_reports_idempotent_success(monkeypatch, sample_event):
    calls = []

    def fake_insert(envelope, sanitized=False):
        calls.append(envelope)
        return False

    monkeypatch.setattr(ingest, "insert_event", fake_insert)

    result = ingest.handle_event(json.dumps(sample_event()).encode("utf-8"))

    assert result == {"status": "SUCCESS", "inserted": False}
    assert len(calls) == 1


def test_only_a_new_row_is_placed_and_placement_cannot_fail_ingest(monkeypatch, sample_event):
    placed = []
    monkeypatch.setattr(ingest, "insert_event", lambda envelope, sanitized=False: True)
    monkeypatch.setattr(ingest, "place_work_dir", placed.append)
    ingest.handle_event(json.dumps(sample_event()).encode("utf-8"))
    assert [d["working_directory"] for d in placed] == ["/home/delorenj/code/33GOD/candystore"]

    monkeypatch.setattr(ingest, "insert_event", lambda envelope, sanitized=False: False)
    ingest.handle_event(json.dumps(sample_event()).encode("utf-8"))
    assert len(placed) == 1

    def boom(data):
        raise RuntimeError("map unavailable")

    # The event is committed before placement runs; a 500 here would make
    # Dapr redeliver an event that is already stored.
    monkeypatch.setattr(ingest, "insert_event", lambda envelope, sanitized=False: True)
    monkeypatch.setattr(ingest, "place_work_dir", boom)
    result = ingest.handle_event(json.dumps(sample_event()).encode("utf-8"))
    assert result == {"status": "SUCCESS", "inserted": True}
