# Candystore

Event storage and audit trail service for the 33GOD ecosystem.

## Overview

Candystore is the event storage and query service that:
- Subscribes to **all events** from Bloodbank (RabbitMQ) with wildcard binding
- Stores events in a database (SQLite or PostgreSQL) with full payload and metadata
- Provides a REST API for querying stored events with filters and pagination
- Offers comprehensive audit trail and traceability for the entire system
- Achieves zero event loss with <100ms storage latency

## Features

- **Universal Event Storage**: Captures all events published to Bloodbank
- **Fast Storage**: <100ms latency per event with async database operations
- **Query API**: REST API with filtering by session_id, event_type, source, target, time range
- **Pagination**: Efficient pagination for large result sets
- **Structured Logging**: JSON or console logging with full context
- **Prometheus Metrics**: Built-in metrics for monitoring storage performance
- **Automatic Reconnection**: Resilient RabbitMQ connection with exponential backoff
- **Zero Event Loss**: Durable queues and transaction-safe storage

## Quick Start

### Installation

```bash
# Install dependencies
cd /home/delorenj/code/33GOD/candystore/trunk-main
uv sync

# Copy environment configuration
cp .env.example .env

# Edit .env with your RabbitMQ and database settings
# (defaults work for local development)
```

### Initialize Database

```bash
# Create database tables
uv run candystore init-db
```

### Run the Service

```bash
# Start both consumer and API server
uv run candystore serve

# Or run with custom settings
uv run candystore serve --host 0.0.0.0 --port 8683
```

The service will:
1. Connect to RabbitMQ and start consuming events
2. Start REST API server on configured port (default: 8683)
3. Start Prometheus metrics server (default: 9090)

## Configuration

Configure via environment variables or `.env` file:

```bash
# RabbitMQ (Bloodbank)
RABBIT_URL=amqp://guest:guest@localhost:5672/

# Database
DATABASE_URL=sqlite+aiosqlite:///./candystore.db
# For PostgreSQL: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/candystore

# API
API_HOST=0.0.0.0
API_PORT=8683

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json  # or 'console'

# Metrics
METRICS_ENABLED=true
METRICS_PORT=9090

# Consumer
PREFETCH_COUNT=100  # RabbitMQ prefetch
BATCH_SIZE=50       # Database batch size (future optimization)
```

## API Documentation

### Health Check

```bash
GET /health
```

Response:
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "database": "sqlite+aiosqlite"
}
```

### Query Events

```bash
GET /events?session_id=<uuid>&event_type=<type>&limit=100&offset=0
```

Query Parameters:
- `session_id` (optional): Filter by session ID
- `event_type` (optional): Filter by event type
- `source` (optional): Filter by source service
- `target` (optional): Filter by target service
- `start_time` (optional): Filter by start time (ISO8601)
- `end_time` (optional): Filter by end time (ISO8601)
- `limit` (optional, default=100, max=1000): Number of results
- `offset` (optional, default=0): Pagination offset

Response:
```json
{
  "events": [
    {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "event_type": "transcription.voice.completed",
      "source": "whisperlivekit",
      "target": "tonny",
      "routing_key": "transcription.voice.completed",
      "timestamp": "2026-01-27T10:30:00Z",
      "stored_at": "2026-01-27T10:30:00.123Z",
      "payload": {
        "text": "Hello world",
        "confidence": 0.95
      },
      "session_id": "session-123",
      "correlation_id": "corr-456",
      "storage_latency_ms": 5.2
    }
  ],
  "total": 142,
  "limit": 100,
  "offset": 0,
  "has_more": true
}
```

### Get Event by ID

```bash
GET /events/{event_id}
```

Response: Single event object (same structure as in array above)

### Example Queries

```bash
# Get all events for a session
curl "http://localhost:8683/events?session_id=abc-123"

# Get transcription events
curl "http://localhost:8683/events?event_type=transcription.voice.completed"

# Get events from WhisperLiveKit
curl "http://localhost:8683/events?source=whisperlivekit"

# Get events in last hour
curl "http://localhost:8683/events?start_time=2026-01-27T09:00:00Z&end_time=2026-01-27T10:00:00Z"

# Paginate through results
curl "http://localhost:8683/events?limit=50&offset=100"

# Combine filters
curl "http://localhost:8683/events?session_id=abc-123&event_type=transcription.voice.completed&limit=10"
```

## Architecture

### Component Diagram

```
┌─────────────────┐
│   Bloodbank     │
│   (RabbitMQ)    │
└────────┬────────┘
         │ (all events via wildcard "#")
         ▼
