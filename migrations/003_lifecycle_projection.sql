-- Durable, read-only Lifecycle projection sourced exclusively from canonical
-- Bloodbank publications. Candystore never writes operational Lifecycle state.
CREATE TABLE IF NOT EXISTS lifecycle_projection_receipts (
    event_id       UUID PRIMARY KEY REFERENCES events(id) ON DELETE RESTRICT,
    lifecycle_id   TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    state_version  BIGINT,
    projected_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_projection_receipts_lifecycle
    ON lifecycle_projection_receipts(lifecycle_id, projected_at DESC);

CREATE TABLE IF NOT EXISTS lifecycle_projections (
    lifecycle_id           TEXT PRIMARY KEY,
    repo                   TEXT NOT NULL,
    spec_version           BIGINT NOT NULL CHECK (spec_version >= 1),
    state_version          BIGINT NOT NULL CHECK (state_version >= 1),
    previous_state_version BIGINT,
    status                 TEXT NOT NULL,
    health                 TEXT NOT NULL,
    phase                  TEXT,
    progress_percent       NUMERIC NOT NULL,
    state_fingerprint      TEXT,
    legal_frontier         JSONB NOT NULL,
    obligations            JSONB NOT NULL,
    blockers               JSONB NOT NULL,
    gates                  JSONB NOT NULL,
    capabilities           JSONB NOT NULL,
    provenance             JSONB NOT NULL,
    freshness              JSONB NOT NULL,
    publication            JSONB NOT NULL,
    source_event_id        UUID NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    source_event_type      TEXT NOT NULL,
    source_event_time      TIMESTAMPTZ NOT NULL,
    source_ordering_key    TEXT NOT NULL,
    projected_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_projections_repo
    ON lifecycle_projections(repo);
CREATE INDEX IF NOT EXISTS idx_lifecycle_projections_state_version
    ON lifecycle_projections(state_version DESC);

CREATE TABLE IF NOT EXISTS lifecycle_command_verdicts (
    reply_event_id          UUID PRIMARY KEY REFERENCES events(id) ON DELETE RESTRICT,
    lifecycle_id           TEXT NOT NULL,
    repo                   TEXT NOT NULL,
    command_event_id        UUID NOT NULL,
    command_id              UUID NOT NULL,
    idempotency_key         TEXT NOT NULL,
    expected_state_version  BIGINT NOT NULL,
    observed_state_version  BIGINT NOT NULL,
    verdict                 TEXT NOT NULL,
    mutated                 BOOLEAN NOT NULL,
    resulting_state_version BIGINT,
    applied_event_id        UUID,
    capability_id           TEXT,
    reason_code             TEXT NOT NULL,
    correlation_id          UUID,
    causation_id            UUID,
    responded_at            TIMESTAMPTZ NOT NULL,
    source                  JSONB NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_command_verdicts_command_result
    ON lifecycle_command_verdicts(command_id, verdict, observed_state_version,
                                  COALESCE(resulting_state_version, 0));
CREATE INDEX IF NOT EXISTS idx_lifecycle_command_verdicts_lifecycle
    ON lifecycle_command_verdicts(lifecycle_id, responded_at DESC, reply_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_lifecycle_command_verdicts_idempotency
    ON lifecycle_command_verdicts(lifecycle_id, idempotency_key);
