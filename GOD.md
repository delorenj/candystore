# Candystore - GOD Document

> **Guaranteed Organizational Document** - Developer-facing reference for Candystore
>
> **Last Updated**: 2026-02-02
> **Domain**: Infrastructure
> **Status**: Development

---

## Product Overview

Candystore is the **event persistence layer** for the 33GOD ecosystem. It serves as the "memory" of the system, capturing every event that flows through Bloodbank and making it available for audit trails, debugging, analytics, and event replay.

Think of Candystore as a high-performance event recorder that sits alongside Bloodbank, silently capturing the complete history of the system. While Bloodbank is ephemeral (events are consumed and forgotten), Candystore ensures nothing is ever lost.

**Key Capabilities:**
- **Universal Event Capture**: Subscribes to all events via wildcard binding (`#`) for complete system visibility
- **Sub-100ms Storage Latency**: Async database operations with SQLite or PostgreSQL backends
- **Historical Query API**: REST API with powerful filtering by session, event type, source, time range
- **Zero Event Loss Guarantee**: Durable queues, message acknowledgment, and transaction-safe storage
- **Prometheus Metrics**: Built-in observability for storage performance and health monitoring

---

## Architecture Position

```mermaid
graph TB
    subgraph "33GOD Event Pipeline"
        subgraph "Producers"
            WLK[WhisperLiveKit]
            IMI[iMi]
            TT[HeyMa]
            AGENTS[Agent Services]
        end

        subgraph "Infrastructure Domain"
            BB[Bloodbank<br/>RabbitMQ]
            HF[Holyfields<br/>Schema Registry]
        end

        subgraph "Persistence & Visualization"
            CS[Candystore<br/>Event Store]
            CB[Candybar<br/>Dashboard]
        end

        subgraph "Consumers"
            TONNY[Tonny<br/>AI Agent]
            HOLOCENE[Holocene<br/>Mission Control]
        end
    end

    WLK -->|transcription.voice.completed| BB
    IMI -->|worktree.created| BB
    TT -->|voice.command.received| BB
    AGENTS -->|agent.task.*| BB

    HF -->|validates schemas| BB

    BB -->|#<br/>all events| CS
    BB -->|#<br/>all events| CB

    CS -->|GET /events| TONNY
    CS -->|GET /events| HOLOCENE
    CS -->|GET /events| CB
```

**Role in Pipeline**: Candystore operates as a **passive observer** in the event pipeline. It subscribes to all events using a wildcard binding but does not participate in event routing or transformation. Its primary role is to persist events and provide query capabilities for downstream consumers that need historical data.

**Relationship with Bloodbank**: While Bloodbank is the real-time nervous system, Candystore is the long-term memory. Components that need real-time updates subscribe directly to Bloodbank; components that need historical context query Candystore.

---

## Event Contracts

### Bloodbank Events Emitted

| Event Name | Routing Key | Payload Schema | Trigger Condition |
|------------|-------------|----------------|-------------------|
| `candystore.event.persisted` | `candystore.event.persisted` | `EventPersistedPayload` | After successful storage of any event |

**EventPersistedPayload** (planned):
```json
{
  "original_event_id": "uuid",
  "original_event_type": "string",
  "original_routing_key": "string",
  "stored_at": "datetime",
  "storage_latency_ms": "float"
}
```

> **Note**: The `candystore.event.persisted` event is designed for observability and debugging. It is NOT persisted by Candystore itself to avoid infinite loops. This event enables downstream systems to monitor storage health and latency.

### Bloodbank Events Consumed

| Event Name | Routing Key | Handler | Purpose |
|------------|-------------|---------|---------|
| All Events | `#` (wildcard) | `EventConsumer._process_message()` | Persist all system events for audit trail |

**Consumption Strategy**:
- **Binding Key**: `#` - matches all routing keys
- **Queue**: `candystore.storage` - durable, auto-reconnect enabled
- **Acknowledgment**: Events acknowledged only after successful database commit
- **Prefetch**: Configurable (default: 100) for throughput optimization

### EventEnvelope Schema

Candystore expects all events to conform to the standard 33GOD EventEnvelope:

```python
class EventEnvelope(BaseModel):
    """Event envelope from Bloodbank."""
    id: str                        # UUID - unique event identifier
    ts: datetime                   # Event creation timestamp
    event_type: str                # e.g., 'transcription.voice.completed'
    source: str                    # Source service name
    data: dict[str, Any]           # Event-specific payload
    target: str | None = None      # Optional target service
    correlation_id: str | None = None  # For event chain tracing
    session_id: str | None = None  # Session identifier for grouping
```

**Validation**: Events that do not conform to this schema are logged as validation errors but do not block the consumer.

---

## Non-Event Interfaces

### CLI Interface

