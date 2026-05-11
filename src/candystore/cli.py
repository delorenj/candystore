"""Candystore CLI — Typer entry point for the FastAPI service.

`candystore serve` boots the Dapr subscriber + query API. No broker client
runs in this process; Dapr is the only ingress.
"""

import asyncio

import typer
import uvicorn

from candystore.api import create_app
from candystore.config import settings
from candystore.database import Database
from candystore.logging_config import configure_logging, get_logger
from candystore.metrics import start_metrics_server

app = typer.Typer(help="Candystore — Dapr subscriber + event store for 33GOD.")

configure_logging()
logger = get_logger(__name__)


@app.command()
def serve(
    host: str = typer.Option(settings.app_host, help="HTTP bind host"),
    port: int = typer.Option(settings.app_port, help="HTTP bind port (also Dapr --app-port)"),
    reload: bool = typer.Option(False, help="Enable auto-reload (development)"),
) -> None:
    """Boot the Candystore HTTP server (Dapr subscriber + query API)."""
    logger.info(
        "candystore_starting",
        host=host,
        port=port,
        database=settings.database_url,
        pubsub=settings.pubsub_name,
        topics=settings.topics,
    )

    database = Database()
    start_metrics_server()

    fastapi_app = create_app(database)

    @fastapi_app.on_event("startup")
    async def _startup() -> None:
        await database.init_db()
        logger.info("candystore_ready", port=port)

    @fastapi_app.on_event("shutdown")
    async def _shutdown() -> None:
        await database.close()
        logger.info("candystore_stopped")

    uvicorn.run(fastapi_app, host=host, port=port, reload=reload, log_config=None)


@app.command()
def init_db() -> None:
    """Create the database schema."""
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
