"""Bloodbank event consumer for storing all events."""

import asyncio
import time
from datetime import datetime
from typing import Any

import aio_pika
import orjson
from pydantic import BaseModel, ValidationError

from candystore.config import settings
from candystore.database import Database
from candystore.logging_config import get_logger
from candystore.metrics import (
    consumer_connected,
    consumer_reconnects_total,
    events_failed_total,
    events_received_total,
    events_stored_total,
    storage_latency_histogram,
    storage_latency_ms,
)

logger = get_logger(__name__)


class EventEnvelope(BaseModel):
    """Event envelope from Bloodbank (matches event_producers.events.EventEnvelope)."""

    id: str
    ts: datetime
    event_type: str
    source: str
    data: dict[str, Any]
    target: str | None = None
    correlation_id: str | None = None
    session_id: str | None = None


class EventConsumer:
    """Consumer for Bloodbank events with storage in database."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.connection: aio_pika.abc.AbstractRobustConnection | None = None
        self.channel: aio_pika.abc.AbstractChannel | None = None
        self.queue: aio_pika.abc.AbstractQueue | None = None
        self.is_running = False
        self.reconnect_delay = 5  # seconds
        self.max_reconnect_delay = 60  # seconds

    async def start(self) -> None:
        """Start the consumer and connect to RabbitMQ."""
        self.is_running = True
        await self._connect()

    async def _connect(self) -> None:
        """Connect to RabbitMQ with automatic reconnection."""
        reconnect_delay = self.reconnect_delay

        while self.is_running:
            try:
                logger.info("consumer_connecting", rabbit_url=settings.rabbit_url)

                # Create robust connection (auto-reconnect)
                self.connection = await aio_pika.connect_robust(
                    settings.rabbit_url,
                    reconnect_interval=5,
                )

                self.channel = await self.connection.channel()

                # Set QoS (prefetch count)
                await self.channel.set_qos(prefetch_count=settings.prefetch_count)

                # Declare exchange (should already exist, but ensure it)
                exchange = await self.channel.declare_exchange(
                    settings.exchange_name,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )

                # Declare queue (durable, survives broker restart)
                self.queue = await self.channel.declare_queue(
                    settings.queue_name,
                    durable=True,
                    auto_delete=False,
                )

                # Bind to all events with wildcard
                await self.queue.bind(exchange, routing_key="#")

                logger.info(
                    "consumer_connected",
                    queue=settings.queue_name,
                    exchange=settings.exchange_name,
                    binding_key="#",
                )

                consumer_connected.set(1)
                reconnect_delay = self.reconnect_delay  # Reset delay on success

                # Start consuming
                await self._consume()

            except aio_pika.exceptions.AMQPConnectionError as e:
                consumer_connected.set(0)
                consumer_reconnects_total.inc()
                logger.error(
                    "consumer_connection_failed",
                    error=str(e),
                    reconnect_delay=reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.max_reconnect_delay)

            except Exception as e:
                consumer_connected.set(0)
                logger.error(
                    "consumer_unexpected_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(reconnect_delay)

    async def _consume(self) -> None:
        """Consume messages from the queue."""
        if not self.queue:
            logger.error("consumer_no_queue")
            return

        logger.info("consumer_started", queue=settings.queue_name)

        async with self.queue.iterator() as queue_iter:
            async for message in queue_iter:
                if not self.is_running:
                    break

                await self._process_message(message)

    async def _process_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Process a single message from RabbitMQ.

        Args:
            message: Incoming RabbitMQ message
        """
        start_time = time.perf_counter()
        routing_key = message.routing_key or "unknown"

        async with message.process():
            try:
                # Parse message body
                body = orjson.loads(message.body)

                # Validate against EventEnvelope schema
                envelope = EventEnvelope.model_validate(body)

                # Track event received
                events_received_total.labels(
                    event_type=envelope.event_type,
                    source=envelope.source,
                ).inc()

                logger.debug(
                    "event_received",
                    event_id=envelope.id,
                    event_type=envelope.event_type,
                    source=envelope.source,
                    routing_key=routing_key,
                )

                # Store event in database
                await self._store_event(envelope, routing_key)

                # Calculate and record storage latency
                latency_seconds = time.perf_counter() - start_time
                latency_ms = latency_seconds * 1000

                storage_latency_histogram.observe(latency_seconds)
                storage_latency_ms.set(latency_ms)

                # Track successful storage
                events_stored_total.labels(event_type=envelope.event_type).inc()

                logger.info(
                    "event_stored",
                    event_id=envelope.id,
                    event_type=envelope.event_type,
                    latency_ms=round(latency_ms, 2),
                )

            except ValidationError as e:
                events_failed_total.labels(
                    event_type="unknown",
                    error_type="validation_error",
                ).inc()
                logger.error(
                    "event_validation_failed",
                    error=str(e),
                    raw_body=message.body.decode("utf-8"),
                )

            except Exception as e:
                event_type = body.get("event_type", "unknown") if "body" in locals() else "unknown"
                events_failed_total.labels(
                    event_type=event_type,
                    error_type=type(e).__name__,
                ).inc()
                logger.error(
                    "event_storage_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    routing_key=routing_key,
                )

    async def _store_event(self, envelope: EventEnvelope, routing_key: str) -> None:
        """Store event in database.

        Args:
            envelope: Validated event envelope
            routing_key: RabbitMQ routing key
        """
        storage_start = time.perf_counter()

        await self.database.store_event(
            event_id=envelope.id,
            event_type=envelope.event_type,
            source=envelope.source,
            target=envelope.target,
            routing_key=routing_key,
            timestamp=envelope.ts,
            payload=envelope.data,
            session_id=envelope.session_id,
            correlation_id=envelope.correlation_id,
            storage_latency_ms=(time.perf_counter() - storage_start) * 1000,
        )

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        logger.info("consumer_stopping")
        self.is_running = False

        if self.channel and not self.channel.is_closed:
            await self.channel.close()

        if self.connection and not self.connection.is_closed:
            await self.connection.close()

        consumer_connected.set(0)
        logger.info("consumer_stopped")
