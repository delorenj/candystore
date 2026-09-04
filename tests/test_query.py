from __future__ import annotations

from candystore.db import insert_event
from candystore.query import (
    by_cli,
    by_project,
    daily,
    get_event,
    get_event_record,
    get_session_events,
    get_session_summary,
    heatmap,
    list_events,
)


def test_event_queries_and_summaries(db, project_map, sample_event):
    session_id = "550e8400-e29b-41d4-a716-446655440111"
    first = sample_event(
        id="550e8400-e29b-41d4-a716-446655440000",
        correlationid=session_id,
        time="2026-05-24T15:00:00Z",
        type="bloodbank.v1.cli.session.started",
    )
    second = sample_event(
        id="550e8400-e29b-41d4-a716-446655440001",
        correlationid=session_id,
        time="2026-05-24T16:00:00Z",
        type="bloodbank.v1.cli.session.ended",
    )
    third = sample_event(
        id="550e8400-e29b-41d4-a716-446655440002",
        correlationid="550e8400-e29b-41d4-a716-446655440222",
        time="2026-05-24T17:00:00Z",
        domain="system",
        type="bloodbank.v1.system.heartbeat.received",
        actor={"cli": "copilot"},
        data={"project": "other"},
    )
    for event in (first, second, third):
        assert insert_event(event) is True

    # `project=` is a registry slug resolved through project_dir_map now, not a
    # substring of a directory basename -- the sample events' working_directory
    # is /home/delorenj/code/33GOD/candystore, which the map assigns to
    # `candystore` (see the project_map fixture).
    listed = list_events(cli="claude", project="candystore", limit=10)
    assert listed["total"] == 2
    assert listed["events"][0]["summary"]["title"].startswith("Session ended")

    record = get_event_record(second["id"])
    assert record is not None
    assert record["type"] == "bloodbank.v1.cli.session.ended"
    assert get_event(second["id"])["id"] == second["id"]

    timeline = get_session_events(session_id)
    assert [event["id"] for event in timeline] == [first["id"], second["id"]]

    summary = get_session_summary(session_id)
    assert summary["events_count"] == 2
    assert summary["duration_seconds"] == 95
    assert summary["cli"] == "claude"

    assert heatmap(group_by="cli")[0]["count"] >= 1
    assert daily()[0]["count"] == 3
    assert {item["cli"] for item in by_cli()} == {"claude", "copilot"}
    assert {item["project"] for item in by_project()} >= {"candystore", "other"}


def test_capped_count_reports_at_least_not_exactly(db, sample_event):
    """A capped count must be legible as a floor, or the UI renders "10,000
    events" as fact when there are 118,745. `count_cap` counts through a
    bounded subquery so Postgres stops early instead of finding every match."""
    for index in range(5):
        event = sample_event(id=f"550e8400-e29b-41d4-a716-44665544c{index:03d}")
        assert insert_event(event) is True

    exact = list_events(limit=1)
    assert exact["total"] == 5
    assert exact["total_capped"] is False

    # Cap below the true count: the number is the cap, and the flag says so.
    capped = list_events(limit=1, count_cap=3)
    assert capped["total"] == 4
    assert capped["total_capped"] is True

    # Cap above it: the number is exact, and the flag must not cry wolf.
    under = list_events(limit=1, count_cap=50)
    assert under["total"] == 5
    assert under["total_capped"] is False

    # Exactly at the cap is the off-by-one that matters -- 5 rows with cap 5 is
    # an exact answer, not a floor.
    boundary = list_events(limit=1, count_cap=5)
    assert boundary["total"] == 5
    assert boundary["total_capped"] is False

    # Skipping the count must not cost one, and must not fake one either.
    none = list_events(limit=1, total=False, count_cap=3)
    assert none["total"] is None
    assert none["total_capped"] is False
    assert len(none["events"]) == 1


