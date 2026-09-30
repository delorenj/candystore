from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from candystore import cli
from candystore.context import ContextError, latest_context, since_time
from candystore.db import insert_event
from candystore.handoff import render_context, summarize_session, text
from candystore.main import Handler

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def event(kind, data, n=1):
    return {
        "id": f"event-{n}",
        "time": f"2026-09-29T12:{n:02}:00+00:00",
        "type": f"bloodbank.{kind}",
        "data": data,
    }


def test_handoff_preserves_evidence_and_does_not_invent_completion():
    summary = summarize_session(
        [
            event("conversation.turn.started", {"prompt_text": "Fix the retry loop"}),
            event(
                "agent.tool.completed",
                {
                    "tool_name": "Edit",
                    "outcome": "failed",
                    "arguments": {"file_path": "broken.py"},
                    "error": "permission denied",
                },
                2,
            ),
            event(
                "agent.tool.completed",
                {
                    "tool_name": "apply_patch",
                    "outcome": "success",
                    "arguments": {"patch": "*** Update File: retry.py\n+fixed"},
                },
                3,
            ),
            event(
                "conversation.turn.completed",
                {
                    "payload": {
                        "last_assistant_message": (
                            "Implemented bounded retries; deployment remains pending."
                        )
                    }
                },
                4,
            ),
            event("agent.session.ended", {"next_steps": ["Deploy and verify the live route"]}, 5),
        ],
        {"session_id": "session-a", "cli": "codex", "last_activity": NOW.isoformat()},
    )

    assert summary["requests"][0]["text"] == "Fix the retry loop"
    assert summary["requests"][0]["event_id"] == "event-1"
    assert summary["changes"][0]["text"] == "Edit reported successful: retry.py"
    assert all("broken.py" not in item["text"] for item in summary["changes"])
    assert "permission denied" in summary["errors"][0]["text"]
    assert "pending" in summary["outcomes"][0]["text"]
    assert "pending" in summary["unfinished"][0]["text"]
    assert summary["next_steps"][0]["event_id"] == "event-5"
    assert summary["status"] == "ended"
    brief = render_context({"project": "candystore", "sessions": [summary]})
    assert "resolution unverified" in brief
    assert "Recorded next step: Deploy" in brief


def test_session_end_alone_does_not_claim_task_finished():
    summary = summarize_session([event("agent.session.ended", {"final_status": "success"})], {})
    assert summary["outcomes"] == []
    assert summary["coverage"]["has_outcome"] is False
    assert summary["status"] == "ended"


def test_failed_exit_code_overrides_successful_tool_envelope():
    summary = summarize_session(
        [
            event(
                "agent.tool.completed",
                {
                    "tool_name": "Write",
                    "outcome": "success",
                    "result": {"exit_code": 1},
                    "arguments": {"file_path": "not-written.py"},
                },
            )
        ],
        {},
    )
    assert summary["changes"] == []
    assert summary["errors"]


def test_historical_dialects_and_bounded_text():
    row = event("conversation.turn.started", {"prompt_text": "the request " * 1000})
    row["type"] = "bloodbank.v1.conversation.turn.started"
    summary = summarize_session([row], {})
    assert len(summary["requests"][0]["text"]) <= 420
    assert len(summary["requests"]) == 1


def test_pending_work_at_end_of_a_long_response_keeps_its_evidence():
    message = "Completed implementation. " * 100 + "\nRemaining work: deploy the app."
    summary = summarize_session(
        [
            event(
                "conversation.turn.completed",
                {
                    "payload": {"last_assistant_message": message},
                },
            )
        ],
        {},
    )
    assert "Remaining work: deploy" in summary["unfinished"][0]["text"]
    assert summary["unfinished"][0]["event_id"] == "event-1"


def test_text_budget_preserves_each_selected_session_and_json_evidence():
    sessions = []
    for index in range(3):
        rows = [
            event(
                "conversation.turn.completed",
                {
                    "summary": (f"Fact {number} " * 100),
                    "remaining_work": (f"Pending {number} " * 100),
                },
                number + 1,
            )
            for number in range(12)
        ]
        sessions.append(
            summarize_session(
                rows,
                {
                    "session_id": f"session-{index}",
                    "cli": "codex",
                    "last_activity": NOW.isoformat(),
                },
            )
        )
    brief = render_context({"project": "candystore", "sessions": sessions})
    assert len(brief) <= 8000
    assert all(f"session-{index}" in brief for index in range(3))
    assert "--json" in brief
    assert len(sessions[0]["unfinished"]) == 3


