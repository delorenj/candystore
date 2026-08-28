from __future__ import annotations

from candystore.summarize import summarize


def test_session_end_summary(sample_event):
    env = sample_event()

    summary = summarize(env)

    assert summary["title"] == "Session ended - candystore (claude)"
    assert summary["duration"] == "1m 35s"
    assert summary["tools_used"] == 2


def test_generic_summary(sample_event):
    env = sample_event(type="bloodbank.v1.unknown")

    summary = summarize(env)

    assert summary["title"] == "bloodbank.v1.unknown"
    assert summary["project"] == "candystore"


def test_version_free_session_end_summary(sample_event):
    """The version-free four-token type must reach the same summarizer."""
    env = sample_event(type="bloodbank.cli.session.ended")

    summary = summarize(env)

    assert summary["title"] == "Session ended - candystore (claude)"
    assert summary["duration"] == "1m 35s"


def test_agent_session_end_supersedes_cli(sample_event):
    """agent.session.* is the canonical session lane and carries the same payload."""
    for event_type in (
        "bloodbank.agent.session.ended",
        "bloodbank.v1.agent.session.ended",
    ):
        summary = summarize(sample_event(type=event_type))
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
    summary = summarize(sample_event(type="bloodbank.repo.decision.recorded"))

    assert summary["title"] == "bloodbank.repo.decision.recorded"
