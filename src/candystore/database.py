"""Database connection and operations."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

    async def init_schema(self) -> None:
        """Initialize the configured database schema.

        Task CANPM-T2 uses the name ``init_schema``; keep ``init_db`` as the
        historical API and route both through SQLAlchemy metadata so tests can
        run on SQLite while production can target PostgreSQL.
        """
        await self.init_db()

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()
        logger.info("database_closed")

    async def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.session_factory()

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        """Normalize timestamps to UTC + tz-aware to match TIMESTAMPTZ columns."""
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)

    @staticmethod
    def _derive_domain(event_type: str) -> str:
        """Best-effort domain extraction from Bloodbank event types."""
        parts = event_type.split(".")
        if len(parts) >= 4 and parts[0] == "bloodbank":
            return parts[3]
        return parts[0] if parts else "unknown"

    @staticmethod
    def _derive_service(source: str) -> str:
        """Best-effort service extraction from a source string."""
        if "@" in source:
            return source.split("@", 1)[0] or "unknown"
        if source.startswith("urn:"):
            return source.rsplit(":", 1)[-1] or "unknown"
        return source or "unknown"

    async def store_event(
        self,
        event_type: str,
        source: str,
        target: str | None,
        routing_key: str,
        timestamp: datetime,
        payload: dict[str, Any],
        event_id: str | None = None,
        legacy_id: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
        storage_latency_ms: float | None = None,
        **legacy_kwargs: Any,
    ) -> StoredEvent:
        """Store a single event in the database.

        Args:
            event_type: Event type/category
            source: Event source service
            target: Event target service (optional)
            routing_key: RabbitMQ routing key
            timestamp: Event timestamp
            payload: Full event payload
            event_id: Unique event ID (UUID, preferred)
            legacy_id: Legacy alias for event_id (backward-compat)
            session_id: Session ID for tracing (optional)
            correlation_id: Correlation ID for tracing (optional)
            storage_latency_ms: Time taken to store event (optional)
            **legacy_kwargs: Legacy keyword aliases, including id

        Returns:
            Created StoredEvent instance
        """
        legacy_id = legacy_id or legacy_kwargs.pop("id", None)
        if legacy_kwargs:
            unexpected = ", ".join(legacy_kwargs)
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")

        resolved_event_id = event_id or legacy_id
        if not resolved_event_id:
            raise ValueError("store_event requires event_id (or legacy id)")

        # Normalize timestamps to UTC + tz-aware to match TIMESTAMPTZ columns
        timestamp = self._normalize_timestamp(timestamp)

        service = self._derive_service(source)
        domain = self._derive_domain(event_type)
        raw_payload = {
            "id": resolved_event_id,
            "specversion": "1.0",
            "source": source,
            "type": event_type,
            "time": timestamp.isoformat(),
            "producer": service,
            "service": service,
            "domain": domain,
            "kind": "event",
            "data": payload,
        }

        async with self.session_factory() as session:
            stored_event = StoredEvent(
                id=resolved_event_id,
                specversion="1.0",
                ce_type=event_type,
                time=timestamp,
                correlationid=correlation_id,
                producer=service,
                service=service,
                domain=domain,
                kind="event",
                data=payload,
                raw=raw_payload,
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

    @staticmethod
    def _parse_event_time(value: Any) -> datetime:
        """Parse a CloudEvents time value into a timezone-aware datetime."""
        if isinstance(value, datetime):
            return Database._normalize_timestamp(value)
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            return Database._normalize_timestamp(datetime.fromisoformat(normalized))
        raise ValueError("CloudEvent requires a valid time value")

    async def insert_event(self, envelope: dict[str, Any]) -> bool:
        """Insert a Bloodbank v1 CloudEvent envelope idempotently.

        Returns True when a new row is inserted and False when the event ID is
        already present. The CloudEvents fields are stored verbatim alongside
        legacy columns used by the existing API.
        """
        event_id = envelope.get("id")
        event_type = envelope.get("type")
        source = envelope.get("source")
        event_time = self._parse_event_time(envelope.get("time"))
        producer = envelope.get("producer")
        service = envelope.get("service")
        domain = envelope.get("domain")
        kind = envelope.get("kind")

        required = {
            "id": event_id,
            "source": source,
            "type": event_type,
            "time": event_time,
            "producer": producer,
            "service": service,
            "domain": domain,
            "kind": kind,
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError(f"CloudEvent missing required field(s): {', '.join(missing)}")

        raw = dict(envelope)
        routing_key = str(envelope.get("ordering_key") or event_type)
        data = envelope.get("data")
        correlationid = envelope.get("correlationid")
        session_id = data.get("session_id") if isinstance(data, dict) else None

        stored_event = StoredEvent(
            id=str(event_id),
            specversion=str(envelope.get("specversion", "1.0")),
            ce_type=str(event_type),
            subject=envelope.get("subject"),
            time=event_time,
            datacontenttype=envelope.get("datacontenttype"),
            dataschema=envelope.get("dataschema"),
            correlationid=str(correlationid) if correlationid else None,
            causationid=str(envelope.get("causationid")) if envelope.get("causationid") else None,
            producer=str(producer),
            service=str(service),
            domain=str(domain),
            schemaref=envelope.get("schemaref"),
            traceparent=envelope.get("traceparent"),
            kind=str(kind),
            actor=envelope.get("actor"),
            data=data,
            ordering_key=envelope.get("ordering_key"),
            raw=raw,
            event_type=str(event_type),
            source=str(source),
            target=envelope.get("subject"),
            routing_key=routing_key,
            timestamp=event_time,
            payload=data if isinstance(data, dict) else {},
            session_id=str(session_id) if session_id else None,
            correlation_id=str(correlationid) if correlationid else None,
        )

        async with self.session_factory() as session:
            session.add(stored_event)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.get(StoredEvent, str(event_id))
                if existing is not None:
                    return False
                raise
            return True

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


def _default_database() -> Database:
    """Create a Database bound to current settings for module-level helpers."""
    return Database()


async def init_schema() -> None:
    """Initialize schema using the default configured database."""
    database = _default_database()
    try:
        await database.init_schema()
    finally:
        await database.close()


async def insert_event(envelope: dict[str, Any]) -> bool:
    """Insert a CloudEvent using the default configured database."""
    database = _default_database()
    try:
        return await database.insert_event(envelope)
    finally:
        await database.close()
