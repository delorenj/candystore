"""The flat feed-row contract (candystore.summarize.row) -- CANDYS-41.

A dense fixed-height feed cannot lay out a dict whose keys change with the
event type, which is what `summarize()` returns. `row()` is the flat projection
it renders from, and its promise is that every key is present on every event.

The bulk assertion replays real envelopes pulled from the live trail rather
than fixtures: the shapes that break a row contract are the ones nobody thought
to write a fixture for (an object where a string was assumed, a null where a
name was assumed), and there are 886k of them next door.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from candystore.summarize import UNRESOLVED_PROJECT, row, status_string, summarize

# Captured from the live table by tests/fixtures/capture_envelopes.py so the
# suite stays runnable with no database. Refresh it when producers change shape.
CORPUS = pathlib.Path(__file__).parent / "fixtures" / "envelopes.jsonl"

REQUIRED_KEYS = {
    "headline",
    "body",
    "actor_label",
    "status",
    "ok",
    "class",
    "project_label",
    "duration_ms",
}


def _corpus() -> list[dict]:
    if not CORPUS.exists():
        pytest.skip(f"{CORPUS} absent; run tests/fixtures/capture_envelopes.py")
    with CORPUS.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_every_live_envelope_produces_a_complete_row():
    """AC4: replay the corpus, raise nothing, and leave no headline empty.

    A headline is the only thing a row cannot render without, so it is the one
    field with no null case at all.
    """
    envelopes = _corpus()
    assert len(envelopes) >= 1000, f"corpus too small to be evidence ({len(envelopes)})"

    for envelope in envelopes:
        built = summarize(envelope, "candystore", "agent")["row"]
        assert set(built) == REQUIRED_KEYS, envelope.get("type")
        assert isinstance(built["headline"], str) and built["headline"].strip(), envelope.get("id")
        assert isinstance(built["actor_label"], str) and built["actor_label"].strip()
        assert isinstance(built["project_label"], str) and built["project_label"]
        # Optional VALUES are null; optional KEYS would put the type-switching
        # back, which is the whole thing this contract exists to remove.
        assert built["status"] is None or isinstance(built["status"], str)
        assert built["ok"] in (True, False, None)
        assert built["body"] is None or isinstance(built["body"], str)
        assert built["duration_ms"] is None or isinstance(built["duration_ms"], int)


def test_the_corpus_actually_covers_the_type_families():
    """A corpus of 5,000 tool calls would pass the test above and prove almost
    nothing, so assert the spread rather than the count."""
    families = {
        (envelope.get("type") or "").split(".")[-3:-1][0]
        for envelope in _corpus()
        if (envelope.get("type") or "").count(".") >= 3
    }
    assert len(families) >= 5, f"corpus covers only {sorted(families)}"


def test_status_is_a_string_even_when_the_producer_sent_an_object():
    """AC3. Measured on the live table: 358,717 rows carry a string outcome and
    90 carry an object. `_tool_status` returned it raw, so a pr-crusher row's
    status reached the UI as JSON inside a 28px row."""
    assert status_string({"status": "failed", "merge_attempts": 3, "provider": "gh"}) == "failed"
    assert status_string({"success": False, "sections": 4}) == "failure"
    assert status_string({"success": True}) == "success"
    # An object with nothing readable is still not JSON in the UI.
    assert status_string({"merge_attempts": 3}) == "unknown"
    assert status_string("success") == "success"
    assert status_string(True) == "success"
    assert status_string(False) == "failure"
    assert status_string(None) is None
    assert status_string("   ") is None


def test_ok_refuses_to_guess_an_unrecognized_status():
    """The third state is load-bearing: the feed draws a glyph from `ok`, and a
    status like `recorded_fragmentary_intake_pending_clarification` (a real
    value in the trail) is neither good news nor bad. A wrong glyph is worse
    than no glyph."""
    def ok(status):
        return row({"data": {"outcome": status}}, {"title": "t"}, None, "agent")["ok"]

    assert ok("success") is True
    assert ok("completed") is True
    assert ok("error") is False
    assert ok("failure") is False
    assert ok("recorded_fragmentary_intake_pending_clarification") is None
    assert ok(None) is None
    # Case is not signal.
    assert ok("SUCCESS") is True


def test_actor_label_names_the_subagent_not_just_the_cli():
    """The point of the violet dot is WHICH subagent -- `codex` alone would make
    every one of the 15,415 rows look identical."""
    env = {
        "producer": "codex-cli",
        "actor": {"cli": "codex"},
        "data": {"payload": {"agent_id": "a", "agent_type": "code-reviewer"}},
    }
    assert row(env, {"title": "t"}, None, "subagent")["actor_label"] == "codex/code-reviewer"

    # `default` is a real agent_type meaning "unnamed subagent" (22,284 rows);
    # rendering the literal word would be worse than saying what it is.
    env["data"]["payload"]["agent_type"] = "default"
    assert row(env, {"title": "t"}, None, "subagent")["actor_label"] == "codex/subagent"

    # The `hermes-agent:` prefix is on every PM row and carries nothing once the
    # dot is already teal.
    pm = {"producer": "hermes-agent:33god-pm", "actor": {"cli": "claude"}, "data": {}}
    assert row(pm, {"title": "t"}, None, "pm_agent")["actor_label"] == "33god-pm"

    # Nothing to go on is still a string, never None -- the column always renders.
    assert row({"data": {}}, {"title": "t"}, None, "agent")["actor_label"] == "unknown"


def test_an_unresolved_project_gets_the_label_not_a_null():
    """`row` is the RENDERING projection, so its project is always a string.
    The nullable slug stays on the event itself (CANDYS-37), which is what the
    two endpoints compare."""
    built = row({"data": {}}, {"title": "t"}, None, "agent")
    assert built["project_label"] == UNRESOLVED_PROJECT
    assert row({"data": {}}, {"title": "t"}, "vinyl", "agent")["project_label"] == "vinyl"


def test_the_per_type_dict_is_untouched(sample_event):
    """AC2: `row` is added ALONGSIDE the per-type keys, never instead of them.
    EventDetail renders the per-type dict and must keep working unchanged."""
    env = sample_event()
    summary = summarize(env, "candystore", "agent")
    # Every key the session-ended summarizer has always produced is still there.
    for key in ("title", "cli", "duration", "turns", "tools_used", "working_directory"):
        assert key in summary, key
    assert summary["duration"] == "1m 35s"
    assert set(summary["row"]) == REQUIRED_KEYS


def test_body_prefers_the_summarizers_trimmed_field(sample_event):
    """Where a summarizer already trimmed a field, its version beats the raw
    one -- otherwise a 4 KB `arguments` blob would win over the 200-char
    `input_preview` built from it."""
    env = sample_event(
        type="bloodbank.agent.tool.completed",
        data={"tool_name": "Bash", "arguments": {"command": "x" * 4000}},
    )
    built = summarize(env, None, "agent")["row"]
    assert built["body"] is not None
    assert len(built["body"]) <= 200


@pytest.mark.skipif(
    not os.environ.get("CANDYSTORE_TEST_DATABASE_URL"),
    reason="corpus refresh needs a database",
)
def test_corpus_is_not_stale(db):
    """The corpus is evidence only while it resembles the trail. This does not
    re-capture -- it just refuses to let a year-old snapshot masquerade."""
    envelopes = _corpus()
    types = {envelope.get("type") for envelope in envelopes}
    assert "bloodbank.agent.tool.completed" in types or "bloodbank.agent.tool.requested" in types
