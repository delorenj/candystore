-- CANPM-T2: metadata-aligned events table for Bloodbank v1 CloudEvents.
-- Runtime init_schema/init_db uses SQLAlchemy create_all; keep this raw migration
-- intentionally aligned with candystore.models.StoredEvent metadata to avoid drift.
CREATE TABLE IF NOT EXISTS events (
    id                  VARCHAR(36) NOT NULL,
    specversion         VARCHAR(16) NOT NULL,
    type                VARCHAR(255) NOT NULL,
    subject             VARCHAR(255),
    time                TIMESTAMP WITH TIME ZONE NOT NULL,
    datacontenttype     VARCHAR(255),
    dataschema          VARCHAR(255),
    correlationid       VARCHAR(36),
    causationid         VARCHAR(36),
    producer            VARCHAR(255) NOT NULL,
    service             VARCHAR(255) NOT NULL,
    domain              VARCHAR(255) NOT NULL,
    schemaref           VARCHAR(255),
    traceparent         VARCHAR(255),
    kind                VARCHAR(64) NOT NULL,
    actor               JSON,
    data                JSON,
    received_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    ordering_key        VARCHAR(255),
    raw                 JSON NOT NULL,

    -- Backward-compatible fields used by the current API/tests.
    event_type          VARCHAR(255) NOT NULL,
    source              VARCHAR(255) NOT NULL,
    target              VARCHAR(255),
    routing_key         VARCHAR(255) NOT NULL,
    timestamp           TIMESTAMP WITH TIME ZONE NOT NULL,
    stored_at           TIMESTAMP WITH TIME ZONE NOT NULL,
    payload             JSON NOT NULL,
    session_id          VARCHAR(36),
    correlation_id      VARCHAR(36),
    storage_latency_ms  FLOAT,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_event_type_timestamp ON events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_correlation_time ON events(correlationid, time);
CREATE INDEX IF NOT EXISTS idx_events_domain_time ON events(domain, time);
CREATE INDEX IF NOT EXISTS idx_events_time_domain ON events(time, domain);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, time);
CREATE INDEX IF NOT EXISTS idx_session_timestamp ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_source_timestamp ON events(source, timestamp);
CREATE INDEX IF NOT EXISTS idx_stored_at ON events(stored_at);
CREATE INDEX IF NOT EXISTS ix_events_correlation_id ON events(correlation_id);
CREATE INDEX IF NOT EXISTS ix_events_correlationid ON events(correlationid);
CREATE INDEX IF NOT EXISTS ix_events_domain ON events(domain);
CREATE INDEX IF NOT EXISTS ix_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS ix_events_producer ON events(producer);
CREATE INDEX IF NOT EXISTS ix_events_received_at ON events(received_at);
CREATE INDEX IF NOT EXISTS ix_events_service ON events(service);
CREATE INDEX IF NOT EXISTS ix_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS ix_events_source ON events(source);
CREATE INDEX IF NOT EXISTS ix_events_stored_at ON events(stored_at);
CREATE INDEX IF NOT EXISTS ix_events_target ON events(target);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(time);
CREATE INDEX IF NOT EXISTS ix_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type);
