# Candystore Current Architecture

**Updated:** 2026-07-18

Candystore is Bloodbank's durable append-only event history and Lifecycle read
projection. It is not an operational Lifecycle writer.

## Current Lifecycle slice

The durable `candystore-events` JetStream/Dapr consumer accepts canonical
Bloodbank lifecycle events. Lifecycle snapshot v3 supplies stable obligation
occurrence identity and activation time in addition to versioned capabilities.
In the same database transaction as event
persistence it applies replay-safe projection logic backed by migration
`003_lifecycle_projection.sql`.

Every projection attempt reads the accepted envelope back from `events.raw`
under a PostgreSQL share lock. When an event ID is a duplicate, the existing
append-only row—not the incoming body—is the only projection input. This also
handles events that predate the projection migration: successful projection
adds its receipt atomically; a projection error commits no new receipt or read
model change, and durable redelivery retries the unchanged canonical row. For a
new event, audit insertion, receipt, and projection either commit together or
abort together.

Before claiming a receipt, Candystore validates every snapshot/reply candidate
with the exact Bloodbank JSON Schema tree selected by `BLOODBANK_SCHEMAS_DIR`
and then requires Lifecycle's canonical envelope source, producer, service,
actor, subject/type/kind/domain, schema version, and authority provenance. A
contract-invalid or spoofed candidate remains in append-only `events` history
as evidence but is excluded from projections and verdicts. This intentional
contract rejection is distinct from an operational database failure: the
latter still rolls the transaction back, while a pre-existing canonical audit
row remains retryable without accepting a conflicting duplicate body.
An unavailable, unreadable, corrupt, or unresolvable Bloodbank schema tree is
classified as an operational registry failure. It is never converted into an
audit-only payload rejection: the transaction rolls back, Dapr receives a
retryable 500 response, and readiness stays false. Payload/schema violations
from an available registry remain audit-only and receive no projection receipt.

The read model retains:

- lifecycle and project identity;
- `spec_version` and `state_version`;
- status, health, phase, and deterministic fingerprint when present;
- source observation, provenance, freshness, and as-of time;
- the immutable snapshot event ID and its correlation/causation lineage;
- legal frontier, obligations, blockers, and gates;
- authority-owned obligation occurrence IDs and activation times;
- authority-owned capability IDs and `capability_version`; and
- stable Lifecycle command verdicts.

Duplicate receipts are idempotent and older state versions cannot replace a
newer snapshot. Missing or stale observations are returned as
unknown/degraded, never as a healthy empty projection.

## Ownership boundary

- Lifecycle alone owns specification, operational state, reconcile, frontier,
  obligations, capabilities, version checks, and state-changing writes.
- Bloodbank owns canonical schemas and NATS/Dapr transport.
- Candystore owns append-only history and rebuildable query projections.
- Holocene reads the projection; it cannot mutate it into lifecycle truth.

The HTTP surface is read-only for Lifecycle. Unsupported mutation paths return
not found rather than forwarding or writing operational state.

## Validation

```bash
CANDYSTORE_TEST_DATABASE_URL=postgresql://... mise run test
mise run lint
DATABASE_URL=postgresql://... mise run test:schema
```

The focused suite covers migration repeatability, replay, idempotency,
version ordering, exact-schema/authority validation, snapshot and reply spoof
rejection, conflicting duplicate integrity, transaction rollback/retry,
provenance/causal metadata, explicit unknown/degraded behavior, stable
verdicts, and read-only ownership.
