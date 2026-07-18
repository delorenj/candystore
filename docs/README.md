# Candystore Current Architecture

**Updated:** 2026-07-18

Candystore is Bloodbank's durable append-only event history and Lifecycle read
projection. It is not an operational Lifecycle writer.

## Current Lifecycle slice

The durable `candystore-events` JetStream/Dapr consumer accepts canonical
Bloodbank lifecycle events. In the same database transaction as event
persistence it applies replay-safe projection logic backed by migration
`003_lifecycle_projection.sql`.

The read model retains:

- lifecycle and project identity;
- `spec_version` and `state_version`;
- status, health, phase, and deterministic fingerprint when present;
- source observation, provenance, freshness, and as-of time;
- legal frontier, obligations, blockers, and gates; and
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
version ordering, provenance/freshness, explicit unknown/degraded behavior,
stable verdicts, and read-only ownership.
