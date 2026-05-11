"""FastAPI surface for Candystore.

Two surfaces share one app:

* **Dapr subscriber** — `GET /dapr/subscribe` advertises the subjects this
  service wants to receive; `POST <subscribe_route>` receives CloudEvents
  envelopes pushed by the local Dapr sidecar.
* **Query API** — `/events`, `/events/{id}`, `/health`, `/metrics`.

There is no broker client in this process; Dapr is the only ingress.
"""

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from candystore.config import settings
from candystore.database import Database
from candystore.logging_config import get_logger
from candystore.metrics import (
    api_request_duration_histogram,
    api_requests_total,
    events_failed_total,
    events_received_total,
    events_stored_total,
    query_results_total,
    storage_latency_histogram,
    storage_latency_ms,
)
from candystore.models import StoredEvent

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EventResponse(BaseModel):
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
    events: list[EventResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


class SubscriptionEntry(BaseModel):
    pubsubname: str
    topic: str
    route: str
    deadLetterTopic: str | None = None  # noqa: N815 — Dapr field casing


# ---------------------------------------------------------------------------
# CloudEvents helpers
# ---------------------------------------------------------------------------


def _parse_ce_time(raw: Any) -> datetime:
    """Parse the CloudEvents `time` field. Falls back to now() on garbage."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _extract_session_id(envelope: dict[str, Any]) -> str | None:
    """Pull a session id from common CloudEvents fields produced by the
    bloodbank-publisher hook script."""
    data = envelope.get("data") or {}
    for key in ("session_id", "sessionid", "sessionId"):
        if key in data and data[key]:
            return str(data[key])
        if key in envelope and envelope[key]:
            return str(envelope[key])
    subject = envelope.get("subject")
    if isinstance(subject, str) and subject.startswith("session:"):
        return subject.split(":", 1)[1] or None
    return None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(database: Database) -> FastAPI:
    """Create the FastAPI application wired to ``database``."""

    app = FastAPI(
        title="Candystore",
        description="Durable event storage + Dapr subscriber for the 33GOD platform.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----- Dapr subscribe advertisement ------------------------------------

    @app.get("/dapr/subscribe", response_model=list[SubscriptionEntry])
    async def dapr_subscribe() -> list[SubscriptionEntry]:
        """Return the list of subscriptions Dapr should set up for this app."""
        dlq = settings.dead_letter_topic or None
        return [
            SubscriptionEntry(
                pubsubname=settings.pubsub_name,
                topic=topic,
                route=settings.subscribe_route,
                deadLetterTopic=dlq,
            )
            for topic in settings.topics
        ]

    # ----- Dapr push endpoint ---------------------------------------------

    @app.post(settings.subscribe_route, status_code=status.HTTP_200_OK)
    async def receive_claude_event(request: Request) -> Response:
        """Receive a CloudEvents envelope from the Dapr sidecar and persist it.

        Dapr expects:
          * 2xx → ack
          * `{"status":"RETRY"}` → redeliver
          * `{"status":"DROP"}` → discard
        """
        start = time.perf_counter()
        try:
            envelope: dict[str, Any] = await request.json()
        except Exception as exc:  # malformed body
            events_failed_total.labels(event_type="unknown", error_type="json_decode").inc()
            logger.error("dapr_event_bad_json", error=str(exc))
            return Response(content='{"status":"DROP"}', media_type="application/json")

        event_type = str(envelope.get("type") or "unknown")
        source = str(envelope.get("source") or "unknown")
        events_received_total.labels(event_type=event_type, source=source).inc()

        # CloudEvents `id` is required; if absent, drop.
        event_id = envelope.get("id")
        if not event_id:
            events_failed_total.labels(event_type=event_type, error_type="missing_id").inc()
            logger.error("dapr_event_missing_id", envelope=envelope)
            return Response(content='{"status":"DROP"}', media_type="application/json")

        timestamp = _parse_ce_time(envelope.get("time"))
        session_id = _extract_session_id(envelope)
        correlation_id = (
            envelope.get("correlationid")
            or envelope.get("correlation_id")
            or envelope.get("traceid")
        )

        try:
            await database.store_event(
                event_id=str(event_id),
                event_type=event_type,
                source=source,
                target=None,
                routing_key=event_type,
                timestamp=timestamp,
                payload=envelope,
                session_id=session_id,
                correlation_id=str(correlation_id) if correlation_id else None,
                storage_latency_ms=None,
            )
        except Exception as exc:
            events_failed_total.labels(event_type=event_type, error_type=type(exc).__name__).inc()
            logger.error(
                "dapr_event_store_failed",
                event_id=event_id,
                event_type=event_type,
                error=str(exc),
            )
            # RETRY: Dapr will redeliver. Idempotency is enforced by the
            # primary-key constraint on StoredEvent.id (duplicate inserts
            # raise IntegrityError and are caught here on retry).
            return Response(content='{"status":"RETRY"}', media_type="application/json")

        latency_s = time.perf_counter() - start
        storage_latency_histogram.observe(latency_s)
        storage_latency_ms.set(latency_s * 1000)
        events_stored_total.labels(event_type=event_type).inc()
        logger.info(
            "claude_event_stored",
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            latency_ms=round(latency_s * 1000, 2),
        )
        return Response(status_code=status.HTTP_200_OK)

    # ----- Health ----------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            version="0.1.0",
            database=settings.database_url.split("://")[0],
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ----- Query API ------------------------------------------------------

    @app.get("/events", response_model=EventsResponse)
    async def query_events(
        session_id: str | None = Query(None),
        event_type: str | None = Query(None),
        source: str | None = Query(None),
        target: str | None = Query(None),
        start_time: datetime | None = Query(None),
        end_time: datetime | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> EventsResponse:
        start = time.perf_counter()
        try:
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
            event_responses = [
                EventResponse(
                    id=e.id,
                    event_type=e.event_type,
                    source=e.source,
                    target=e.target,
                    routing_key=e.routing_key,
                    timestamp=e.timestamp,
                    stored_at=e.stored_at,
                    payload=e.payload,
                    session_id=e.session_id,
                    correlation_id=e.correlation_id,
                    storage_latency_ms=e.storage_latency_ms,
                )
                for e in events
            ]
            duration = time.perf_counter() - start
            api_request_duration_histogram.labels(method="GET", endpoint="/events").observe(duration)
            api_requests_total.labels(method="GET", endpoint="/events", status="200").inc()
            query_results_total.inc(len(event_responses))
            return EventsResponse(
                events=event_responses,
                total=total,
                limit=limit,
                offset=offset,
                has_more=(offset + limit) < total,
            )
        except Exception as exc:
            api_requests_total.labels(method="GET", endpoint="/events", status="500").inc()
            logger.error("query_failed", error=str(exc), error_type=type(exc).__name__)
            raise HTTPException(status_code=500, detail=f"Query failed: {exc}") from exc

    @app.get("/events/{event_id}", response_model=EventResponse)
    async def get_event_by_id(event_id: str) -> EventResponse:
        async with database.session_factory() as session:
            result = await session.execute(select(StoredEvent).where(StoredEvent.id == event_id))
            event = result.scalar_one_or_none()
            if not event:
                raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
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

    return app
