"""Prometheus metrics for Candystore."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from candystore.config import settings
from candystore.logging_config import get_logger

logger = get_logger(__name__)

# Event metrics
events_received_total = Counter(
    "candystore_events_received_total",
    "Total number of events received from Bloodbank",
    ["event_type", "source"],
)

events_stored_total = Counter(
    "candystore_events_stored_total",
    "Total number of events successfully stored",
    ["event_type"],
)

events_failed_total = Counter(
    "candystore_events_failed_total",
    "Total number of events that failed to store",
    ["event_type", "error_type"],
)

# Storage performance metrics
storage_latency_histogram = Histogram(
    "candystore_storage_latency_seconds",
    "Time taken to store an event in the database",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0],
)

storage_latency_ms = Gauge(
    "candystore_storage_latency_milliseconds",
    "Current storage latency in milliseconds",
)

# Database metrics
database_connections = Gauge(
    "candystore_database_connections",
    "Number of active database connections",
)

# API metrics
api_requests_total = Counter(
    "candystore_api_requests_total",
    "Total number of API requests",
    ["method", "endpoint", "status"],
)

api_request_duration_histogram = Histogram(
    "candystore_api_request_duration_seconds",
    "API request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
)

query_results_total = Counter(
    "candystore_query_results_total",
    "Total number of events returned by queries",
)

# System health metrics
consumer_connected = Gauge(
    "candystore_consumer_connected",
    "Whether the RabbitMQ consumer is connected (1=connected, 0=disconnected)",
)

consumer_reconnects_total = Counter(
    "candystore_consumer_reconnects_total",
    "Total number of consumer reconnection attempts",
)


def start_metrics_server() -> None:
    """Start Prometheus metrics HTTP server."""
    if settings.metrics_enabled:
        try:
            start_http_server(settings.metrics_port)
            logger.info(
                "metrics_server_started",
                port=settings.metrics_port,
            )
        except Exception as e:
            logger.error(
                "metrics_server_failed",
                error=str(e),
                port=settings.metrics_port,
            )
