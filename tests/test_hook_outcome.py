"""Startup-only outcome classification; no database required."""
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from candystore import cli


def response(body=None, code=200):
    data = json.dumps(body).encode() if not isinstance(body, bytes) else body
    if code == 200:
        class Result:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return data
        return Result()
    raise HTTPError("http://localhost/context/latest", code, "error", Message(), io.BytesIO(data))


@pytest.mark.parametrize("cwd", ["/tmp", str(Path.home())])
def test_no_project_is_hook_skip_only(monkeypatch, capsys, cwd):
    monkeypatch.setattr(cli, "urlopen", lambda *a, **kw: response({
        "error": "working directory is not a registered project; specify --project"
    }, 400))
    args = ["context", "latest", "--cwd", cwd]
    assert cli.main([*args, "--hook-outcome"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "skipped", "reason": "no_project"}
    assert cli.main(args) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "not a registered project" in out.err


@pytest.mark.parametrize("sessions,decisions,expected", [
    ([], [], "skipped"),
    ([{"session_id": "prior", "last_activity": "2026-11-17", "status": "ended",
       "coverage": {"has_request": False, "has_outcome": False, "sample_truncated": False},
       "next_steps": []}], [], "succeeded"),
    ([], [{"text": "decision", "event_id": "event-1"}], "succeeded"),
])
def test_history_after_filters(monkeypatch, capsys, sessions, decisions, expected):
    monkeypatch.setattr(cli, "urlopen", lambda *a, **kw: response({
        "schema_version": 1, "project": "candystore", "sessions": sessions,
        "decisions": decisions,
    }))
    assert cli.main(["context", "latest", "--hook-outcome", "--exclude-session", "current"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected
    if expected == "skipped":
        assert result == {"status": "skipped", "reason": "no_history"}
    else:
        assert result["context"].strip()


@pytest.mark.parametrize("body,code", [
    ({"error": "since must not be in the future"}, 400),
    ({"error": "working directory is not a registered project; specify --project"}, 503),
    ({"error": "unavailable"}, 503),
    (b"broken json", 200),
    ({"schema_version": 2, "sessions": [], "decisions": []}, 200),
    ({"schema_version": 1, "sessions": [], "decisions": []}, 200),
    ({"schema_version": 1, "project": "x", "sessions": [], "decisions": None}, 200),
])
def test_genuine_errors_never_become_skips(monkeypatch, capsys, body, code):
    monkeypatch.setattr(cli, "urlopen", lambda *a, **kw: response(body, code))
    assert cli.main(["context", "latest", "--hook-outcome"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith("candystore:")


def test_timeout_and_connection_are_failures(monkeypatch, capsys):
    for error in (TimeoutError(), ConnectionRefusedError()):
        def fail(*args, error=error, **kwargs):
            raise error
        monkeypatch.setattr(cli, "urlopen", fail)
        assert cli.main(["context", "latest", "--hook-outcome"]) == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert "unavailable" in out.err
