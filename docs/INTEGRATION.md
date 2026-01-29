# Candystore Integration Guide

This guide explains how to integrate Candystore with other 33GOD components.

## Prerequisites

1. RabbitMQ running (via Bloodbank)
2. Candystore service running
3. Event publishers configured (WhisperLiveKit, etc.)

## Integration Points

### 1. Bloodbank Event Publishing

Candystore automatically receives all events published to Bloodbank through a wildcard subscription.

**No configuration needed on publisher side** - just publish to Bloodbank normally.

Example from WhisperLiveKit:
```python
from event_producers.events import EventEnvelope, envelope_for

# Create event
event = envelope_for(
    event_type="transcription.voice.completed",
    source="whisperlivekit",
    data={
        "text": "Hello world",
        "confidence": 0.95
    }
)

# Publish to Bloodbank
await publisher.publish(
    routing_key="transcription.voice.completed",
    body=event.model_dump(),
    message_id=event.id
)
```

Candystore will automatically:
- Receive the event
- Store it in the database
- Make it available via API

### 2. Candybar Real-Time Display

Candybar can query Candystore API to display events.

**WebSocket Alternative**: For real-time updates, Candybar should subscribe directly to Bloodbank (not Candystore). Use Candystore for historical queries.

Example query from Candybar:
```typescript
// Fetch recent events
const response = await fetch(
  'http://localhost:8683/events?limit=50&offset=0'
);
const { events, total, has_more } = await response.json();

// Filter by session
const response = await fetch(
  `http://localhost:8683/events?session_id=${sessionId}`
);
```

### 3. Tonny Agent Historical Queries

Tonny can query Candystore to retrieve conversation history.

```python
import httpx

async def get_session_history(session_id: str) -> list[dict]:
    """Get all events for a session."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8683/events",
            params={
                "session_id": session_id,
                "limit": 1000,
            }
        )
        data = response.json()
        return data["events"]
```

## Query Patterns

### By Session ID (Most Common)

Track all events in a conversation:
```bash
GET /events?session_id=abc-123
```

### By Event Type

Get specific event types:
```bash
GET /events?event_type=transcription.voice.completed
```

### By Time Range

Query events in a time window:
```bash
GET /events?start_time=2026-01-27T09:00:00Z&end_time=2026-01-27T10:00:00Z
```

### Combined Filters

Most powerful pattern:
```bash
GET /events?session_id=abc-123&event_type=transcription.voice.completed&limit=10
```

## Performance Considerations

### Indexing

Candystore creates indexes on:
- `event_type`
- `source`
- `session_id`
- `timestamp`
- `stored_at`

**Always use indexed fields in queries** for best performance.

### Pagination

Always paginate for large result sets:
```bash
# First page
GET /events?limit=100&offset=0

# Second page
GET /events?limit=100&offset=100
```

### Query Optimization

1. **Use specific filters**: Avoid querying all events
2. **Limit time ranges**: Use start_time/end_time
3. **Keep limit reasonable**: Max 1000, typical 50-100
4. **Use session_id when possible**: Most selective filter

## Error Handling

### API Errors

```typescript
try {
  const response = await fetch('http://localhost:8683/events');
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  const data = await response.json();
} catch (error) {
  console.error('Failed to fetch events:', error);
}
```

### Connection Failures

Candystore consumer automatically reconnects to RabbitMQ. Events published during disconnection are queued and processed when connection is restored.

## Monitoring

### Metrics

Monitor Candystore health via Prometheus:
```bash
# Check metrics
curl http://localhost:9090/metrics

# Key metrics to watch
candystore_events_received_total
candystore_events_stored_total
candystore_storage_latency_seconds
candystore_consumer_connected
```

### Logging

Check structured logs for debugging:
```bash
# JSON logs
tail -f logs/candystore.log | jq

# Console logs (if LOG_FORMAT=console)
tail -f logs/candystore.log
```

## Security

### API Access

For production, add authentication:
```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

@app.get("/events")
async def query_events(
    token: str = Depends(security),
    ...
):
    # Verify token
    if not verify_token(token):
        raise HTTPException(status_code=401)
    ...
```

### Database Access

Use read-only database credentials for API queries:
```bash
# Read-write for consumer
DATABASE_URL=postgresql+asyncpg://candystore_rw:password@localhost/candystore

# Read-only for API (optional optimization)
API_DATABASE_URL=postgresql+asyncpg://candystore_ro:password@localhost/candystore
```

## Testing Integration

### Integration Test Example

```python
import asyncio
import httpx
from event_producers.rabbit import Publisher
from event_producers.events import envelope_for

async def test_end_to_end():
    # Publish event to Bloodbank
    publisher = Publisher()
    await publisher.start()

    event = envelope_for(
        event_type="test.integration",
        source="test-service",
        data={"test": "data"}
    )

    await publisher.publish(
        routing_key="test.integration",
        body=event.model_dump(),
        message_id=event.id
    )

    # Wait for storage (async processing)
    await asyncio.sleep(1)

    # Query Candystore API
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8683/events",
            params={"event_type": "test.integration"}
        )
        data = response.json()

        # Verify event was stored
        assert data["total"] >= 1
        assert any(e["id"] == event.id for e in data["events"])

    await publisher.close()

if __name__ == "__main__":
    asyncio.run(test_end_to_end())
```

## Troubleshooting

### Events not appearing

1. Check Bloodbank connection:
   ```bash
   # Check RabbitMQ queue
   rabbitmqctl list_queues
   ```

2. Check Candystore logs:
   ```bash
   tail -f logs/candystore.log
   ```

3. Verify consumer is connected:
   ```bash
   curl http://localhost:9090/metrics | grep candystore_consumer_connected
   ```

### Slow queries

1. Add indexes for your query patterns
2. Use PostgreSQL instead of SQLite
3. Reduce query time ranges
4. Add pagination

### Storage latency high

1. Check database performance
2. Reduce PREFETCH_COUNT
3. Verify network latency to database
4. Consider database connection pooling

## Best Practices

1. **Use session IDs**: Always include session_id in events for traceability
2. **Query by session**: Most efficient query pattern
3. **Paginate**: Always use limit/offset for large result sets
4. **Index aware**: Filter by indexed fields (session_id, event_type, timestamp)
5. **Monitor metrics**: Watch storage latency and error rates
6. **PostgreSQL for prod**: Use PostgreSQL for production deployments
7. **Separate read/write**: Consider read replicas for high query load
