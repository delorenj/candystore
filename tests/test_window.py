"""The default-browse window policy (candystore.query.applied_window).

Pure: no database, so it runs without CANDYSTORE_TEST_DATABASE_URL.
"""

from __future__ import annotations

from datetime import UTC, datetime

from candystore.query import DEFAULT_WINDOW_HOURS, applied_window

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
SESSION = "550e8400-e29b-41d4-a716-446655440111"


def test_unbounded_browse_gets_the_default_window():
    from_time, to_time = applied_window(None, None, now=NOW)
    assert from_time == "2026-09-03T12:00:00+00:00"
    assert to_time is None
    assert DEFAULT_WINDOW_HOURS == 24


def test_an_explicit_floor_is_never_overridden():
    """Naming a floor is a statement that you want that floor."""
    assert applied_window("2026-01-01T00:00:00Z", None, now=NOW) == (
        "2026-01-01T00:00:00Z",
        None,
    )
    assert applied_window("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", now=NOW) == (
        "2026-01-01T00:00:00Z",
        "2026-02-01T00:00:00Z",
    )


def test_a_ceiling_alone_still_gets_a_floor_anchored_to_it():
    """A ceiling does not make a query cheap: `?to=2026-01-01` with no floor is
    still a scan of every row before that date. So `from` is invented -- and
    anchored to `to`, because a now-relative floor here would describe a window
    ending 8 months before it starts and return zero rows."""
    from_time, to_time = applied_window(None, "2026-01-01T00:00:00Z", now=NOW)
    assert from_time == "2025-12-31T00:00:00+00:00"
    assert to_time == "2026-01-01T00:00:00Z"


def test_a_point_lookup_is_exempt():
    """The two ways the UI asks for one specific thing must reach the whole
    trail. Both resolve through a btree index, so they are already bounded --
    and on an audit trail, almost everything worth looking up is older than a
    day."""
    assert applied_window(None, None, correlationid=SESSION, now=NOW) == (None, None)
    assert applied_window(None, None, q=SESSION, now=NOW) == (None, None)
    assert applied_window(None, None, q=f"  {SESSION.upper()}  ", now=NOW) == (None, None)
    # Exempt from the invented floor, not from a ceiling the caller asked for.
    assert applied_window(None, "2026-01-01T00:00:00Z", q=SESSION, now=NOW) == (
        None,
        "2026-01-01T00:00:00Z",
    )


def test_free_text_search_is_not_exempt():
    """A UUID is a point lookup; prose is not. Trigram search over the whole
    table is exactly the scan the window exists to prevent."""
    from_time, _ = applied_window(None, None, q="holocene traefik", now=NOW)
    assert from_time == "2026-09-03T12:00:00+00:00"


def test_unparseable_bounds_are_passed_through_untouched():
    """Rejecting a bad timestamp is the database's job, and it already reports
    it as a 400. Silently rewriting it here would hide the caller's typo."""
    assert applied_window("NOT-A-TIMESTAMP", None, now=NOW) == ("NOT-A-TIMESTAMP", None)
    from_time, to_time = applied_window(None, "NOT-A-TIMESTAMP", now=NOW)
    assert from_time == "2026-09-03T12:00:00+00:00"
    assert to_time == "NOT-A-TIMESTAMP"


def test_naive_timestamps_are_treated_as_utc():
    from_time, _ = applied_window(None, "2026-01-01T00:00:00", now=NOW)
    assert from_time == "2025-12-31T00:00:00+00:00"