```bash
# Initialize database (create tables)
candystore init-db

# Start the service (consumer + API)
candystore serve

# Start with custom host/port
candystore serve --host 0.0.0.0 --port 8683

# Enable development auto-reload
candystore serve --reload

# Show version information
candystore version
```

**Commands:**

| Command | Arguments | Description |
|---------|-----------|-------------|
| `init-db` | None | Creates database tables using SQLAlchemy migrations |
| `serve` | `--host`, `--port`, `--reload` | Starts consumer + API server concurrently |
| `version` | None | Displays Candystore version |

### API Interface

**Base URL**: `http://localhost:8683`

**Endpoints:**

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| `GET` | `/health` | Health check with version and database type | `HealthResponse` |
| `GET` | `/events` | Query events with filters and pagination | `EventsResponse` |
| `GET` | `/events/{event_id}` | Get single event by UUID | `EventResponse` |

#### Health Check

```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "database": "sqlite+aiosqlite"
}
```

#### Query Events

```http
GET /events?session_id=<uuid>&event_type=<type>&source=<service>&target=<service>&start_time=<iso8601>&end_time=<iso8601>&limit=100&offset=0
```

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `session_id` | UUID | No | - | Filter by session identifier |
| `event_type` | string | No | - | Filter by event type (exact match) |
| `source` | string | No | - | Filter by source service |
| `target` | string | No | - | Filter by target service |
| `start_time` | ISO8601 | No | - | Events after this timestamp (inclusive) |
| `end_time` | ISO8601 | No | - | Events before this timestamp (inclusive) |
| `limit` | int | No | 100 | Max results (1-1000) |
| `offset` | int | No | 0 | Pagination offset |

**Response:**
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

#### Get Event by ID

```http
GET /events/{event_id}
```

**Response:** Single `EventResponse` object (same structure as in array above)

**Error Response (404):**
```json
{
  "detail": "Event {event_id} not found"
}
```

#### Example API Usage

```bash
# Get all events for a session
curl "http://localhost:8683/events?session_id=abc-123"

# Get transcription events from last hour
curl "http://localhost:8683/events?event_type=transcription.voice.completed&start_time=2026-01-27T09:00:00Z"

# Paginate through large result sets
curl "http://localhost:8683/events?limit=50&offset=100"

# Combine multiple filters
curl "http://localhost:8683/events?session_id=abc-123&source=whisperlivekit&limit=10"
```

---

## Technical Deep-Dive

### Technology Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI (async web framework)
- **Message Queue Client**: aio-pika (async RabbitMQ client)
- **ORM**: SQLAlchemy 2.0 (async mode)
- **Database**: SQLite (aiosqlite) or PostgreSQL (asyncpg)
- **Logging**: structlog (structured logging)
- **Metrics**: prometheus-client
- **CLI**: Typer
- **Serialization**: orjson (fast JSON parsing)

### Architecture Pattern

Candystore follows a **hexagonal architecture** with clear separation between:

1. **Consumer Layer** (`consumer.py`): Handles RabbitMQ connection, message processing, and event validation
2. **Storage Layer** (`database.py`): Database operations with async SQLAlchemy
3. **API Layer** (`api.py`): FastAPI REST endpoints for queries
4. **Configuration Layer** (`config.py`): Environment-based configuration via pydantic-settings

```
                    ┌─────────────────────┐
                    │     RabbitMQ        │
                    │    (Bloodbank)      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   EventConsumer     │
                    │    (consumer.py)    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │     Database        │
                    │   (database.py)     │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  SQLite/PostgreSQL  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    REST API         │
                    │    (api.py)         │
                    └─────────────────────┘
```

### Key Implementation Details

#### Consumer Resilience

The `EventConsumer` class implements robust connection handling:

```python
# Automatic reconnection with exponential backoff
reconnect_delay = 5  # seconds
max_reconnect_delay = 60  # seconds

# Connection failure triggers exponential backoff
reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
```

Key features:
- **Robust Connection**: Uses `aio_pika.connect_robust()` for automatic reconnection
- **Durable Queue**: Queue survives broker restarts
- **Message Acknowledgment**: Events acknowledged only after database commit
- **Prefetch Control**: Configurable QoS for throughput tuning

#### Database Connection Pooling

```python
self.engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,    # Verify connections before using
    pool_size=20,          # Connection pool size
    max_overflow=10,       # Max overflow connections
)
```

#### Latency Tracking

Every stored event includes `storage_latency_ms`:

```python
storage_start = time.perf_counter()
await self.database.store_event(...)
storage_latency_ms = (time.perf_counter() - storage_start) * 1000
```

### Data Models

#### StoredEvent (SQLAlchemy Model)

