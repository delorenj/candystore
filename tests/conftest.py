"""Pytest configuration and fixtures."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from candystore.database import Database
from candystore.models import Base, StoredEvent


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_database() -> AsyncGenerator[Database, None]:
    """Create test database with in-memory SQLite."""
    # Use in-memory SQLite for tests
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create database instance
    db = Database()
    db.engine = engine
    db.session_factory = session_factory

    yield db

    # Cleanup
    await engine.dispose()


@pytest.fixture
def sample_event_data() -> dict:
    """Create sample event data for testing."""
    return {
        "id": str(uuid4()),
        "event_type": "test.event.created",
        "source": "test-service",
        "target": "target-service",
        "routing_key": "test.event.created",
        "timestamp": datetime.now(timezone.utc),
        "payload": {
            "message": "Test event",
            "count": 42,
            "nested": {"key": "value"},
        },
        "session_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "storage_latency_ms": 5.0,
    }


@pytest.fixture
async def stored_event(test_database: Database, sample_event_data: dict) -> StoredEvent:
    """Create and store a sample event."""
    event = await test_database.store_event(**sample_event_data)
    return event
