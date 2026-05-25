-- CANPM-T2: events table for Bloodbank v1 CloudEvents.
-- The CloudEvents columns are the canonical ingest/query shape. Legacy columns
-- are retained so the existing FastAPI query surface remains backward-compatible.
CREATE TABLE IF NOT EXISTS events (
    id                  UUID PRIMARY KEY,
    specversion         TEXT NOT NULL DEFAULT '1.0',
    source              TEXT NOT NULL,
    type                TEXT NOT NULL,
    subject             TEXT,
    time                TIMESTAMPTZ NOT NULL,
    datacontenttype     TEXT,
    dataschema          TEXT,
    correlationid       UUID,
    causationid         UUID,
    producer            TEXT NOT NULL,
    service             TEXT NOT NULL,
    domain              TEXT NOT NULL,
    schemaref           TEXT,
    traceparent         TEXT,
    kind                TEXT NOT NULL,
    actor               JSONB,
    data                JSONB,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ordering_key        TEXT,
    raw                 JSONB NOT NULL,

    -- Backward-compatible fields used by the current API/tests.
    event_type          TEXT NOT NULL,
    target              TEXT,
    routing_key         TEXT NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    stored_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload             JSONB NOT NULL,
    session_id          UUID,
    correlation_id      UUID,
    storage_latency_ms  DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_domain ON events(domain);
CREATE INDEX IF NOT EXISTS idx_events_correlationid ON events(correlationid);
CREATE INDEX IF NOT EXISTS idx_events_producer ON events(producer);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(service);
CREATE INDEX IF NOT EXISTS idx_events_actor_cli ON events((actor->>'cli'));
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, time DESC);
CREATE INDEX IF NOT EXISTS idx_events_domain_time ON events(domain, time DESC);
CREATE INDEX IF NOT EXISTS idx_events_correlation_time ON events(correlationid, time);
CREATE INDEX IF NOT EXISTS idx_events_time_domain ON events(time, domain);
CREATE INDEX IF NOT EXISTS idx_events_time_actorcli ON events(time, (actor->>'cli'));
CREATE INDEX IF NOT EXISTS idx_events_data_gin ON events USING GIN (data jsonb_path_ops);

CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_event_type_timestamp ON events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_source_timestamp ON events(source, timestamp);
CREATE INDEX IF NOT EXISTS idx_session_timestamp ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_stored_at ON events(stored_at);