```python
class StoredEvent(Base):
    __tablename__ = "events"

    # Primary key
    id: str                           # UUID from EventEnvelope

    # Event metadata
    event_type: str                   # Event type/category
    source: str                       # Source service
    target: str | None                # Target service (optional)
    routing_key: str                  # RabbitMQ routing key

    # Timestamps
    timestamp: datetime               # Event creation time
    stored_at: datetime               # Storage time (for latency calc)

    # Payload
    payload: dict[str, Any]           # Full event data (JSON)

    # Tracing
    session_id: str | None            # Session identifier
    correlation_id: str | None        # Event chain tracing

    # Performance
    storage_latency_ms: float | None  # Storage latency metric
```

#### Database Indexes

Optimized for common query patterns:

| Index Name | Columns | Purpose |
|------------|---------|---------|
| `idx_event_type_timestamp` | `event_type`, `timestamp` | Filter by type, order by time |
| `idx_source_timestamp` | `source`, `timestamp` | Filter by source, order by time |
| `idx_session_timestamp` | `session_id`, `timestamp` | Session-based queries (most common) |
| `idx_stored_at` | `stored_at` | Admin/monitoring queries |

### Configuration

All configuration via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `RABBIT_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection URL |
| `EXCHANGE_NAME` | `bloodbank.events.v1` | RabbitMQ exchange name |
| `QUEUE_NAME` | `candystore.storage` | Consumer queue name |
| `DATABASE_URL` | `sqlite+aiosqlite:///./candystore.db` | Database connection URL |
| `API_HOST` | `0.0.0.0` | API server host |
| `API_PORT` | `8683` | API server port |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | `json` | Log format (`json` or `console`) |
| `METRICS_ENABLED` | `true` | Enable Prometheus metrics |
| `METRICS_PORT` | `9090` | Prometheus metrics port |
| `PREFETCH_COUNT` | `100` | RabbitMQ prefetch count |
| `BATCH_SIZE` | `50` | Database batch size (future) |

**PostgreSQL Configuration Example:**
```bash
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/candystore
```

---

## Observability

### Prometheus Metrics

Candystore exposes metrics on port 9090 (configurable):

**Event Metrics:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `candystore_events_received_total` | Counter | `event_type`, `source` | Total events received |
| `candystore_events_stored_total` | Counter | `event_type` | Successfully stored events |
| `candystore_events_failed_total` | Counter | `event_type`, `error_type` | Failed storage attempts |

**Performance Metrics:**

| Metric | Type | Description |
|--------|------|-------------|
| `candystore_storage_latency_seconds` | Histogram | Storage latency distribution |
| `candystore_storage_latency_milliseconds` | Gauge | Current storage latency |

**API Metrics:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `candystore_api_requests_total` | Counter | `method`, `endpoint`, `status` | Total API requests |
| `candystore_api_request_duration_seconds` | Histogram | `method`, `endpoint` | API latency distribution |
| `candystore_query_results_total` | Counter | - | Total events returned |

**System Metrics:**

| Metric | Type | Description |
|--------|------|-------------|
| `candystore_consumer_connected` | Gauge | Consumer connection status (1/0) |
| `candystore_consumer_reconnects_total` | Counter | Reconnection attempts |
| `candystore_database_connections` | Gauge | Active DB connections |

### Structured Logging

Candystore uses structlog for structured logging:

**JSON Format (Production):**
```json
{
  "timestamp": "2026-01-27T10:30:00.123Z",
  "level": "info",
  "logger": "candystore.consumer",
  "event": "event_stored",
  "event_id": "abc-123",
  "event_type": "transcription.voice.completed",
  "latency_ms": 5.2
}
```

**Console Format (Development):**
```
2026-01-27 10:30:00 [info     ] event_stored          event_id=abc-123 event_type=transcription.voice.completed latency_ms=5.2
```

### Key Log Events

| Event | Level | Description |
|-------|-------|-------------|
| `consumer_connecting` | INFO | Starting RabbitMQ connection |
| `consumer_connected` | INFO | Successfully connected to queue |
| `event_received` | DEBUG | Event received from queue |
| `event_stored` | INFO | Event successfully persisted |
| `event_validation_failed` | ERROR | Event failed schema validation |
| `event_storage_failed` | ERROR | Database storage failed |
| `query_executed` | INFO | API query completed |
| `consumer_connection_failed` | ERROR | RabbitMQ connection failed |

---

## Development

### Setup

```bash
# Navigate to Candystore directory
cd /home/delorenj/code/33GOD/candystore

# Install dependencies with uv
uv sync

# Copy environment configuration
cp .env.example .env

# Edit .env with your settings (defaults work for local dev)
```

### Running Locally

```bash
# Initialize database
uv run candystore init-db

# Start the service
uv run candystore serve

# Or with custom options
uv run candystore serve --host 0.0.0.0 --port 8683 --reload
```

**Prerequisites:**
1. RabbitMQ running (via Bloodbank): `docker-compose up -d rabbitmq`
2. Event publishers configured (optional for testing)

