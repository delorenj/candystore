from __future__ import annotations

import json
import logging
import os
from typing import Any

from candystore import stats
from candystore.db import insert_event, sanitize_envelope

logger = logging.getLogger("candystore.ingest")

SUBSCRIBE_PUBSUB = os.environ.get("SUBSCRIBE_PUBSUB", "bloodbank-pubsub")
SUBSCRIBE_TOPIC = os.environ.get("SUBSCRIBE_TOPIC", "bloodbank.evt.v1.>")
SUBSCRIBE_ROUTE = os.environ.get("SUBSCRIBE_ROUTE", "/events/all")

EXPLICIT_TOPICS = [
    ("bloodbank.evt.v1.cli.session.started", "/events/cli_session_started"),
    ("bloodbank.evt.v1.cli.session.ended", "/events/cli_session_ended"),
    ("bloodbank.evt.v1.conversation.turn.started", "/events/turn_started"),
    ("bloodbank.evt.v1.tool.tool_call.requested", "/events/tool_requested"),
    ("bloodbank.evt.v1.tool.tool_call.invoked", "/events/tool_invoked"),
    ("bloodbank.evt.v1.tool.tool_call.completed", "/events/tool_completed"),
    ("bloodbank.evt.v1.agent.invocation.completed", "/events/agent_completed"),
    ("bloodbank.evt.v1.agent.invocation.failed", "/events/agent_failed"),
    ("bloodbank.evt.v1.system.heartbeat.received", "/events/heartbeat"),
]


def subscribe_response() -> list[dict[str, str]]:
    """Return Dapr programmatic subscription declarations."""
    if os.environ.get("SUBSCRIBE_MODE", "wildcard").lower() == "explicit":
        return [
            {"pubsubname": SUBSCRIBE_PUBSUB, "topic": topic, "route": route}
            for topic, route in EXPLICIT_TOPICS
        ]

    return [
        {
            "pubsubname": SUBSCRIBE_PUBSUB,
            "topic": SUBSCRIBE_TOPIC,
            "route": SUBSCRIBE_ROUTE,
        }
    ]


def known_event_routes() -> set[str]:
    routes = {SUBSCRIBE_ROUTE}
    routes.update(route for _, route in EXPLICIT_TOPICS)
    return routes


def handle_event(body: bytes, topic: str | None = None) -> dict[str, Any]:
    envelope = json.loads(body.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a JSON object")

    clean, sanitized = sanitize_envelope(envelope)
    inserted = insert_event(clean, sanitized=sanitized)
    if sanitized:
        # Persisted with NUL stripped rather than poison-looping forever. Not
        # silent: mark the row (events.sanitized), count it, and log.
        stats.incr("sanitized")
        logger.warning(
            "stripped NUL before insert: event %s topic=%s", clean.get("id"), topic
        )
    stats.incr("inserted" if inserted else "duplicate")
    return {"status": "SUCCESS", "inserted": inserted}
