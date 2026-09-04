from __future__ import annotations

import pytest

from candystore.db import cursor, insert_event
from candystore.query import SEARCH_MIN_TERM, SearchError, list_events

SESSION = "550e8400-e29b-41d4-a716-4466554aa000"


def tool_event(sample_event, event_id: str, **data):
    """A tool-call event with the fixture's session-shaped defaults cleared, so
    each test controls exactly which text is searchable."""
    payload = {
        "session_id": None,
        "working_directory": None,
        "git_branch": None,
        "duration_seconds": None,
        "total_turns": None,
        "tools_used": None,
        "final_status": None,
        "project": None,
    }
    payload.update(data)
    return sample_event(
        id=event_id,
        correlationid=SESSION,
        domain="agent",
        type="bloodbank.agent.tool.invoked",
        data=payload,
    )


def test_search_spans_envelope_and_payload_and_ignores_case(db, sample_event):
    edit = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa001",
        tool_name="Edit",
        arguments='{"file_path": "/home/delorenj/code/33GOD/holocene/src/App.tsx"}',
    )
    bash = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa002",
        tool_name="Bash",
        arguments='{"command": "mise run build:ui"}',
    )
    for event in (edit, bash):
        assert insert_event(event) is True

    # A short payload field (tool_name) and a bounded prose one (arguments) are
    # both in the haystack, and so is the envelope's own type.
    assert _ids(list_events(q="holocene")) == {edit["id"]}
    assert _ids(list_events(q="build:ui")) == {bash["id"]}
    assert _ids(list_events(q="agent.tool.invoked")) == {edit["id"], bash["id"]}

    # search_text is stored lowercased and matched with ILIKE, so neither the
    # stored case nor the query's case can hide a row.
    assert _ids(list_events(q="EDIT")) == {edit["id"]}
    assert _ids(list_events(q="app.tsx")) == {edit["id"]}


def test_search_requires_every_term_to_match(db, sample_event):
    holocene = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa010",
        tool_name="Edit",
        arguments='{"file_path": "holocene/src/App.tsx"}',
    )
    candystore = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa011",
        tool_name="Read",
        arguments='{"file_path": "candystore/query.py"}',
    )
    for event in (holocene, candystore):
        assert insert_event(event) is True

    # Whitespace-separated terms are ANDed, and may land in different fields --
    # that is the point: "an Edit in holocene" is not one literal substring of
    # any single column.
    assert _ids(list_events(q="edit holocene")) == {holocene["id"]}
    assert _ids(list_events(q="read candystore")) == {candystore["id"]}
    assert list_events(q="edit candystore")["total"] == 0


def test_search_treats_like_metacharacters_as_literal_text(db, sample_event):
    underscore = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa020",
        arguments='{"needle": "file_path"}',
    )
    wildcarded = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa021",
        arguments='{"needle": "filexpath"}',
    )
    percent = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa022",
        arguments='{"needle": "coverage 92% of lines"}',
    )
    for event in (underscore, wildcarded, percent):
        assert insert_event(event) is True

    # Unescaped, LIKE reads `_` as "any character" and would drag `filexpath`
    # in -- searching for a snake_case identifier is far too common for that.
    assert _ids(list_events(q="file_path")) == {underscore["id"]}
    # ... and an unescaped `%` would match every row in the table.
    assert _ids(list_events(q="92%")) == {percent["id"]}


def test_search_refuses_terms_the_trigram_index_cannot_serve(db, sample_event):
    assert insert_event(tool_event(sample_event, "550e8400-e29b-41d4-a716-4466554aa030")) is True

    # Under three characters there is no complete trigram, so the index cannot
    # be used and the query degrades to a full scan of the whole trail. Refuse
    # it (main.py renders this as a 400) rather than serve it in minutes.
    for term in ("ab", "%", "x"):
        with pytest.raises(SearchError, match=str(SEARCH_MIN_TERM)):
            list_events(q=term)

    # One short term poisons an otherwise-servable multi-term query.
    with pytest.raises(SearchError, match="'xy'"):
        list_events(q="holocene xy")

    # A blank q is not a search at all -- it must not filter, and must not raise.
    assert list_events(q="   ")["total"] == 1


