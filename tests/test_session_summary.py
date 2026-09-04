from __future__ import annotations

from candystore.query import session_summary

# These tests call the pure roll-up directly, NOT get_session_summary(), so they
# run with no Postgres. tests/test_query.py skips wholesale without
# CANDYSTORE_TEST_DATABASE_URL, which is exactly why the session summary
# reported turns/tools_requested/tools_invoked = 0 for every post-migration
# session for as long as it did without a single red test.


def _event(event_type: str, *, time: str = "2026-08-28T16:00:00Z", **overrides):
    event = {
        "id": "00000000-0000-0000-0000-000000000000",
        "type": event_type,
        "time": time,
        "actor": {"cli": "claude"},
        "data": {},
    }
    event.update(overrides)
    return event


def test_counts_version_free_types():
    """The four-token shape must count. Every one of these types was worth 0
    before: the lookups were hardcoded to the retired `bloodbank.v1.` spelling."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:01:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-08-28T16:02:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-08-28T16:03:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-08-28T16:04:00Z"),
        _event("bloodbank.agent.tool.invoked", time="2026-08-28T16:05:00Z"),
    ]

    summary = session_summary("s-1", events)

    assert summary["turns"] == 2
    assert summary["tools_requested"] == 3
    assert summary["tools_invoked"] == 1
    assert summary["events_count"] == 6


def test_counts_v1_types_identically():
    """The ~718k historical rows keep counting; normalizing is not a cutover."""
    events = [
        _event("bloodbank.v1.conversation.turn.started", time="2026-05-01T10:00:00Z"),
        _event("bloodbank.v1.agent.tool.requested", time="2026-05-01T10:01:00Z"),
        _event("bloodbank.v1.agent.tool.invoked", time="2026-05-01T10:02:00Z"),
    ]

    summary = session_summary("s-2", events)

    assert (summary["turns"], summary["tools_requested"], summary["tools_invoked"]) == (1, 1, 1)


def test_tool_call_entity_rename_folds_into_agent_tool():
    """`tool.tool_call.*` -> `agent.tool.*` is an ENTITY rename that survives
    version-stripping, so both spellings land in the same bucket. 29,341
    v1.tool.tool_call.requested rows are live."""
    events = [
        _event("bloodbank.v1.tool.tool_call.requested", time="2026-05-01T10:00:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-05-01T10:01:00Z"),
        _event("bloodbank.v1.tool.tool_call.invoked", time="2026-05-01T10:02:00Z"),
    ]

    summary = session_summary("s-3", events)

    assert summary["tools_requested"] == 2
    assert summary["tools_invoked"] == 1


def test_events_by_type_keeps_the_raw_spelling():
    """The canonical fold is for COUNTS. `events_by_type` stays a literal report
    of the table, or a producer still emitting a retired shape goes invisible."""
    events = [
        _event("bloodbank.v1.tool.tool_call.requested", time="2026-05-01T10:00:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-05-01T10:01:00Z"),
    ]

    assert session_summary("s-4", events)["events_by_type"] == {
        "bloodbank.v1.tool.tool_call.requested": 1,
        "bloodbank.agent.tool.requested": 1,
    }


def test_agent_session_ended_payload_is_authoritative():
    """`agent.session.ended` supersedes `cli.session.ended` for agent CLIs. The
    old probe was `.endswith("cli.session.ended")`, so it never matched this and
    the data.total_turns / data.duration_seconds fallback never fired."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event(
            "bloodbank.agent.session.ended",
            time="2026-08-28T16:10:00Z",
            actor={"cli": "codex"},
            data={
                "total_turns": 107,
                "duration_seconds": 652,
                "working_directory": "/home/delorenj/code/vinyl",
            },
            project="vinyl",
        ),
    ]

    summary = session_summary("s-5", events)

    assert summary["turns"] == 107  # payload wins over the 1 counted turn event
    assert summary["duration_seconds"] == 652  # not the 600s wall-clock span
    assert summary["cli"] == "codex"
    # Read off the row, which arrives already resolved from get_session_events.
    # The roll-up must not re-derive it: basename(working_directory) is the
    # derivation that reported worktrees and `dist` as projects, and a second
    # copy of it here is how the list and the detail pane came to disagree.
    assert summary["project"] == "vinyl"


def test_cli_session_ended_still_recognized():
    """The retired spelling has 592 live rows; it must not stop being a session end."""
    events = [
        _event(
            "bloodbank.v1.cli.session.ended",
            data={"total_turns": 9},
            project="candystore",
        ),
    ]

    summary = session_summary("s-6", events)

    assert summary["turns"] == 9
    assert summary["project"] == "candystore"


def test_session_project_is_the_first_resolved_row_not_a_re_derivation():
    """A session can start outside a registered project and move into one --
    472 of 2,204 measured sessions span more than one directory -- so the
    roll-up scans for the first resolved slug rather than reading events[0].

    And a session that resolves to nothing reports None. The old code answered
    `basename(working_directory)` here, which is how `dist` became a project."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:01:00Z", project="bb"),
    ]
    assert session_summary("s-7", events)["project"] == "bb"

    unplaced = [
        _event(
            "bloodbank.agent.session.ended",
            data={"working_directory": "/tmp/hermes-board-cranker-50", "project": "wax"},
        ),
    ]
    # Neither the directory basename nor the unregistered `data.project` value
    # may become an answer.
    assert session_summary("s-8", unplaced)["project"] is None


def test_audio_session_ended_is_not_a_cli_session_end():
    """A different domain. Its payload must not be mistaken for the agent's."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event(
            "bloodbank.v1.audio.session.ended",
            time="2026-08-28T16:05:00Z",
            data={"total_turns": 9999},
        ),
    ]

    assert session_summary("s-7", events)["turns"] == 1


def test_in_flight_session_falls_back_to_counting():
    """No end event at all -- counts, not zeros."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event("bloodbank.agent.tool.requested", time="2026-08-28T16:01:00Z"),
    ]

    summary = session_summary("s-8", events)

    assert summary["turns"] == 1
    assert summary["tools_requested"] == 1
    assert summary["duration_seconds"] == 60


def test_null_total_turns_falls_back():
    """A present-but-null field is not an answer; dict-default lookups took it as one."""
    events = [
        _event("bloodbank.conversation.turn.started", time="2026-08-28T16:00:00Z"),
        _event(
            "bloodbank.agent.session.ended",
            time="2026-08-28T16:02:00Z",
            data={"total_turns": None, "duration_seconds": None},
        ),
    ]

    summary = session_summary("s-9", events)

    assert summary["turns"] == 1
    assert summary["duration_seconds"] == 120


def test_empty_session_does_not_explode():
    summary = session_summary("s-10", [])

    assert summary["events_count"] == 0
    assert summary["turns"] == 0
    assert summary["tools_requested"] == 0
    assert summary["cli"] is None
