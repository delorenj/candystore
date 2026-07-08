# Pillars — Candystore

> Per-repo decision compass for Momo. Cited by **slug** in every Bloodbank decision event
> (`data.basis`). Universal pillars (how Momo works) live in the `momo` skill; these are
> what THIS project is for.
>
> STATUS: **starter draft.** The architectural/quality pillars below are derived from
> candystore's CLAUDE.md and are safe. The **monetary / strategic** section is a
> placeholder — Jarad, fill it so Momo can weigh business tradeoffs on your behalf.

## Architectural compass

- **`bloodbank-is-the-only-channel`** — Candystore only ever receives work via the Bloodbank
  event bus (Dapr subscription). Never add a direct service-to-service call or a second
  ingress. Everything is an event.
- **`never-lose-an-event`** — Persist before acknowledging. An event that reached candystore
  and was not durably stored is a defect, not an edge case.
- **`idempotent-and-poison-safe`** — Duplicate CloudEvent IDs are handled idempotently and
  return HTTP 200; malformed events return 200 with `{"status":"DROP"}`. Never return a
  non-2xx that makes Dapr redeliver forever. Retry storms are worse than a dropped bad event.
- **`schema-driven`** — Holyfields JSON Schema is the single source of truth for event
  contracts. Never hand-edit generated Pydantic/Zod. A schema change is a Holyfields change.
- **`migrations-for-every-schema-change`** — Candystore's Postgres schema only changes
  through a plain-SQL migration in `migrations/`. No implicit/auto schema drift.
- **`deliberate-stdlib-simplicity`** — Candystore is intentionally stdlib `http.server` +
  `psycopg2`, no FastAPI / SQLAlchemy / Alembic / RabbitMQ consumer. Resist framework creep;
  new dependencies must earn their place against this pillar.

## Quality / moral standards

- **`audit-trail-integrity`** — This service IS the durable audit trail. Correctness and
  completeness of the recorded history outrank feature velocity. Do not ship anything that
  could silently corrupt or gap the trail.

## Monetary / strategic  (FILL ME — Jarad)

- **`<slug>`** — <what makes/saves money or is not worth the spend on candystore? e.g. "the
  audit trail is infrastructure, not a product surface — keep it cheap, boring, and
  bulletproof; do not gold-plate the UI">
- **`<slug>`** — <strategic direction: what does a solid candystore unlock for 33GOD?>

---

*Momo weighs these when making a call and records the basis in a decision event. Product/scope
pillars here outrank universal process pillars, except the safety ones (no-code-mutation,
reviewer-independence, evidence-over-status, respect-the-contracts).*
