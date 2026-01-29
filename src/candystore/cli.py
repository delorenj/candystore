"""CLI for Candystore service."""

import asyncio

import typer
import uvicorn

from candystore.api import create_app
from candystore.config import settings
from candystore.consumer import EventConsumer
from candystore.database import Database
from candystore.logging_config import configure_logging, get_logger
from candystore.metrics import start_metrics_server

app = typer.Typer(help="Candystore - Event storage and query service")

configure_logging()
logger = get_logger(__name__)


@app.command()
def serve(
    host: str = typer.Option(settings.api_host, help="API host"),
    port: int = typer.Option(settings.api_port, help="API port"),
    reload: bool = typer.Option(False, help="Enable auto-reload (development)"),
) -> None:
    """Start the Candystore service (consumer + API).

    This starts both the event consumer and the REST API server.
    """
    logger.info(
        "candystore_starting",
        host=host,
        port=port,
        database=settings.database_url,
        rabbit_url=settings.rabbit_url,
    )

    # Initialize database
    database = Database()

    # Start metrics server
    start_metrics_server()

    # Create FastAPI app
    fastapi_app = create_app(database)

    # Start consumer in background
    async def start_consumer() -> None:
        await database.init_db()
        consumer = EventConsumer(database)
        await consumer.start()

    @fastapi_app.on_event("startup")
    async def startup_event() -> None:
        """Run on application startup."""
        logger.info("api_starting")
        await database.init_db()
        consumer = EventConsumer(database)
        # Start consumer as background task
        asyncio.create_task(consumer.start())

    @fastapi_app.on_event("shutdown")
    async def shutdown_event() -> None:
        """Run on application shutdown."""
        logger.info("api_stopping")
        await database.close()

    # Run uvicorn server
    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        reload=reload,
        log_config=None,  # Use our own logging
    )


@app.command()
def init_db() -> None:
    """Initialize the database (create tables)."""
    logger.info("initializing_database")

    database = Database()

    asyncio.run(database.init_db())

    logger.info("database_initialized")


@app.command()
def version() -> None:
    """Show version information."""
    from candystore import __version__

    typer.echo(f"Candystore v{__version__}")


if __name__ == "__main__":
    app()
