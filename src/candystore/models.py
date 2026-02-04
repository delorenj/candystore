"""Database models for event storage."""

from datetime import datetime, timezone
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

    # Event metadata
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    routing_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Timestamps (timezone-aware; stored in UTC)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
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
