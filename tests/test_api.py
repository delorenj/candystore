"""Tests for REST API."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from candystore.api import create_app
from candystore.database import Database
from candystore.models import StoredEvent


@pytest.fixture
async def test_client(test_database: Database) -> AsyncClient:
    """Create test client for FastAPI app."""
    app = create_app(test_database)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health_check(test_client: AsyncClient) -> None:
    """Test health check endpoint."""
    response = await test_client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "database" in data


@pytest.mark.asyncio
async def test_query_events_no_filters(
    test_client: AsyncClient, stored_event: StoredEvent
) -> None:
    """Test querying events without filters."""
    response = await test_client.get("/events")

    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert "has_more" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_query_events_with_filters(
    test_client: AsyncClient, test_database: Database
) -> None:
    """Test querying events with filters."""
    session_id = str(uuid4())
    event_type = "test.api.filter"

    # Store test event
    await test_database.store_event(
        id=str(uuid4()),
        event_type=event_type,
        source="test-source",
        target=None,
        routing_key=event_type,
        timestamp=datetime.now(timezone.utc),
        payload={"test": "data"},
        session_id=session_id,
    )

    # Query with filters
    response = await test_client.get(
        "/events",
        params={"session_id": session_id, "event_type": event_type},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert all(e["event_type"] == event_type for e in data["events"])
    assert all(e["session_id"] == session_id for e in data["events"])


@pytest.mark.asyncio
async def test_query_events_pagination(
    test_client: AsyncClient, test_database: Database
) -> None:
    """Test event query pagination."""
    # Store multiple events
    for i in range(15):
        await test_database.store_event(
            id=str(uuid4()),
            event_type=f"test.pagination.{i}",
            source="test-source",
            target=None,
            routing_key="test.pagination",
            timestamp=datetime.now(timezone.utc),
            payload={"index": i},
        )

    # Query first page
    response = await test_client.get("/events", params={"limit": 10, "offset": 0})
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 10
    assert data["has_more"] is True

    # Query second page
    response = await test_client.get("/events", params={"limit": 10, "offset": 10})
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) >= 5


@pytest.mark.asyncio
async def test_get_event_by_id(test_client: AsyncClient, stored_event: StoredEvent) -> None:
    """Test getting a specific event by ID."""
    response = await test_client.get(f"/events/{stored_event.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == stored_event.id
    assert data["event_type"] == stored_event.event_type
    assert data["source"] == stored_event.source


@pytest.mark.asyncio
async def test_get_event_by_id_not_found(test_client: AsyncClient) -> None:
    """Test getting a non-existent event."""
    fake_id = str(uuid4())
    response = await test_client.get(f"/events/{fake_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_query_validation(test_client: AsyncClient) -> None:
    """Test query parameter validation."""
    # Test invalid limit
    response = await test_client.get("/events", params={"limit": 2000})
    assert response.status_code == 422  # Validation error

    # Test invalid offset
    response = await test_client.get("/events", params={"offset": -1})
    assert response.status_code == 422