def test_briefing_scrubs_credentials_but_preserves_vault_references():
    specimen = "sk-" + "samplecredential" * 3
    result = text(
        f"Use API_KEY={specimen} with token=sample-value and "
        "op://DeLoSecrets/service/credential; postgresql://user:sample-pass@db"
    )
    assert specimen not in result
    assert "sample-value" not in result
    assert "sample-pass" not in result
    assert "op://DeLoSecrets/service/credential" in result
    assert "<environment_context>" not in text(
        "<environment_context>irrelevant harness data</environment_context>Actual request"
    )


@pytest.mark.parametrize("value", ["tomorrowish", "0d", "-1x", "999999999999999999999w"])
def test_invalid_time_floor_is_a_client_error(value):
    with pytest.raises(ContextError):
        since_time(value, NOW)


def test_relative_and_absolute_floors_are_timezone_aware():
    assert since_time("24h", NOW) == "2026-09-29T12:00:00+00:00"
    assert since_time("-24h", NOW) == since_time("24h", NOW)
    assert since_time("2026-09-29", NOW) == "2026-09-29T00:00:00+00:00"


def seed(sample_event, native, minute, *, correlation=None, agent="claude", **data):
    envelope = sample_event(
        correlationid=correlation or native,
        time=f"2026-09-29T12:{minute:02}:00Z",
        type="bloodbank.conversation.turn.started",
        actor={"cli": agent},
        data={"session_id": native, "prompt_text": f"Request from {agent}", **data},
    )
    insert_event(envelope)
    return envelope


def test_latest_groups_across_clis_excludes_current_and_subagents(db, project_map, sample_event):
    current = "00000000-0000-4000-8000-000000000004"
    first = seed(sample_event, "00000000-0000-4000-8000-000000000001", 10)
    second = seed(sample_event, "kimi-session-non-uuid", 20, agent="kimi")
    seed(sample_event, current, 40, agent="codex")
    seed(sample_event, "subagent-session", 50, payload={"agent_id": "child-agent"})
    seed(
        sample_event,
        "different-project",
        55,
        working_directory="/home/delorenj/code/vinyl",
        project="vinyl",
    )

    context = latest_context(project="candystore", exclude_session=current, now=NOW)
    assert [s["cli"] for s in context["sessions"]] == ["kimi", "claude"]
    assert context["sessions"][0]["session_id"] == "kimi-session-non-uuid"
    assert context["sessions"][0]["requests"][0]["event_id"] == second["id"]
    assert context["sessions"][1]["session_id"] == first["correlationid"]


def test_native_alias_exclusion_and_identity_are_not_limited_to_end_events(
    db,
    project_map,
    sample_event,
):
    native = "native-current"
    mapped = "00000000-0000-4000-8000-000000000003"
    seed(sample_event, native, 25, correlation=mapped, agent="hermes")
    seed(sample_event, "00000000-0000-4000-8000-000000000002", 15, agent="gemini")
    result = latest_context(project="candystore", exclude_session=native, now=NOW)
    assert len(result["sessions"]) == 1
    assert result["sessions"][0]["cli"] == "gemini"
    assert result["sessions"][0]["status"] == "no_recorded_end"


def test_same_cli_keeps_both_uuid_and_historical_native_sessions(db, project_map, sample_event):
    seed(sample_event, "old-native-session", 10)
    seed(sample_event, "00000000-0000-4000-8000-000000000002", 20)
    context = latest_context(project="candystore", now=NOW)
    assert [s["session_id"] for s in context["sessions"]] == [
        "00000000-0000-4000-8000-000000000002",
        "old-native-session",
    ]


def test_excluding_native_alias_also_removes_thin_correlated_tool_rows(
    db,
    project_map,
    sample_event,
):
    mapped = "00000000-0000-4000-8000-000000000003"
    insert_event(
        sample_event(
            correlationid=mapped,
            type="bloodbank.agent.session.started",
            time="2026-09-29T12:10:00Z",
            data={"session_id": "native-current"},
        )
    )
    insert_event(
        sample_event(
            correlationid=mapped,
            type="bloodbank.agent.tool.completed",
            time="2026-09-29T12:30:00Z",
            data={"session_id": None, "tool_name": "Read"},
        )
    )
    seed(sample_event, "prior-session", 20)
    context = latest_context(project="candystore", exclude_session="native-current", now=NOW)
    assert [s["session_id"] for s in context["sessions"]] == ["prior-session"]


