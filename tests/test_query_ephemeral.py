"""The /events preview surfaces the `ephemeral` extension when raw carries it.

The preview is built from columns, so envelope extension fields that have no
column (ephemeral — worktree session breadcrumbs) used to be invisible to API
consumers even though ingest stores the full envelope in raw. An agent that
queries the feed to decide what to do with a dying worktree session needs the
marker in the response it actually reads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from candystore.query import _preview_from_row

EPHEMERAL = {
    "worktree": {
        "path": "/home/operator/code/33GOD/.worktrees/feat-auth",
        "branch": "feat-auth",
        "repo": "33GOD",
        "main_checkout": "/home/operator/code/33GOD",
    },
    "session": {"harness": "claude", "harness_session_id": "abc", "turn_number": 4},
}


def _row(raw: dict) -> tuple:
    return (
        uuid.uuid4(),
        "bloodbank.agent.session.started",
        datetime(2026, 9, 6, tzinfo=UTC),
        "claude-code",
        "claude-code",
        "agent",
        {"type": "agent_cli", "agent_id": "bloodbank.agent.claude", "cli": "claude"},
        {"session_id": "s"},
        uuid.uuid4(),
        raw,
        None,
        "agent",
    )


def test_preview_includes_ephemeral_when_raw_carries_it():
    raw = {"type": "bloodbank.agent.session.started", "ephemeral": EPHEMERAL}
    event = _preview_from_row(_row(raw))
    assert event["ephemeral"] == EPHEMERAL


def test_preview_omits_ephemeral_for_durable_contexts():
    event = _preview_from_row(_row({"type": "bloodbank.agent.session.started"}))
    assert "ephemeral" not in event
