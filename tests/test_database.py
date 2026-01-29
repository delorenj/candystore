"""Tests for database operations."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from candystore.database import Database
from candystore.models import StoredEvent


@pytest.mark.asyncio
async def test_store_event(test_database: Database, sample_event_data: dict) -> None:
    """Test storing a single event."""
    event = await test_database.store_event(**sample_event_data)

    assert event.id == sample_event_data["id"]
    assert event.event_type == sample_event_data["event_type"]
    assert event.source == sample_event_data["source"]
    assert event.target == sample_event_data["target"]
    assert event.routing_key == sample_event_data["routing_key"]
    assert event.payload == sample_event_data["payload"]
    assert event.session_id == sample_event_data["session_id"]
    assert event.correlation_id == sample_event_data["correlation_id"]


@pytest.mark.asyncio
async def test_query_events_no_filters(test_database: Database, stored_event: StoredEvent) -> None:
    """Test querying events without filters."""
    events, total = await test_database.query_events()

    assert total >= 1
    assert len(events) >= 1
    assert any(e.id == stored_event.id for e in events)


@pytest.mark.asyncio
async def test_query_events_by_event_type(
    test_database: Database, stored_event: StoredEvent
) -> None:
    """Test querying events by event type."""
    events, total = await test_database.query_events(event_type=stored_event.event_type)

    assert total >= 1
    assert all(e.event_type == stored_event.event_type for e in events)


@pytest.mark.asyncio
async def test_query_events_by_session_id(
    test_database: Database, stored_event: StoredEvent
) -> None:
    """Test querying events by session ID."""
    events, total = await test_database.query_events(session_id=stored_event.session_id)

    assert total >= 1
    assert all(e.session_id == stored_event.session_id for e in events)


@pytest.mark.asyncio
async def test_query_events_by_source(test_database: Database, stored_event: StoredEvent) -> None:
    """Test querying events by source."""
    events, total = await test_database.query_events(source=stored_event.source)

    assert total >= 1
    assert all(e.source == stored_event.source for e in events)


@pytest.mark.asyncio
async def test_query_events_by_time_range(
    test_database: Database, sample_event_data: dict
) -> None:
    """Test querying events by time range."""
    # Store multiple events with different timestamps
    now = datetime.now(timezone.utc)

    await test_database.store_event(
        **{
            **sample_event_data,
            "id": str(uuid4()),
            "timestamp": now - timedelta(hours=2),
        }
    )

    await test_database.store_event(
        **{
            **sample_event_data,
            "id": str(uuid4()),
            "timestamp": now - timedelta(hours=1),
        }
    )

    await test_database.store_event(
        **{
            **sample_event_data,
            "id": str(uuid4()),
            "timestamp": now,
        }
    )

    # Query for events in the last 90 minutes
    events, total = await test_database.query_events(
        start_time=now - timedelta(minutes=90),
        end_time=now,
    )

    assert total >= 2  # Should include the last two events


@pytest.mark.asyncio
async def test_query_events_pagination(test_database: Database, sample_event_data: dict) -> None:
    """Test pagination in event queries."""
    # Store multiple events
    for i in range(10):
        await test_database.store_event(
            **{
                **sample_event_data,
                "id": str(uuid4()),
            }
        )

    # Query with pagination
    events_page1, total = await test_database.query_events(limit=5, offset=0)
    events_page2, _ = await test_database.query_events(limit=5, offset=5)

    assert len(events_page1) == 5
    assert len(events_page2) == 5
    assert total >= 10

    # Ensure no overlap
    page1_ids = {e.id for e in events_page1}
    page2_ids = {e.id for e in events_page2}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.asyncio
async def test_query_events_combined_filters(
    test_database: Database, sample_event_data: dict
) -> None:
    """Test querying with multiple filters combined."""
    session_id = str(uuid4())
    event_type = "test.combined.filter"

    # Store matching event
    await test_database.store_event(
        **{
            **sample_event_data,
            "id": str(uuid4()),
            "event_type": event_type,
            "session_id": session_id,
        }
    )

    # Store non-matching events
    await test_database.store_event(
        **{
            **sample_event_data,
            "id": str(uuid4()),
            "event_type": "other.type",
            "session_id": session_id,
        }
    )

    # Query with combined filters
    events, total = await test_database.query_events(
        event_type=event_type,
        session_id=session_id,
    )

    assert total >= 1
    assert all(e.event_type == event_type and e.session_id == session_id for e in events)
