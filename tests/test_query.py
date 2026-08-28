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


def test_event_queries_and_summaries(db, sample_event):
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