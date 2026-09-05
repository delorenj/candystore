"""Tool-call collapse (candystore.query.collapse_tool_runs) -- CANDYS-48.

Tool calls are 95.9% of the trail (159,816 of 166,723 rows over 7 days), so a
feed that renders them one per row is a scroll bar rather than a feed. With
them hidden, every consecutive run between two orchestrator-level events
becomes one counted fold marker.

The rules that make a collapsed feed trustworthy rather than merely shorter:
rows are folded, counts never are; and a failure always stands on its own.
"""

from __future__ import annotations

from candystore.query import MIN_FOLD_RUN, collapse_tool_runs


def tool(index: int, *, ok: bool = True, name: str = "Bash") -> dict:
    return {
        "id": f"t{index}",
        "type": "bloodbank.agent.tool.completed",
        "time": f"2026-09-04T12:00:{index:02d}Z",
        "class": "agent",
        "project": "candystore",
        "summary": {"tool": name, "row": {"ok": ok}},
    }


def other(index: int, kind: str = "bloodbank.conversation.turn.started") -> dict:
    return {
        "id": f"o{index}",
        "type": kind,
        "time": f"2026-09-04T12:00:{index:02d}Z",
        "class": "agent",
        "project": "candystore",
        "summary": {"row": {"ok": None}},
    }


def test_a_run_becomes_one_counted_marker():
    rows = collapse_tool_runs([other(0), *[tool(i) for i in range(1, 8)], other(9)])

    assert [row.get("kind") for row in rows] == [None, "fold", None]
    fold = rows[1]
    assert fold["count"] == 7
    assert fold["tools"] == {"Bash": 7}
    # Newest first, matching the feed's own order.
    assert fold["from"] == "2026-09-04T12:00:07Z"
    assert fold["to"] == "2026-09-04T12:00:01Z"
    assert fold["member_ids"] == [f"t{i}" for i in range(1, 8)]


def test_the_arithmetic_closes_exactly():
    """The invariant the whole feature rests on: sum of fold counts plus plain
    rows equals the unfolded row count. Rows are collapsed; nothing is sampled,
    so a collapsed feed can never quietly lose an event."""
    events = [other(0), *[tool(i) for i in range(1, 40)], other(40), tool(41), tool(42)]
    rows = collapse_tool_runs(events)

    folded = sum(row["count"] for row in rows if row.get("kind") == "fold")
    plain = sum(1 for row in rows if row.get("kind") != "fold")
    assert folded + plain == len(events)


def test_a_failure_terminates_the_run_and_stands_alone():
    """This is why a fold needs no error badge, and why hiding tool calls can
    never hide a problem: a failed call is never inside a fold."""
    events = [*[tool(i) for i in range(1, 5)], tool(5, ok=False), *[tool(i) for i in range(6, 10)]]
    rows = collapse_tool_runs(events)

    assert [row.get("kind") for row in rows] == ["fold", None, "fold"]
    assert rows[1]["id"] == "t5"
    for fold in (rows[0], rows[2]):
        assert "t5" not in fold["member_ids"]


def test_a_short_run_is_not_worth_a_control():
    """A chevron over one item is noise, and hiding two rows behind a click is
    a worse trade than showing them."""
    assert MIN_FOLD_RUN == 3
    for length in (1, 2):
        rows = collapse_tool_runs([other(0), *[tool(i) for i in range(1, length + 1)], other(9)])
        assert all(row.get("kind") != "fold" for row in rows)
        assert len(rows) == length + 2

    rows = collapse_tool_runs([other(0), *[tool(i) for i in range(1, 4)], other(9)])
    assert rows[1]["kind"] == "fold"


def test_a_fold_names_its_top_tools_and_counts_the_rest():
    events = [
        *[tool(i, name="Bash") for i in range(1, 6)],
        *[tool(i, name="Read") for i in range(6, 9)],
        tool(9, name="Edit"),
        tool(10, name="Grep"),
        tool(11, name="Glob"),
    ]
    fold = collapse_tool_runs(events)[0]

    assert fold["count"] == 11
    assert list(fold["tools"]) == ["Bash", "Read", "Edit"]
    assert fold["tools"]["Bash"] == 5
    assert fold["other_tools"] == 2
    assert sum(fold["tools"].values()) + 2 == fold["count"]


def test_a_run_at_either_edge_is_still_folded():
    """A page boundary is not a run boundary in the data, and a run that opens
    or closes the page must not silently render as plain rows."""
    leading = collapse_tool_runs([*[tool(i) for i in range(1, 6)], other(9)])
    assert leading[0]["kind"] == "fold"

    trailing = collapse_tool_runs([other(0), *[tool(i) for i in range(1, 6)]])
    assert trailing[-1]["kind"] == "fold"

    only = collapse_tool_runs([tool(i) for i in range(1, 6)])
    assert len(only) == 1 and only[0]["count"] == 5


def test_non_tool_events_are_never_folded():
    events = [other(i) for i in range(6)]
    assert collapse_tool_runs(events) == events


def test_the_retired_tool_spelling_folds_with_the_current_one():
    """`tool.tool_call.* -> agent.tool.*` is an entity rename that survives
    version-stripping, and 29,341 rows still carry the old spelling. Two
    spellings of one event must not break a run in half."""
    events = [
        tool(1),
        {**tool(2), "type": "bloodbank.v1.tool.tool_call.requested"},
        tool(3),
    ]
    rows = collapse_tool_runs(events)
    assert len(rows) == 1
    assert rows[0]["count"] == 3


def test_an_empty_feed_collapses_to_nothing():
    assert collapse_tool_runs([]) == []