def test_search_by_uuid_resolves_event_and_session_ids(db, sample_event):
    first = tool_event(sample_event, "550e8400-e29b-41d4-a716-4466554aa040", tool_name="Edit")
    second = tool_event(sample_event, "550e8400-e29b-41d4-a716-4466554aa041", tool_name="Bash")
    other_session = sample_event(
        id="550e8400-e29b-41d4-a716-4466554aa042",
        correlationid="550e8400-e29b-41d4-a716-4466554bb000",
    )
    for event in (first, second, other_session):
        assert insert_event(event) is True

    # Pasting an id in is the commonest thing anyone does with one. UUIDs are
    # deliberately absent from search_text, so this has to be an exact-column
    # match on id/correlationid, not a substring scan.
    assert _ids(list_events(q=first["id"])) == {first["id"]}
    assert _ids(list_events(q=SESSION)) == {first["id"], second["id"]}
    assert _ids(list_events(q=SESSION.upper())) == {first["id"], second["id"]}
    assert list_events(q="550e8400-e29b-41d4-a716-4466554cc000")["total"] == 0


def test_search_composes_with_the_other_filters(db, sample_event):
    claude = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa050",
        arguments='{"file_path": "holocene/src/App.tsx"}',
    )
    gemini = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa051",
        arguments='{"file_path": "holocene/src/main.tsx"}',
    )
    gemini["actor"] = {"cli": "gemini", "provider": "google"}
    for event in (claude, gemini):
        assert insert_event(event) is True

    assert list_events(q="holocene")["total"] == 2
    assert _ids(list_events(q="holocene", cli="claude")) == {claude["id"]}
    assert _ids(list_events(q="holocene", scope="agent.tool")) == {claude["id"], gemini["id"]}
    assert list_events(q="holocene", domain="system")["total"] == 0


def test_search_text_is_bounded_per_field(db, sample_event):
    """The haystack is capped so the index stays bounded (see 003_search.sql).
    Text past a field's cap is genuinely unsearchable -- assert the boundary so
    the trade-off is visible rather than a mystery when a deep needle misses."""
    padding = "z" * 400
    event = tool_event(
        sample_event,
        "550e8400-e29b-41d4-a716-4466554aa060",
        arguments=f'{{"head": "NEEDLEHEAD", "pad": "{padding}", "tail": "NEEDLETAIL"}}',
    )
    assert insert_event(event) is True

    assert _ids(list_events(q="needlehead")) == {event["id"]}
    assert list_events(q="needletail")["total"] == 0


def test_search_reaches_deep_into_a_long_prompt(db, sample_event):
    """prompt_text carries a 4000-char cap while everything else prose-shaped is
    at 256, because prompt-bearing rows are only 0.69% of the trail -- generosity
    there is nearly free. Assert the depth actually reached, not just that short
    prompts work."""
    turn = sample_event(
        id="550e8400-e29b-41d4-a716-4466554aa070",
        type="bloodbank.conversation.turn.started",
        domain="conversation",
        data={
            "prompt_text": (
                "NEEDLEOPEN " + ("filler word " * 240) + " NEEDLEDEEP "
                + ("filler word " * 200) + " NEEDLEBEYOND"
            )
        },
    )
    assert insert_event(turn) is True

    # ~2900 chars in -- unreachable under the old 256-char prompt cap.
    assert _ids(list_events(q="needleopen")) == {turn["id"]}
    assert _ids(list_events(q="needledeep")) == {turn["id"]}
    # Past 4000, and therefore genuinely gone.
    assert list_events(q="needlebeyond")["total"] == 0


def test_search_text_expression_carries_no_dead_fields(db):
    """004_search_caps.sql duplicates 003's expression to carry an already-
    migrated database across, so the two files can drift. This asserts the END
    STATE instead of either file: whichever migration path built the column, it
    must land on the measured policy.

    `input_preview` is the specific regression to catch -- it was in the first
    expression, is present on 0 of 871,438 live rows, and cost a 256-char slot
    on every row until it was measured."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = 'events'::regclass AND a.attname = 'search_text'
            """
        )
        row = cur.fetchone()

    assert row is not None, "events.search_text has no generation expression"
    expression = row[0]

    for dead in ("input_preview", "payload"):
        assert dead not in expression, f"{dead} is back in the haystack"
    # The fields that carry the search signal must still be there.
    for live in ("arguments", "prompt_text", "tool_name", "working_directory"):
        assert live in expression, f"{live} fell out of the haystack"
    assert "4000" in expression, "prompt_text is not on the 4000-char cap"
    assert "4096" in expression, "the total cap is not 4096"


def _ids(result: dict) -> set[str]:
    return {event["id"] for event in result["events"]}