def test_project_alias_and_subdirectory_resolve_to_the_registry(db, project_map, sample_event):
    seed(
        sample_event,
        "bb-session",
        10,
        working_directory="/home/delorenj/code/33GOD/bloodbank",
        project="bb",
    )
    context = latest_context(cwd="/home/delorenj/code/33GOD/bloodbank/services", now=NOW)
    assert context["project"] == "bb"
    assert len(context["sessions"]) == 1
    assert latest_context(project="bloodbank", now=NOW)["project"] == "bb"
    with pytest.raises(ContextError, match="unknown"):
        latest_context(project="not-a-project", now=NOW)


def test_time_window_and_separate_project_decisions(db, project_map, sample_event):
    seed(sample_event, "old-session", 10)
    decision = sample_event(
        type="bloodbank.repo.decision.recorded",
        actor={"cli": None},
        time="2026-09-29T12:25:00Z",
        data={"decision": "Keep context summaries deterministic"},
    )
    insert_event(decision)
    result = latest_context(project="candystore", since="24h", now=NOW)
    assert result["decisions"][0]["event_id"] == decision["id"]
    assert latest_context(project="candystore", since="1h", now=NOW)["sessions"] == []


def test_answer_without_cwd_inherits_session_but_other_project_is_excluded(
    db,
    project_map,
    sample_event,
):
    session = "00000000-0000-4000-8000-000000000003"
    seed(sample_event, session, 10)
    for minute, cwd, project, message in (
        (20, None, None, "Implemented retry handling"),
        (25, "/home/delorenj/code/vinyl", "vinyl", "Other project's work"),
    ):
        insert_event(
            sample_event(
                correlationid=session,
                type="bloodbank.conversation.turn.completed",
                time=f"2026-09-29T12:{minute}:00Z",
                data={
                    "working_directory": cwd,
                    "project": project,
                    "last_assistant_message": message,
                },
            )
        )
    context = latest_context(project="candystore", now=NOW)
    assert [item["text"] for item in context["sessions"][0]["outcomes"]] == [
        "Implemented retry handling",
    ]


def test_cli_calls_http_api_and_uses_native_current_session(
    db, project_map, sample_event, monkeypatch, capsys
):
    current = "00000000-0000-4000-8000-000000000004"
    seed(sample_event, "prior-session", 10, agent="kimi")
    seed(sample_event, current, 20, agent="codex")
    monkeypatch.setenv("CODEX_THREAD_ID", current)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert (
            cli.main(
                [
                    "context",
                    "latest",
                    "--project",
                    "candystore",
                    "--since",
                    "2026-09-01",
                    "--base-url",
                    base,
                    "--json",
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert [s["session_id"] for s in result["sessions"]] == ["prior-session"]
        assert (
            cli.main(
                [
                    "context",
                    "latest",
                    "--project",
                    "candystore",
                    "--since",
                    "invalid",
                    "--base-url",
                    base,
                ]
            )
            == 1
        )
        assert "ISO" in capsys.readouterr().err
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_cli_network_failure_is_an_error_without_context(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(cli, "urlopen", unavailable)
    assert cli.main(["context", "latest", "--project", "candystore"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "unavailable" in output.err


def test_cli_serializes_filters_without_shell_interpolation(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, _):
            return b'{"schema_version":1,"sessions":[]}'

    def open_request(request, **kwargs):
        requests.append(request.full_url)
        return Response()

    monkeypatch.setattr(cli, "urlopen", open_request)
    cli.fetch_context(
        cli.DEFAULT_URL,
        project="project & special",
        cwd="/ignored",
        since="2026-09-29T10:00:00+02:00",
        sessions=3,
        exclude_session="native/identifier",
        timeout=2,
    )
    query = parse_qs(urlsplit(requests[0]).query)
    assert query["project"] == ["project & special"]
    assert query["since"] == ["2026-09-29T10:00:00+02:00"]
    assert query["exclude_session"] == ["native/identifier"]


def test_worktree_uses_main_checkout_and_submodule_stays_in_its_repo(monkeypatch, tmp_path):
    for common, expected in (
        ("/code/repo/.git", "/code/repo"),
        ("/code/parent/.git/modules/child", "/code/parent/child"),
    ):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, common=common, **k: SimpleNamespace(
                returncode=0, stdout=f"/code/parent/child\n{common}\n"
            ),
        )
        assert cli.project_directory(str(tmp_path)) == expected


def test_console_server_compatibility(monkeypatch):
    monkeypatch.setattr("candystore.main.main", lambda: 17)
    assert cli.main([]) == 17
    assert cli.main(["serve"]) == 17
