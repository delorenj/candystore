"""Main entry point for Candystore service."""

import asyncio
import signal
import sys

from candystore.api import create_app
from candystore.config import settings
from candystore.consumer import EventConsumer
from candystore.database import Database
from candystore.logging_config import configure_logging, get_logger
from candystore.metrics import start_metrics_server

configure_logging()
logger = get_logger(__name__)


async def main() -> None:
    """Main entry point for running Candystore."""
    logger.info(
        "candystore_starting",
        database=settings.database_url,
        rabbit_url=settings.rabbit_url,
        api_host=settings.api_host,
        api_port=settings.api_port,
    )

    # Initialize database
    database = Database()
    await database.init_db()

    # Start metrics server
    start_metrics_server()

    # Create and start consumer
    consumer = EventConsumer(database)

    # Handle shutdown gracefully
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame: Any) -> None:  # noqa: ARG001
        logger.info("shutdown_signal_received", signal=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start consumer
    consumer_task = asyncio.create_task(consumer.start())

    logger.info("candystore_running")

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("candystore_shutting_down")
    await consumer.stop()
    await database.close()

    # Cancel consumer task
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    logger.info("candystore_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("candystore_interrupted")
        sys.exit(0)