┌─────────────────┐
│  Event Consumer │
│  (aio-pika)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────┐
│    Database     │◄─────│  REST API    │
│ (SQLite/Postgres│      │  (FastAPI)   │
└─────────────────┘      └──────────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐      ┌──────────────┐
│  Audit Trail    │      │   Candybar   │
│   & Storage     │      │   (UI)       │
└─────────────────┘      └──────────────┘
```

### Database Schema

```sql
CREATE TABLE events (
    id VARCHAR(36) PRIMARY KEY,           -- UUID from EventEnvelope
    event_type VARCHAR(255) NOT NULL,     -- Event type/category
    source VARCHAR(255) NOT NULL,         -- Source service
    target VARCHAR(255),                  -- Target service (nullable)
    routing_key VARCHAR(255) NOT NULL,    -- RabbitMQ routing key
    timestamp TIMESTAMP NOT NULL,         -- Event timestamp
    stored_at TIMESTAMP NOT NULL,         -- Storage timestamp
    payload JSON NOT NULL,                -- Full event payload
    session_id VARCHAR(36),               -- Session ID for tracing
    correlation_id VARCHAR(36),           -- Correlation ID for tracing
    storage_latency_ms FLOAT,             -- Storage performance metric

    INDEX idx_event_type_timestamp (event_type, timestamp),
    INDEX idx_source_timestamp (source, timestamp),
    INDEX idx_session_timestamp (session_id, timestamp),
    INDEX idx_stored_at (stored_at)
);
```

## Metrics

Candystore exposes Prometheus metrics on port 9090 (configurable):

### Event Metrics
- `candystore_events_received_total{event_type, source}` - Total events received
- `candystore_events_stored_total{event_type}` - Total events successfully stored
- `candystore_events_failed_total{event_type, error_type}` - Total storage failures

### Performance Metrics
- `candystore_storage_latency_seconds` - Storage latency histogram
- `candystore_storage_latency_milliseconds` - Current storage latency gauge

### API Metrics
- `candystore_api_requests_total{method, endpoint, status}` - API request counts
- `candystore_api_request_duration_seconds{method, endpoint}` - API latency histogram
- `candystore_query_results_total` - Total events returned by queries

### System Metrics
- `candystore_consumer_connected` - Consumer connection status (1=connected, 0=disconnected)
- `candystore_consumer_reconnects_total` - Reconnection attempt count
- `candystore_database_connections` - Active database connections

## Development

### Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=candystore --cov-report=html

# Run specific test file
uv run pytest tests/test_database.py

# Run with verbose output
uv run pytest -v
```

### Code Quality

```bash
# Linting
uv run ruff check src/

# Type checking
uv run mypy src/

# Format code
uv run ruff format src/
```

## Integration with 33GOD Ecosystem

### Prerequisites

1. **Bloodbank** (RabbitMQ): Must be running with exchange configured
2. **HolyFields** (optional): Schema validation for event payloads

### Event Flow

1. **WhisperLiveKit** publishes transcription event to Bloodbank
2. **Candystore** receives event via wildcard subscription
3. Event stored in database with full metadata
4. **Candybar** queries Candystore API for real-time display
5. **Tonny** can query historical events via API

### Zero Event Loss Guarantee

Candystore ensures zero event loss through:
- **Durable Queue**: RabbitMQ queue survives broker restarts
- **Message Acknowledgment**: Events acknowledged only after successful storage
- **Automatic Reconnection**: Consumer reconnects with exponential backoff
- **Transaction Safety**: Database transactions ensure atomicity
- **Unique ID Tracking**: Event IDs prevent duplicates

## Performance

### Benchmarks

- **Storage Latency**: <100ms per event (target: 10-50ms typical)
- **Query Latency**: <200ms for filtered queries (target: 50-150ms typical)
- **Throughput**: 1000+ events/second sustained
- **Database Size**: ~1KB per event (varies by payload size)

### Optimization Tips

1. **Use PostgreSQL** for production (faster than SQLite at scale)
2. **Index strategy**: Existing indexes cover common query patterns
3. **Query optimization**: Use specific filters to reduce result sets
4. **Pagination**: Always paginate large queries (limit ≤ 1000)
5. **Time-based cleanup**: Archive old events periodically

## Troubleshooting

### Consumer not connecting

Check RabbitMQ connection:
```bash
# Verify RabbitMQ is running
curl http://localhost:15672/api/overview

# Check logs
tail -f logs/candystore.log
```

### Slow queries

1. Check database indexes are created
2. Use specific filters (avoid full table scans)
3. Monitor metrics endpoint for performance data
4. Consider PostgreSQL for production workloads

### Database locked errors (SQLite)

SQLite has limited write concurrency. For production:
1. Use PostgreSQL instead
2. Or reduce PREFETCH_COUNT to lower concurrent writes

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies
RUN uv sync --no-dev

# Run service
CMD ["uv", "run", "candystore", "serve"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  candystore:
    build: .
    ports:
      - "8683:8683"
      - "9090:9090"
    environment:
      RABBIT_URL: amqp://guest:guest@rabbitmq:5672/
      DATABASE_URL: postgresql+asyncpg://user:password@postgres:5432/candystore
      LOG_LEVEL: INFO
      LOG_FORMAT: json
    depends_on:
      - rabbitmq
      - postgres
    restart: unless-stopped

  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: candystore
    volumes:
      - postgres_data:/var/lib/postgresql/data

  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "5672:5672"
      - "15672:15672"

volumes:
  postgres_data:
```

## License

Part of the 33GOD ecosystem.

## Support

For issues and questions, see the main 33GOD project documentation.
