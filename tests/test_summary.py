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
