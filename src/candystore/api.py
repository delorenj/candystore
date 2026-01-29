"""REST API for querying stored events."""

import time
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from candystore.config import settings
from candystore.database import Database
from candystore.logging_config import get_logger
from candystore.metrics import api_request_duration_histogram, api_requests_total, query_results_total

logger = get_logger(__name__)


class EventResponse(BaseModel):
    """Response model for a single event."""

    id: str
    event_type: str
    source: str
    target: str | None
    routing_key: str
    timestamp: datetime
    stored_at: datetime
    payload: dict[str, Any]
    session_id: str | None
    correlation_id: str | None
    storage_latency_ms: float | None


class EventsResponse(BaseModel):
    """Response model for event query with pagination."""

    events: list[EventResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    database: str


def create_app(database: Database) -> FastAPI:
    """Create FastAPI application.

    Args:
        database: Database instance

    Returns:
        Configured FastAPI app
    """
    app = FastAPI(
        title="Candystore API",
        description="Event storage and query API for 33GOD ecosystem",
        version="0.1.0",
    )

    # Enable CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(
            status="healthy",
            version="0.1.0",
            database=settings.database_url.split("://")[0],
        )

    @app.get("/events", response_model=EventsResponse)
    async def query_events(
        session_id: str | None = Query(None, description="Filter by session ID"),
        event_type: str | None = Query(None, description="Filter by event type"),
        source: str | None = Query(None, description="Filter by source service"),
        target: str | None = Query(None, description="Filter by target service"),
        start_time: datetime | None = Query(None, description="Filter by start time (ISO8601)"),
        end_time: datetime | None = Query(None, description="Filter by end time (ISO8601)"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        offset: int = Query(0, ge=0, description="Offset for pagination"),
    ) -> EventsResponse:
        """Query stored events with filters and pagination.

        Args:
            session_id: Filter by session ID
            event_type: Filter by event type
            source: Filter by source service
            target: Filter by target service
            start_time: Filter by start timestamp
            end_time: Filter by end timestamp
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            EventsResponse with events and pagination info
        """
        start = time.perf_counter()

        try:
            # Query database
            events, total = await database.query_events(
                session_id=session_id,
                event_type=event_type,
                source=source,
                target=target,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                offset=offset,
            )

            # Convert to response models
            event_responses = [
                EventResponse(
                    id=event.id,
                    event_type=event.event_type,
                    source=event.source,
                    target=event.target,
                    routing_key=event.routing_key,
                    timestamp=event.timestamp,
                    stored_at=event.stored_at,
                    payload=event.payload,
                    session_id=event.session_id,
                    correlation_id=event.correlation_id,
                    storage_latency_ms=event.storage_latency_ms,
                )
                for event in events
            ]

            # Track metrics
            duration = time.perf_counter() - start
            api_request_duration_histogram.labels(method="GET", endpoint="/events").observe(duration)
            api_requests_total.labels(method="GET", endpoint="/events", status="200").inc()
            query_results_total.inc(len(event_responses))

            logger.info(
                "query_executed",
                total=total,
                returned=len(event_responses),
                limit=limit,
                offset=offset,
                duration_ms=round(duration * 1000, 2),
                filters={
                    "session_id": session_id,
                    "event_type": event_type,
                    "source": source,
                    "target": target,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )

            return EventsResponse(
                events=event_responses,
                total=total,
                limit=limit,
                offset=offset,
                has_more=(offset + limit) < total,
            )

        except Exception as e:
            api_requests_total.labels(method="GET", endpoint="/events", status="500").inc()
            logger.error(
                "query_failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")

    @app.get("/events/{event_id}", response_model=EventResponse)
    async def get_event_by_id(event_id: str) -> EventResponse:
        """Get a specific event by ID.

        Args:
            event_id: Event UUID

        Returns:
            EventResponse for the requested event
        """
        start = time.perf_counter()

        try:
            events, total = await database.query_events(limit=1, offset=0)

            # Find event by ID (simplified - in production would add direct ID lookup)
            # For now, we use a filter query
            from sqlalchemy import select

            from candystore.models import StoredEvent

            async with database.session_factory() as session:
                result = await session.execute(select(StoredEvent).where(StoredEvent.id == event_id))
                event = result.scalar_one_or_none()

                if not event:
                    api_requests_total.labels(method="GET", endpoint="/events/{id}", status="404").inc()
                    raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

                duration = time.perf_counter() - start
                api_request_duration_histogram.labels(method="GET", endpoint="/events/{id}").observe(
                    duration
                )
                api_requests_total.labels(method="GET", endpoint="/events/{id}", status="200").inc()

                logger.info(
                    "event_retrieved",
                    event_id=event_id,
                    duration_ms=round(duration * 1000, 2),
                )

                return EventResponse(
                    id=event.id,
                    event_type=event.event_type,
                    source=event.source,
                    target=event.target,
                    routing_key=event.routing_key,
                    timestamp=event.timestamp,
                    stored_at=event.stored_at,
                    payload=event.payload,
                    session_id=event.session_id,
                    correlation_id=event.correlation_id,
                    storage_latency_ms=event.storage_latency_ms,
                )

        except HTTPException:
            raise
        except Exception as e:
            api_requests_total.labels(method="GET", endpoint="/events/{id}", status="500").inc()
            logger.error(
                "event_retrieval_failed",
                event_id=event_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise HTTPException(status_code=500, detail=f"Event retrieval failed: {str(e)}")

    return app
