"""Database models for event storage."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all database models."""

    pass


class StoredEvent(Base):
    """Stored event from Bloodbank.

    Stores all events with full payload and metadata for audit trail and querying.
    """

    __tablename__ = "events"

    # Primary key - UUID from EventEnvelope
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Bloodbank v1 CloudEvents fields
    specversion: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    ce_type: Mapped[str] = mapped_column("type", String(255), nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    datacontenttype: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataschema: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlationid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    causationid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    producer: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    schemaref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    traceparent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="event")
    actor: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    ordering_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Legacy API metadata retained for compatibility with existing query/API tests.
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    routing_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Timestamps (timezone-aware; stored in UTC)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    # Full event payload (as JSON)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Optional session tracking
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # Optional correlation ID for tracing
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # Storage metadata
    storage_latency_ms: Mapped[float | None] = mapped_column(nullable=True)

    # Indexes for common query patterns
    __table_args__ = (
        Index("idx_events_type_time", "type", "time"),
        Index("idx_events_domain_time", "domain", "time"),
        Index("idx_events_correlation_time", "correlationid", "time"),
        Index("idx_events_time_domain", "time", "domain"),
        Index("idx_event_type_timestamp", "event_type", "timestamp"),
        Index("idx_source_timestamp", "source", "timestamp"),
        Index("idx_session_timestamp", "session_id", "timestamp"),
        Index("idx_stored_at", "stored_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<StoredEvent(id={self.id}, "
            f"event_type={self.event_type}, "
            f"timestamp={self.timestamp})>"
        )