### Testing

```bash
# Run all tests
uv run pytest

# Run with coverage report
uv run pytest --cov=candystore --cov-report=html

# Run specific test file
uv run pytest tests/test_database.py

# Run with verbose output
uv run pytest -v

# Run async tests
uv run pytest tests/test_api.py -v
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

---

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

# Expose ports
EXPOSE 8683 9090

# Run service
CMD ["uv", "run", "candystore", "serve"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  candystore:
    build: ./candystore
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
      - candystore_data:/var/lib/postgresql/data

  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "5672:5672"
      - "15672:15672"

volumes:
  candystore_data:
```

### Production Recommendations

1. **Use PostgreSQL**: SQLite is for development only
2. **Enable TLS**: Configure RabbitMQ and PostgreSQL with TLS
3. **Set appropriate prefetch**: Tune `PREFETCH_COUNT` based on throughput needs
4. **Configure log rotation**: JSON logs can grow large
5. **Set up alerting**: Monitor `candystore_consumer_connected` and `candystore_events_failed_total`
6. **Database maintenance**: Implement event archival for long-term storage

---

## Performance Characteristics

### Benchmarks

| Metric | Target | Typical | Notes |
|--------|--------|---------|-------|
| Storage Latency | <100ms | 10-50ms | Per-event database insert |
| Query Latency | <200ms | 50-150ms | Filtered query with pagination |
| Throughput | 1000+ events/sec | Varies | Depends on database backend |
| Event Size | ~1KB avg | Varies | Depends on payload size |

### Optimization Tips

1. **Use specific filters**: Always filter by indexed fields (`session_id`, `event_type`, `source`, `timestamp`)
2. **Paginate**: Keep `limit` reasonable (50-100 typical, max 1000)
3. **Time-bound queries**: Use `start_time`/`end_time` for large datasets
4. **PostgreSQL for scale**: Switch from SQLite at ~100K events/day
5. **Connection pooling**: Adjust `pool_size` based on concurrent query load

### Bottlenecks

| Area | Symptom | Mitigation |
|------|---------|------------|
| Database writes | High `storage_latency_ms` | Use PostgreSQL, tune `pool_size` |
| RabbitMQ | Consumer disconnect | Increase prefetch, check network |
| Query performance | Slow API responses | Add indexes, paginate, filter |
| Memory | OOM errors | Reduce prefetch, batch size |

---

## Security Considerations

### Current State

- **API**: No authentication (intended for internal network only)
- **Database**: Uses connection string credentials
- **RabbitMQ**: Uses Bloodbank credentials

### Production Hardening

1. **API Authentication**: Add JWT or API key authentication
2. **Network Isolation**: Run on internal network only
3. **TLS**: Enable TLS for all connections
4. **Read-Only API User**: Use separate DB credentials for queries
5. **Rate Limiting**: Add rate limits to prevent DoS

```python
# Example: Adding API authentication
from fastapi.security import HTTPBearer

security = HTTPBearer()

@app.get("/events")
async def query_events(token: str = Depends(security), ...):
    if not verify_token(token):
        raise HTTPException(status_code=401)
    ...
```

---

## Integration Guide

### For Event Publishers

No changes needed. Candystore automatically receives all events published to Bloodbank.

### For Event Consumers

Query historical events via the REST API:

```python
import httpx

async def get_session_history(session_id: str) -> list[dict]:
    """Get all events for a session."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8683/events",
            params={"session_id": session_id, "limit": 1000}
        )
        return response.json()["events"]
```

### For Real-Time Display (Candybar)

- **Real-time**: Subscribe directly to Bloodbank
- **Historical**: Query Candystore API
- **Hybrid**: Initial load from Candystore, then Bloodbank for updates

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Events not appearing | Consumer disconnected | Check `candystore_consumer_connected` metric |
| Slow queries | Missing indexes or large dataset | Use filters, check indexes |
| Database locked (SQLite) | Concurrent writes | Switch to PostgreSQL |
| High latency | Database overload | Tune connection pool, use PostgreSQL |
| Validation errors | Malformed events | Check publisher event format |

### Diagnostic Commands

```bash
# Check consumer connection
curl http://localhost:9090/metrics | grep candystore_consumer_connected

# Check event counts
curl "http://localhost:8683/events?limit=1" | jq '.total'

# Check storage latency
curl http://localhost:9090/metrics | grep candystore_storage_latency

# View recent errors
tail -f logs/candystore.log | jq 'select(.level == "error")'
```

---

## References

- **Domain Doc**: `docs/domains/infrastructure/GOD.md`
- **System Doc**: `docs/GOD.md`
- **Source**: `candystore/src/candystore/`
- **Integration Guide**: `candystore/docs/INTEGRATION.md`
- **README**: `candystore/README.md`
