from __future__ import annotations

from candystore.summarize import UNRESOLVED_PROJECT, summarize


def test_session_end_summary(sample_event):
    env = sample_event()

    # The project is handed in, not derived: it depends on the pjangler
    # registry, which an envelope cannot see. Deriving it here as well is what
    # made /events and /events/<id>/summary disagree on 399 rows in 7 days.
    summary = summarize(env, "candystore")

    assert summary["title"] == "Session ended - candystore (claude)"
    assert summary["duration"] == "1m 35s"
    assert summary["tools_used"] == 2


def test_generic_summary(sample_event):
    env = sample_event(type="bloodbank.v1.unknown")

    summary = summarize(env, "candystore")

    assert summary["title"] == "bloodbank.v1.unknown"
    assert summary["project"] == "candystore"


def test_an_unresolved_project_says_so_rather_than_guessing(sample_event):
    """With no resolved slug the summary must not fall back to
    basename(working_directory) -- that fallback is what reported worktrees,
    `dist` and `james-brennan.git` as projects. `unassigned` is the honest
    answer: the repo is simply not in the registry yet (CANDYS-68) or the
    directory is an ephemeral clone (CANDYS-39)."""
    env = sample_event(data={"working_directory": "/home/delorenj/code/33GOD/candystore/dist"})

    # The FIELD stays null -- it is data, and it must match the row-level
    # PROJECT_EXPR byte for byte so /events and /events/<id>/summary agree.
    assert summarize(env)["project"] is None
    assert summarize(env, None)["project"] is None
    # The TITLE is prose, so it gets the label rather than the word "None".
    assert summarize(env)["title"] == f"Session ended - {UNRESOLVED_PROJECT} (claude)"


def test_version_free_session_end_summary(sample_event):
    """The version-free four-token type must reach the same summarizer."""
    env = sample_event(type="bloodbank.cli.session.ended")

    summary = summarize(env, "candystore")

    assert summary["title"] == "Session ended - candystore (claude)"
    assert summary["duration"] == "1m 35s"


def test_agent_session_end_supersedes_cli(sample_event):
    """agent.session.* is the canonical session lane and carries the same payload."""
    for event_type in (
        "bloodbank.agent.session.ended",
        "bloodbank.v1.agent.session.ended",
    ):
        summary = summarize(sample_event(type=event_type), "candystore")
        assert summary["title"] == "Session ended - candystore (claude)"


def test_tool_call_rename_maps_to_agent_tool(sample_event):
    """The retired tool.tool_call.* lane shares agent.tool.*'s payload and summary."""
    data = {
        "tool_name": "Bash",
        "outcome": "success",
        "arguments": {"command": "ls"},
    }
    for event_type in (
        "bloodbank.v1.tool.tool_call.invoked",
        "bloodbank.agent.tool.completed",
        "bloodbank.v1.agent.tool.completed",
    ):
        summary = summarize(sample_event(type=event_type, data=data))
        assert summary["tool"] == "Bash"
        assert summary["status"] == "success"
        assert summary["input_preview"] == '{"command": "ls"}'


def test_tool_invoked_success_bool(sample_event):
    summary = summarize(
        sample_event(
            type="bloodbank.v1.agent.tool.invoked",
            data={"tool_name": "Edit", "success": False},
        )
    )

    assert summary["status"] == "failure"


def test_turn_index_from_turn_id_suffix(sample_event):
    summary = summarize(
        sample_event(
            type="bloodbank.conversation.turn.started",
            data={
                "session_id": None,
                "thread_id": "abc",
                "turn_id": "abc:458",
                "prompt_text": "hi  there",
            },
        )
    )

    assert summary["title"] == "Turn started - 458"
    assert summary["turn_index"] == 458
    # thread_id is the new-shape spelling of the session identifier.
    assert summary["session_id"] == "abc"
    assert summary["prompt_preview"] == "hi there"


def test_heartbeat_reads_schema_field_names(sample_event):
    summary = summarize(
        sample_event(
            type="bloodbank.system.heartbeat.received",
            data={"source_id": "smoketest-cli", "sequence": 0},
        )
    )

    assert summary["title"] == "Heartbeat - smoketest-cli"
    assert summary["producer_id"] == "smoketest-cli"
    assert summary["tick_seq"] == 0


def test_agent_invocation_stop_reason_and_error_message(sample_event):
    completed = summarize(
        sample_event(
            type="bloodbank.agent.invocation.completed",
            data={"invocation_id": "i1", "stop_reason": "completed"},
        )
    )
    assert completed["status"] == "completed"
    assert completed["invocation_id"] == "i1"

    failed = summarize(
        sample_event(
            type="bloodbank.agent.invocation.failed",
            data={"invocation_id": "i2", "error_message": "boom"},
        )
    )
    assert failed["error"] == "boom"


def test_unknown_type_still_falls_back_to_generic(sample_event):
    # Deliberately a type no producer emits. This test used to name
    # `repo.decision.recorded`, which CANDYS-43 gave a real summarizer -- so it
    # started asserting that a covered type renders generic, which is the
    # opposite of what it is for.
    summary = summarize(sample_event(type="bloodbank.invented.thing.happened"))

    assert summary["title"] == "bloodbank.invented.thing.happened"
