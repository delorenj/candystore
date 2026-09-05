"""The header strip's data (candystore.query.timeline) -- CANDYS-49.

The chart above the feed. Its one hard promise is that it never disagrees with
the feed below it: same window, same filters, and counts that include the tool
calls the feed is currently folding. Rows are collapsed; counts are not.
"""

from __future__ import annotations

import pytest

from candystore.db import insert_event
from candystore.query import (
    MAX_TIMELINE_SERIES,
    TIMELINE_BUCKETS,
    list_events,
    parse_time_bound,
    timeline,
)

WINDOW = {"from_time": "2026-05-24T00:00:00Z", "to_time": "2026-05-25T00:00:00Z"}


def _seed(sample_event, count: int = 6) -> None:
    for index in range(count):
        assert (
            insert_event(
                sample_event(
                    id=f"550e8400-e29b-41d4-a716-4466554ee{index:03d}",
                    time=f"2026-05-24T12:{index:02d}:00Z",
                    producer="claude-code",
                    actor={"type": "agent_cli", "cli": "claude"},
                    data={"tool_name": "Bash"},
                )
            )
            is True
        )


def test_buckets_sum_to_the_same_total_the_feed_reports(db, sample_event):
    """The identity that stops the strip from being decoration: if the chart
    and the list can disagree about the same window, the chart is a picture."""
    _seed(sample_event, 6)
    strip = timeline(bucket_seconds=60, **WINDOW)
    listed = list_events(limit=1, count_cap=None, **WINDOW)

    assert sum(bucket["total"] for bucket in strip["buckets"]) == listed["total"] == 6
    for bucket in strip["buckets"]:
        assert sum(bucket["series"].values()) == bucket["total"]


def test_the_strip_ignores_the_fold_toggle_entirely(db, sample_event):
    """`timeline()` takes no `tools` argument at all -- by construction, not by
    convention. Hiding tool rows must never change the shape of the chart."""
    _seed(sample_event, 6)
    assert "tools" not in timeline.__code__.co_varnames
    strip = timeline(bucket_seconds=60, **WINDOW)
    assert sum(bucket["total"] for bucket in strip["buckets"]) == 6


def test_bucket_width_is_echoed_and_respected(db, sample_event):
    _seed(sample_event, 6)
    # Six events one minute apart: six 60s buckets, one 3600s bucket.
    assert len(timeline(bucket_seconds=60, **WINDOW)["buckets"]) == 6
    hourly = timeline(bucket_seconds=3600, **WINDOW)
    assert len(hourly["buckets"]) == 1
    assert hourly["bucket_seconds"] == 3600
    assert hourly["buckets"][0]["total"] == 6


def test_an_arbitrary_bucket_width_is_refused(db, sample_event):
    """A closed set, like the window presets. An arbitrary width is another
    way to ask for a scan, and the strip only needs ~40 columns."""
    assert TIMELINE_BUCKETS == (1, 60, 300, 1800, 3600)
    with pytest.raises(ValueError, match="bucket must be one of"):
        timeline(bucket_seconds=137, **WINDOW)
    with pytest.raises(ValueError, match="group must be one of"):
        timeline(group="nonsense", **WINDOW)


def test_series_are_capped_and_the_remainder_is_kept_not_dropped(db, sample_event):
    """Past six, colours repeat and a stacked bar stops being readable. The
    seventh series is the honest remainder -- folded into `other` so the bars
    still sum to the true total, never silently discarded."""
    producers = [
        ("agent", {"type": "agent_cli", "cli": "claude"}, "claude-code", {}),
        ("subagent", {"type": "agent_cli", "cli": "codex"}, "codex-cli", {"payload": {"agent_id": "a"}}),
        ("pm_agent", {"type": "agent_cli", "cli": "claude"}, "hermes-agent:33god-pm", {}),
        ("ticket_webhook", {"type": "ticket_provider"}, "n8n-plane-webhook", {}),
        ("n8n_workflow", {"type": "service"}, "n8n", {}),
        ("service", {"type": "service"}, "tiller", {}),
        ("operator", {"type": "operator"}, "wax", {}),
    ]
    for index, (_, actor, producer, data) in enumerate(producers):
        assert (
            insert_event(
                sample_event(
                    id=f"550e8400-e29b-41d4-a716-4466554ef{index:03d}",
                    time="2026-05-24T12:00:00Z",
                    producer=producer,
                    source="urn:33god:test",
                    actor=actor,
                    data=data,
                )
            )
            is True
        )

    strip = timeline(bucket_seconds=3600, **WINDOW)
    assert len(strip["series"]) == MAX_TIMELINE_SERIES + 1
    assert strip["series"][-1] == "other"
    assert strip["truncated_series"], "something must have been folded to justify `other`"
    # The whole point: capping the LEGEND must not change the TOTAL.
    assert strip["buckets"][0]["total"] == len(producers)
    assert sum(strip["buckets"][0]["series"].values()) == len(producers)


def test_the_strip_honours_the_same_filters_as_the_feed(db, sample_event):
    _seed(sample_event, 4)
    assert (
        insert_event(
            sample_event(
                id="550e8400-e29b-41d4-a716-4466554ea999",
                time="2026-05-24T13:00:00Z",
                actor={"type": "agent_cli", "cli": "copilot"},
            )
        )
        is True
    )
    strip = timeline(bucket_seconds=3600, cli="copilot", **WINDOW)
    assert sum(bucket["total"] for bucket in strip["buckets"]) == 1


@pytest.mark.parametrize(
    ("value", "expected_kind"),
    [("-1h", "relative"), ("-90m", "relative"), ("-7d", "relative"), ("-30s", "relative")],
)
def test_relative_bounds_resolve(value, expected_kind):
    resolved = parse_time_bound(value)
    assert resolved != value and resolved.startswith("20"), expected_kind


def test_an_unrecognized_bound_is_passed_through_untouched():
    """So the database still reports a genuine typo, rather than this silently
    reshaping it into something that looks like it worked."""
    for value in ("2026-05-24T00:00:00Z", "-1fortnight", "-x", "", None):
        assert parse_time_bound(value) == value