def test_scope_filter_uses_domain_and_entity_not_repo_slug(db, sample_event):
    valid_decision = sample_event(
        id="550e8400-e29b-41d4-a716-446655440010",
        time="2026-05-24T18:00:00Z",
        domain="repo",
        type="bloodbank.v1.repo.decision.recorded",
        data={"repo": "candybar", "decision": "Use repo-neutral event types"},
    )
    legacy_slugged_decision = sample_event(
        id="550e8400-e29b-41d4-a716-446655440011",
        time="2026-05-24T19:00:00Z",
        domain="repo",
        type="bloodbank.v1.repo.candybar.decision.recorded",
        data={"repo": "candybar", "decision": "Legacy invalid slugged type"},
    )
    version_free_decision = sample_event(
        id="550e8400-e29b-41d4-a716-446655440012",
        time="2026-05-24T20:00:00Z",
        domain="repo",
        type="bloodbank.repo.decision.recorded",
        data={"repo": "candybar", "decision": "Version-free four-token type"},
    )
    for event in (valid_decision, legacy_slugged_decision, version_free_decision):
        assert insert_event(event) is True

    decision_events = list_events(scope="repo.decision", limit=10)

    # Both shapes match: the version token's PRESENCE must not shift the scope
    # filter's reading of domain/entity. Positional split_part on the raw type
    # silently answered "repo.decision" with only the v1 row and filed the
    # four-token one under scope "decision" instead.
    assert decision_events["total"] == 2
    assert {event["type"] for event in decision_events["events"]} == {
        "bloodbank.v1.repo.decision.recorded",
        "bloodbank.repo.decision.recorded",
    }

    # Domain-only scope reads the same slot in both shapes -- and the legacy
    # slugged type is still domain `repo`, so it belongs here.
    assert list_events(scope="repo", limit=10)["total"] == 3
    assert list_events(scope="decision", limit=10)["total"] == 0


def test_session_summary_counts_post_migration_types(db, sample_event):
    """End-to-end through the DB: get_session_summary() must see the
    version-free four-token shape. Before the fix this session reported
    turns/tools_requested/tools_invoked = 0 while the raw rows said 2/3/1,
    because the lookups were hardcoded to `bloodbank.v1.tool.tool_call.*` --
    a spelling that is both versioned AND uses the retired entity."""
    session_id = "550e8400-e29b-41d4-a716-446655440333"
    events = [
        ("550e8400-e29b-41d4-a716-446655440100", "bloodbank.conversation.turn.started", "15:00"),
        ("550e8400-e29b-41d4-a716-446655440101", "bloodbank.conversation.turn.started", "15:01"),
        ("550e8400-e29b-41d4-a716-446655440102", "bloodbank.agent.tool.requested", "15:02"),
        ("550e8400-e29b-41d4-a716-446655440103", "bloodbank.agent.tool.requested", "15:03"),
        # The retired entity spelling folds into the same bucket.
        ("550e8400-e29b-41d4-a716-446655440104", "bloodbank.v1.tool.tool_call.requested", "15:04"),
        ("550e8400-e29b-41d4-a716-446655440105", "bloodbank.agent.tool.invoked", "15:05"),
    ]
    for event_id, event_type, hhmm in events:
        assert (
            insert_event(
                sample_event(
                    id=event_id,
                    correlationid=session_id,
                    time=f"2026-05-24T{hhmm}:00Z",
                    type=event_type,
                    data={"total_turns": None, "duration_seconds": None},
                )
            )
            is True
        )

    # agent.session.ended supersedes cli.session.ended; the old probe only
    # matched the latter, so data.total_turns never reached the summary.
    assert (
        insert_event(
            sample_event(
                id="550e8400-e29b-41d4-a716-446655440106",
                correlationid=session_id,
                time="2026-05-24T15:10:00Z",
                type="bloodbank.agent.session.ended",
                data={"total_turns": 42, "duration_seconds": 600},
            )
        )
        is True
    )

    summary = get_session_summary(session_id)

    assert summary["tools_requested"] == 3
    assert summary["tools_invoked"] == 1
    assert summary["turns"] == 42
    assert summary["duration_seconds"] == 600
    assert summary["events_count"] == 7
