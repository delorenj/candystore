"""Database connection and operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from candystore.config import settings
from candystore.logging_config import get_logger
from candystore.models import Base, StoredEvent

logger = get_logger(__name__)


class Database:
    """Async database connection manager."""

    def __init__(self) -> None:
        self.engine = create_async_engine(
            settings.database_url,
            echo=False,  # Set to True for SQL query logging
            pool_pre_ping=True,  # Verify connections before using
            pool_size=20,  # Connection pool size
            max_overflow=10,  # Max overflow connections
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_db(self) -> None:
        """Initialize database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_initialized", tables=list(Base.metadata.tables.keys()))

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()
        logger.info("database_closed")

    async def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.session_factory()

    async def store_event(
        self,
        event_id: str,
        event_type: str,
        source: str,
        target: str | None,
        routing_key: str,
        timestamp: datetime,
        payload: dict[str, Any],
        session_id: str | None = None,
        correlation_id: str | None = None,
        storage_latency_ms: float | None = None,
    ) -> StoredEvent:
        """Store a single event in the database.

        Args:
            event_id: Unique event ID (UUID)
            event_type: Event type/category
            source: Event source service
            target: Event target service (optional)
            routing_key: RabbitMQ routing key
            timestamp: Event timestamp
            payload: Full event payload
            session_id: Session ID for tracing (optional)
            correlation_id: Correlation ID for tracing (optional)
            storage_latency_ms: Time taken to store event (optional)

        Returns:
            Created StoredEvent instance
        """
        async with self.session_factory() as session:
            stored_event = StoredEvent(
                id=event_id,
                event_type=event_type,
                source=source,
                target=target,
                routing_key=routing_key,
                timestamp=timestamp,
                payload=payload,
                session_id=session_id,
                correlation_id=correlation_id,
                storage_latency_ms=storage_latency_ms,
            )
            session.add(stored_event)
            await session.commit()
            await session.refresh(stored_event)
            return stored_event

    async def query_events(
        self,
        session_id: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        target: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[StoredEvent], int]:
        """Query events with filters and pagination.

        Args:
            session_id: Filter by session ID
            event_type: Filter by event type
            source: Filter by source service
            target: Filter by target service
            start_time: Filter by start timestamp (inclusive)
            end_time: Filter by end timestamp (inclusive)
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            Tuple of (events list, total count)
        """
        async with self.session_factory() as session:
            # Build query with filters
            query = select(StoredEvent)

            if session_id:
                query = query.where(StoredEvent.session_id == session_id)
            if event_type:
                query = query.where(StoredEvent.event_type == event_type)
            if source:
                query = query.where(StoredEvent.source == source)
            if target:
                query = query.where(StoredEvent.target == target)
            if start_time:
                query = query.where(StoredEvent.timestamp >= start_time)
            if end_time:
                query = query.where(StoredEvent.timestamp <= end_time)

            # Get total count
            count_query = select(StoredEvent.id)
            if session_id:
                count_query = count_query.where(StoredEvent.session_id == session_id)
            if event_type:
                count_query = count_query.where(StoredEvent.event_type == event_type)
            if source:
                count_query = count_query.where(StoredEvent.source == source)
            if target:
                count_query = count_query.where(StoredEvent.target == target)
            if start_time:
                count_query = count_query.where(StoredEvent.timestamp >= start_time)
            if end_time:
                count_query = count_query.where(StoredEvent.timestamp <= end_time)

            count_result = await session.execute(count_query)
            total_count = len(count_result.all())

            # Order by timestamp (newest first) and apply pagination
            query = query.order_by(StoredEvent.timestamp.desc())
            query = query.limit(limit).offset(offset)

            # Execute query
            result = await session.execute(query)
            events = result.scalars().all()

            return list(events), total_count
