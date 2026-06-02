from __future__ import annotations

import json
import os
from typing import Any

from candystore.db import insert_event

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


def handle_event(body: bytes) -> dict[str, Any]:
    envelope = json.loads(body.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a JSON object")

    inserted = insert_event(envelope)
    return {"status": "SUCCESS", "inserted": inserted}
