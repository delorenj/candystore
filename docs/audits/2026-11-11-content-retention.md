# CandyStore content quality and storage lifecycle audit

Author: `candystore-pm`  
Observed: 2026-11-11 evening EST / 2026-11-12 01:25–01:44 UTC  
Checkout: `main`, `1ec755a`; no pre-existing working-tree diff.  
Scope: read-only production investigation, local disposable restore experiment, and recommendations. No application code, board tickets, live jobs, deployments, restarts, pushes, uploads, or production rows changed.

## Decision

**Fix access to the request and its outcome first. Keep 60 days of complete events online; archive older events losslessly, not by discarding tools. Keep a small, durable, searchable narrative/provenance catalog beyond the hot window.**

Use the existing Bloodbank producers, CandyStore API, turn-enricher and Clairic integration. Do not buy a new analytics stack or run extraction over every telemetry row. Prefer ordinary compressed objects with an S3-compatible storage adapter initially; consider Parquet/Iceberg only when a demonstrated archive-analysis workload justifies them.

This is a recommendation backed by live reads and a local drill, **not an installed retention system**. No production deletion is safe on the strength of this drill alone.

Decision basis: Momo Pillar 2, **Dogfood the Platform** (reuse the existing pipeline and enrichment); Pillar 3, **Build LEGO, Not Statues**, and its Rule of Three (an adapter for object storage, not a speculative lakehouse); global `zero-dollar-defaults` (evaluate existing capacity and free tiers before new spend), `it-behaves-like-it-looks` (explicit coverage and source evidence), and `reversible-beats-cautious` (local proofs now; no irreversible cleanup without recovery proof).

## 1. Measured size and growth

Production metadata snapshot at 01:26:33 UTC:

| Relation | Table incl. TOAST bytes | Index bytes | Total bytes |
|---|---:|---:|---:|
| events | 20,478,181,376 | 7,940,571,136 | 28,418,752,512 |
| event_embeddings | 862,683,136 | 998,031,360 | 1,860,714,496 |
| dead_letter | 9,371,648 | 679,936 | 10,051,584 |

Whole database: **30,299,036,695 bytes = 30.30 GB / 28.22 GiB**. A later metadata snapshot at 01:40:28 measured 30,325,349,399 bytes: **26,312,704 bytes of physical growth in about 13m55s**. That short interval is burst evidence, not a daily forecast. PostgreSQL physical size has stepwise allocation, index and maintenance effects; event bytes per day are not measured filesystem growth per day.

One repeatable-read coverage snapshot contained **5,066,256 events**. A later type-count snapshot contained 5,066,846; the store continued ingesting during this investigation. Do not combine these as one atomic snapshot.

By arrival day, UTC:

| Window, complete days | Events/day average | Stored `raw` value bytes/day | Stored `data` value bytes/day |
|---|---:|---:|---:|
| Oct 13–Nov 11, 30 days | 138,627 | 241,006,274 | 170,285,456 |
| Nov 5–11, 7 days | 156,041 | 281,729,030 | 198,013,429 |

Last-seven-day total: **1,092,284 events**. Largest day in that 30-day window: **265,002 events on Oct 29**. `pg_column_size` sums describe stored column values, not independently allocated category relation sizes; they exclude heap/index overhead and must not be called logical JSON export sizes.

The root filesystem was **97% used**, with about **126–127 GiB available**. CandyStore matters, but its roughly 30 GB database is not the whole explanation for a multi-terabyte host's disk pressure. A second roughly 22 GB is retained local CandyStore database dumps, measured below.

### What actually dominates now

Later type-count snapshot, 5,066,846 events:

| Event family | Rows | Share | Stored raw GB | Stored data GB |
|---|---:|---:|---:|---:|
| agent.hook.updated | 1,689,809 | 33.35% | 2.888 | 2.117 |
| llm.usage.recorded | 778,893 | 15.37% | 0.994 | 0.654 |
| llm.allowance.observed | 718,815 | 14.19% | 0.665 | 0.344 |
| agent.tool.requested | 606,217 | 11.96% | 0.932 | 0.571 |
| agent.tool.completed | 598,872 | 11.82% | 1.744 | 1.399 |
| cron.candybar.invoked | 202,720 | 4.00% | 0.210 | 0.047 |
| cron.candybar.completed | 192,185 | 3.79% | 0.202 | 0.083 |
| system.hook.updated | 105,902 | 2.09% | 1.123 | 1.070 |

**The old “96% tools” explanation is no longer current. Tools are about 23.8% of rows.** Hook observations plus LLM usage/allowance telemetry are about 65% of rows. Some are intentionally content-free operational facts; blank prompt fields there are not failed prompt capture.

`events` stores the envelope in `raw`, and also projects `data` and `actor` into separate columns (`candystore/db.py:87–120`). That duplication buys query convenience but costs space. Changing this would be a schema/query migration, not a safe ad hoc deletion.

The largest index, `idx_events_data_gin`, occupies **3,800,670,208 bytes** and reports **zero scans** in available statistics. The search trigram index is 1,168,154,624 bytes. Evaluate query plans and migration ownership before removing the data GIN index; zero observed scans alone does not prove it is unnecessary. No index was dropped. A drop without changing its owning migration could simply be undone at application startup.

## 2. Prompt and provenance quality

A global ratio such as 14,716 prompt-bearing rows / 5,066,256 events is a poor quality metric: most events should not carry a prompt. The meaningful denominator is a prompt-start event or an actual tool-using session, not every row or every correlation ID.

### Prompt-bearing turn starts

Detailed snapshot:

| CLI | turn.started rows | With prompt, including measured nested fallback | Coverage |
|---|---:|---:|---:|
| Claude | 8,381 | 8,381 | 100% |
| Codex | 581 | 581 | 100% |
| Hermes | 4,558 | 4,214 | 92.5% |
| OpenCode | 1,545 | 1,545 | 100% |
| Kimi | 3 | 3 | 100%, tiny sample |
| Antigravity | 0 | 0 | No turn-start capture in this corpus |

**344 recorded Hermes turn starts lack a prompt in the checked fields.** The latest such sampled event is Oct 18. In every measured week beginning Oct 19, Hermes turn-start prompt coverage is 100%. Earlier Hermes prompts often exist at `data.payload.extra.user_message` while `data.prompt_text` is blank. Around 405 such nested-only historical records were measured in the initial snapshot; later service-specific reads likewise show that promotion into the top-level field was historically incomplete.

Do not turn this into a claim that all user messages are captured: hooks that never fired have no denominator in the event table, and promptless historical sessions remain.

### Date evidence

Selected UTC weeks, source rows counted by `received_at`:

| Week beginning | Claude start prompts/starts | Codex | Hermes | OpenCode |
|---|---:|---:|---:|---:|
| Oct 5 | 785/785 | 242/242 | 140/282 | 861/861 |
| Oct 12 | 641/641 | 34/34 | 339/502 | 318/318 |
| Oct 19 | 1,543/1,543 | 90/90 | 1,478/1,478 | 150/150 |
| Oct 26 | 1,762/1,762 | 1/1 | 693/693 | 19/19 |
| Nov 2 | 3,168/3,168 | 117/117 | 963/963 | 30/30 |
| Nov 9, partial week | 448/448 | 63/63 | 575/575 | 4/4 |

Complete weekly data is retained in `evidence/2026-11-11-content-retention/detail-audit.jsonl`; these are observed event counts, not a claim that every native user message fired a hook.

### Tool-session coverage, excluding hook-health correlations

Later read, grouping only tool and conversation families by CLI + correlation:

| CLI | Tool-using correlations | With a prompt | With reply text |
|---|---:|---:|---:|
| Hermes | 2,755 | 2,707 | 2,425 |
| OpenCode | 702 | 696 | 119 |
| Claude | 668 | 665 | 485 |
| Antigravity | 173 | 0 | 0 |
| Codex | 153 | 151 | 78 |

These are observed correlation groups, not guaranteed native-session counts. Missing narrative can also reflect the corpus boundary or an unfinished session. Grouping every correlation ID across all event types would count hook observation invocations as sessions and grossly misstate capture quality.

Antigravity is a genuine capture limitation: its hook SSOT explicitly says no native payload carries the user prompt and defines no `prompt_submit` binding (`bloodbank/services/agent-hooks/hooks.master.json:536–588`). A transcript adapter or other supported native source would be required. Do not synthesize a prompt from its tools.

**Exact tool-to-prompt linkage is missing:** all measured tool families carry `invocation_id`, but **zero** have top-level `turn_id`. Session correlation can recover nearby narrative, but does not prove which prompt caused a tool call amid concurrent or nested work. Source contracts should propagate stable native session, turn, parent invocation, initiating request event and command identity where actually available. Historical temporal joins must be marked inferred/ambiguous, never written as original evidence.

## 3. Trace: capture versus persistence versus discovery

### Producer and transport

- Claude adapter reads `payload.prompt` into `data.prompt_text` (`clients/claude.py:111–121`), and obtains final reply text from Stop payload or bounded transcript fallback (`123–135`).
- Hermes adapter promotes `extra.user_message` via its flattened payload and retains the original payload (`clients/hermes.py:121–151`).
- Gateway starts now carry `invocation.prompt` (`hermes-gateway/.../contract.py:474–497`). Gateway terminal events are lifecycle receipts and do **not** themselves carry reply text; separate hook events can carry it. Do not count every empty gateway completion as a lost assistant answer.
- Hook observability facts are intentionally payload-free (`hook-hub/facts.py:1–4`, `84–105`). Their correlations identify hook invocations, not conversation sessions.
- NATS `BLOODBANK_EVENTS` contained about seven days of events at the time of read. CandyStore's `candystore-events` consumer had **pending=0, ack_pending=0, redelivered=0**, and the same delivered and acknowledged stream sequence. That is current delivery evidence, not proof that no producer ever missed an event.
- The active app subscribes to `bloodbank.evt.>` through Dapr. The app is actually owned by `33god-platform/compose.yaml`; the checked-in standalone CandyStore compose is not the active deployment entrypoint on this host. The live summarizer SHA-256 matched the checkout (`c23988ea…a44e452`). No restart was used.

### Persistence and raw evidence

Ingestion validates, sanitizes unsupported NUL characters, persists, and deduplicates on event ID. NUL-sanitized originals are preserved as exact bytes in `dead_letter`. Successful `raw` events are JSONB: this preserves stored JSON values, not original whitespace/key ordering or an original wire-byte checksum.

Five representative live API reads matched the persisted `data` exactly:

| Evidence event | Finding |
|---|---|
| `5277b649-d349-45d5-80ca-ab7d0ad1a52e` | Historical nested Hermes prompt exists; live summary returns a prompt preview. |
| `3d858edf-2c1d-534b-b9e7-a0e11de8e9bc` | Historical gateway turn-start really has no prompt in stored data. |
| `7179974f-3362-4486-9139-93a637fa5bc8` | Current Claude prompt exists and is rendered in summary. |
| `8b9eaecc-a38b-518a-bf87-56d10f3de0ab` | Gateway completion receipt lacks reply; not sufficient by itself to conclude the answer was lost. |
| `e53cc024-b8cc-4081-a661-691b62fa1f58` | Claude reply exists and live summary returns a reply preview. |

A whole-corpus `raw.data = data` scan exceeded its 15-second timeout and was canceled. No corpus-wide equality claim is made. The local restore drill did compare every stored field of its bounded sample.

### Search is materially narrower than stored content

`migrations/003_search.sql` and `004_search_caps.sql` index:

- only top-level `prompt_text`, capped at 4,000 characters;
- tool arguments capped at 256;
- a 4,096-character **overall** concatenated haystack, including metadata before narrative;
- **no reply_text and no nested Hermes prompt fallback**.

Controlled live GET tests used exact event timestamp + type. Each target was returned without `q`; adding a word present in its original text but absent from `search_text` returned zero rows:

1. Nested historical prompt, 2,426 characters.
2. Current top-level prompt, 7,252 characters: a word beyond the searchable cap is invisible.
3. Stored assistant reply, 5,278 characters.

This is a proven discovery defect, not absent storage. Evidence hashes the selected words rather than copying private prompt content.

### UI and handoff

The current page is `web/src/pages/ProjectFeed.jsx`, not the older `EventList.jsx` path mentioned in some guidance. The reducer defaults to an empty lens, tools **on**, and a 24-hour browse window. Turning tools off only folds tool runs; it does not remove hook, allowance, or scheduler telemetry. The existing **Turns** lens works through `GET /events?lens=turns` and is the useful immediate browsing route. Event detail and session timeline already extract/render prompt/reply previews; the problem is not a missing renderer everywhere.

Session initial-prompt/final-reply enrichment is derived at read time. Preserve the distinction between raw and derived views: `/raw` should remain actual stored evidence. The CLI `context latest` is a bounded recent-session sample, not a complete turn ledger. Existing CANDYS-71 captures the high-level daily-turn experience requirement.

### Dead letters and history boundary

At the detailed read: **8,514 malformed records** and **19 NUL-sanitized originals**. A subsequent breakdown showed **8,483 malformed records of type `com.dapr.event.sent`**, mostly missing CandyStore's required producer/service/domain/kind. Other producers have small malformed counts. These do not explain millions of blank tool rows, but DROP/dead-letter visibility belongs in health checks. Do not loosen validation indiscriminately.

**Historical completeness is unresolved.** The active corpus's oldest `received_at` is Oct 3; its oldest event timestamp is Sep 25. A prior measured report documented an 885k-row September corpus. An Oct 1 monthly dump still exists, but its contents were not fully restored here. Preserve it and investigate the discontinuity; do not assert whether this was truncation, restore, migration, or intended reset without evidence.

## 4. Clairic: already present, useful in the right place

The speech spelling “Cleric” resolves in the existing tooling to **Cloudflare Clairic**, `@cf/cloudflare/clairic`, not the unrelated Cleric AI SRE service or Clerk auth.

Verified local owners:

- `~/.agents/tools/jev-clairic/` and its integration skill/pitfall reference.
- `bloodbank/services/turn-enricher/main.py`: runs Jev gating and Clairic over reply text; publishes `bloodbank.conversation.turn.annotated`, preserving source identity through `target_event_id` and causation.
- Live store: **6,738 annotations**, 6,729 with action, 6,631 with nonempty artifacts; the checked fields retain **no spans/confidence**.
- Running consumer log at 01:34:57: 7,508 received, 5,992 annotated, 582 noise, 927 skipped_no_reply, 7 skipped_paused, 4 transport-error counts. These are process counters, not a replacement for database history.
- Separate shared local call history recorded 2,004 Clairic calls, 2,003 successful, median logged latency 352 ms. This log does not include every turn-enricher call.

Cloudflare's current documentation describes typed text spans, lists, numbers, dates, durations, estimates and forecasts, confidence, `p_null`, and truncation reporting. Listed pricing is $0.10 per million input tokens, no generated output tokens. Actual billed input includes schema/multiple passes; do not budget solely from source-text length. Capability is documented and existing live use verified; **no new metered inference was issued for this audit**.

### Recommended enrichment contract

Do direct parsing first for fields already structured: event ID/type, native session and turn identity, producer, time, directory, tool, status. Use Clairic only for genuinely unstructured narrative, with per-family field specifications, not a template call for every telemetry event.

Candidate narrative fields: exact request excerpt, action excerpt, decision excerpt, artifact/ticket/path spans, explicit blocker and explicit error excerpt. Use text/list extraction only for factual audit annotations; estimates/forecasts must not fill missing provenance. Store null with a reason such as not_captured, unsupported_source, redacted or unavailable rather than inventing content.

An accepted annotation needs source event ID, source field/JSON path, input hash, extraction schema version, model identity, returned span(s), confidence/p_null, truncation flag and extraction timestamp. Verify spans equal substrings of the exact input. Never accept a `guess` when `value` is null. Preserve null/list semantics from the existing integration; confidence is not proof of truth. Gate failures degrade the derived view, not ingestion.

The current turn-enricher already has the right sidecar architecture, but discards most extraction evidence and uses a 6,000-character reply window. Extend it behind its existing boundary with measured fixtures and retry/replay coverage, rather than introducing another consumer. A budgeted pilot should score source-span validity, abstention on absent prompts, ticket/path extraction accuracy and cost; no “all fields filled” acceptance criterion.

## 5. Backups: local exists, offsite not proven

The active container's latest existing custom/zstd dump:

`~/backups/postgres/dbs/containers/candystore-postgres.candystore_20261111.dump`

Size: **2,712,791,494 bytes**. `pg_restore -l` succeeded and listed all six table-data entries including events, embeddings, dead letters and project reference data. TOC verification is **not a restore**. Ten retained container dumps total **22,375,294,594 bytes**; the retained Oct 1 monthly is 1,363,980,913 bytes. Same-named 16 KB dumps in the parent directory are a different host database, not proof of backup for this container.

`~/docker/scripts/postgres-nightly-dump.sh` produces local backups and retains recent dailies plus monthlies. Its observed clock is `/etc/cron.daily/db-backup`, invoking `/usr/local/bin/db-backup.sh`. No CandyStore database-dump clock/exception was established in the inspected Candybar registry/policy. This native application-backup clock needs owner classification and a Candybar migration or exact approved exception; **it was not disabled**.

The Candybar-owned `restic-dbdumps` offsite service explicitly excludes `candystore-postgres.candystore_*`. Its source explains a previous Google Drive shared-client quota failure. The latest service result is failure and the API reports **7 failed runs out of 7** in its 7-day window. Existing encrypted Google Drive storage is an option only after quota/auth/operation problems are repaired and upload/download/restore is proven. A current local backup is not host-loss recovery.

## 6. Local lossless archive/restore experiment

This is a bounded proof, not a production retention worker.

Input: page sample plus recent dialog and all sanitized events from a repeatable-read, read-only production snapshot; copied complete stored event records, related embeddings and selected dead letters. No source INSERT/UPDATE/DELETE/DDL.

| Result | Measured |
|---|---:|
| Event records | 16,632 |
| Related 384-dimensional embeddings | 1,684 |
| Dead-letter records, including all 19 sanitized originals | 69 |
| JSONL bytes | 158,361,521 |
| zstd level 3 bytes | 14,802,284 |
| gzip level 6 bytes | 20,180,275 |
| zstd reduction | 10.70×, about 90.65% |
| zstd compression time | 0.083 seconds on this host/sample |
| Insert, repeated import and verification interval | 8.711 seconds |
| Rows added on second import | 0 in all three tables |

Restored into an isolated PostgreSQL 17 instance with pgvector/pg_trgm; source is PostgreSQL 16.15. It listened only on a profile-scratch Unix socket, not TCP, and was stopped afterward. All stored fields compared equal, including raw/data, source and received timestamps, generated search text, vectors and bytea dead-letter evidence. Decompressed JSONL bytes equaled original; modifying an archive byte failed checksum/decompress validation.

Manifest identifiers:

- Canonical records SHA-256: `2d4517e180b2c1a37c26ecdb88c7d20cd1f55f3803dfb9b43c34f17b9accbd29`
- Compressed SHA-256: `10ea83a5c11cb581712dd905972580788afff1468f609365498d86e080a460ba`
- Source event-ID-set SHA-256: `463dddd00ade29f5abb5deb42291fdc4b54506e95c43b4cfa0d898ad3d36d5cb`

The first strict comparison failed; the successful rerun explicitly set UTC for timestamp rendering. An intermediate PostgreSQL startup also rejected a socket path exceeding 107 bytes; a shorter profile-scratch path resolved it. No failed attempt affected production.

Limits: no remote object upload/readback, encryption round trip, complete-corpus recovery, process-kill/resume test, same-ID/different-payload conflict test or purge was exercised. The tested import ignores matching duplicate IDs; a production importer must detect conflicting payloads and fail rather than silently accept them. JSONB round-trip preservation does not claim original publisher wire-byte preservation.

Private sample payloads remain in mode-restricted active-profile scratch, which is subject to cache pruning. They are **not** a durable or offsite backup. Only non-payload evidence is copied with this report.

Focused existing renderer tests: **22 passed, 1 skipped**, using `tests/test_summary.py tests/test_row_contract.py`; this is not the full database suite or independent adversarial review.

## 7. Recommended lifecycle

### Tier policy

1. **Hot:** 60 days of full events and required related evidence in PostgreSQL. Archive candidates must be at least 60 days old by arrival, not merely publisher timestamp; define a fixed UTC cutoff per batch and protect held, late or future-dated records. There are currently **zero candidates**, whether measured by event or received time. Arrival-based eligibility begins around Dec 2 for the oldest active data. Thus a 60-day policy is future control, not immediate disk relief.
2. **Narrative/provenance catalog:** keep prompts, replies, decisions, session/turn identities, coverage flags and archive lookup pointers searchable after full rows leave PostgreSQL. Preserve all request-bearing evidence, not just generated summaries. An archive-aware lookup must distinguish archived, missing and unsupported—not silently return an empty history. Keep the catalog itself bounded and budgeted; do not silently grow a second full event store.
3. **Archive:** immutable compressed complete records by closed arrival-day and bounded shards. Include normalized columns and `received_at`, not raw alone, until exact reconstruction is independently proven. Include embeddings or record an explicit, approved recomputation policy; the current FK cascades embedding deletion. Include dead letters, exact original bytes for sanitized events, project-resolution/version evidence and the schema needed to interpret historical data.
4. **Long-cold, optional later:** move rare-access old objects to colder storage only after access patterns justify the delay. Reduce detail of *derived* dashboards through daily/weekly/monthly aggregates, not probabilistic deletion of original tool facts. Backup snapshot retention (e.g. daily/weekly/monthly) is separate from canonical event retention.

### Archive-before-delete protocol

- Commit the exporter/importer, format version and Candybar manifest after independent spec and quality review. No production worker is installed by this report.
- Select a bounded immutable snapshot/ID set; order deterministically, stream memory-bounded shards, use stable batch IDs and content hashes. Protect active concurrent writes and late arrivals. Take operational locks only as long as needed; never hold a board lease while waiting.
- Manifest includes source instance/schema/version, selection cutoff, IDs/hash, row and category counts, min/max times, per-object compressed and uncompressed hashes, serialization/compression version, sanitized/dead-letter linkage, related-table counts and project catalog references.
- Encrypt privately with recoverable keys in 1Password, use least-privilege storage credentials, and keep the manifest/catalog off-host too. An S3 multipart ETag is not a SHA-256 content proof.
- Upload immutable objects with conditional creation; retry verifies existing bytes instead of overwriting. Finish and authenticate the manifest only after all referenced objects are readable. A partial upload is never eligible for purge.
- Read back from the actual destination, verify checksums, restore into an isolated database, and compare the complete selected IDs, fields and related evidence. Prove repeat execution, failure/restart recovery, same-ID conflict rejection, selective retrieval and key recovery. Test missing or damaged objects: purge must fail closed.
- Confirm source rows are still the archived versions and preserve legal/operational holds. Only then delete the exact verified ID set in bounded transactions. Track each object state separately: exported, uploaded, verified, restore-tested, purge-eligible, purged.
- PostgreSQL DELETE creates reusable space; it generally does **not** return relation files to the filesystem. Measure bloat/WAL and plan maintenance. Avoid an unbudgeted full table rewrite/VACUUM FULL at 97% disk utilization. Partitioning may become useful, but requires a carefully reviewed migration because current ID/FK constraints are not trivially compatible.
- Alert on archive lag, failure, checksum mismatch, object/copy health, disk headroom, oldest hot row and failed restore proof. Keep independent database backups; an event archive is not a complete replacement for database disaster recovery.

### Scheduling evidence

The actual required command was executed from `~/code/33GOD/candybar/`:

`uv run candybar doctor --repo . --live --json`

Result: **unknown**, not green. Repository check passed; machine audit could not classify `newapi-allowances.timer` because the canonical migration directory lock was held. Remote hosts, other users' private crontabs and n8n were outside that inventory. The discovered `/etc/cron.daily/db-backup` wrapper also needs explicit owner follow-up rather than assuming the parent anacron exemption covers application work.

API read-back showed 124 registered jobs, including the enabled hourly `candystore-project-sync`, but no verified CandyStore archive/purge schedule. No clock was created or changed; Candybar's independent watchdog and all unfamiliar timers were left untouched.

## 8. Storage comparison

Published rates checked during this audit; account usage, taxes, network throughput and contracts are not measured quotes. Example: **100 vendor-billed GB held for a month**, before free allowance and request costs.

| Option | Approx. storage | Retrieval and latency | Fit |
|---|---:|---|---|
| Existing DeLoDrive/MinIO | No new cloud storage bill | Immediate local reads | Its `/data` bind is `~/DeLoDrive`, on the same 97%-full filesystem. Staging only; not host-loss recovery. |
| Existing Google Drive/restic | Existing entitlement, marginal cost unverified | Quota-dependent network recovery | Existing adapter, but CandyStore explicitly excluded and latest offsite job failing. Repair/prove before relying on it. |
| Cloudflare R2 Standard | $1.50 / 100 GB-month | Ordinary direct GET; no retrieval or internet-egress fee | **Preferred initial external archive**, using a swappable S3 adapter. 10 GB/month Standard free allowance is account-shared and availability was not checked. |
| R2 Infrequent Access | $1.00 / 100 GB-month | Direct GET; $0.01/GB read, free egress; 30-day minimum | Saving is only $0.50/100 GB-month; unnecessary initial complexity. |
| Backblaze B2 | About $0.695 / 100 GB-month at current $6.95/TB price | Always hot; free egress up to 3× average stored data, then $0.01/GB; no minimum duration | Credible cheaper alternative if introducing an account is justified. Check current account-specific API terms. |
| S3 Glacier Deep Archive, us-east-1 reference | About $0.099 / 100 GB-month | Standard roughly 12h at $0.02/GB; bulk roughly 48h at $0.0025/GB; plus restore requests, temporary S3 copy and applicable transfer | For very old, rarely needed evidence, not interactive agent recall. 180-day minimum and per-object metadata/transition costs. |

At this scale, storage costs are small relative to engineering time. A single current 2.71 GB compressed database snapshot is only about four cents/month at R2 list storage rates before the shared allowance; retaining many full snapshots is a separate total. Do not project the page sample's 10.7× ratio onto the entire corpus: the actual existing complete dump is the stronger whole-database size observation, while incremental event-archive output remains unmeasured.

The AWS archival service is **Glacier**. **Apache Iceberg is an analytic table format**, not a cheap storage medium. A later Parquet/Iceberg layer can reference archived evidence if ad hoc analytics warrant it; it adds catalog/compaction/query-engine ownership and is not required to stop unbounded PostgreSQL growth.

## 9. Existing board mapping and delivery boundaries

Read all 70 visible CANDYS board items and detailed CANDYS-23, CANDYS-63 and CANDYS-71 before recommending new work. No ticket was created, claimed, moved, commented on or closed, and no board-driver lease was acquired because no selection/transition pass was run.

| Workstream | Existing ticket home / gap |
|---|---|
| Searchable prompts/replies, honest caps and source drilldown | CANDYS-70. Prefer a small narrative-search sidecar/index over a full hot-events generated-column rewrite. |
| Exact request/session/turn/subagent provenance and source coverage | CANDYS-63; remeasure CANDYS-64–66 rather than trust September counts. Antigravity needs a source capability solution. |
| High-level daily turns rather than telemetry browsing | CANDYS-71 requirements, reconciled with CANDYS-60 CLI. Do not mistake `context latest` for complete daily history. |
| Verified independent database recovery | CANDYS-23. Its old cron/systemd-timer acceptance text conflicts with current Candybar ownership and must be corrected by its owning workflow. |
| Archive/catalog/purge implementation | No dedicated retention ticket found. It is distinct from backup; create only in an authorized board-write pass after duplicate check. |
| Clairic evidence retention and replay | Extend existing Bloodbank turn-enricher ownership; do not duplicate the service inside CandyStore. |
| Native backup clock and offsite repair | Infra/DeLoContainers ownership; no cross-scope writes made here. |

Implementation priority: make dialog reliably findable and linked, establish actual off-host recovery, then enable lossless lifecycle. Capacity evaluation can proceed in parallel with source-contract investigation where files do not collide. A worker per ticket with independent spec and adversarial quality review remains the delivery path. This pass explicitly prohibited delegation; PM did not write application code as a workaround. No implementation or merge approval is claimed.

## Sources and retained evidence

- Repository references named above; existing live-feed plan read before tracing query/UI behavior.
- Local evidence directory: `docs/audits/evidence/2026-11-11-content-retention/` (aggregate data and non-payload verification only).
- Cloudflare Clairic docs: https://developers.cloudflare.com/ai/models/@cf/cloudflare/clairic/
- Clairic introduction/limitations: https://blog.cloudflare.com/clairic-extraction-model/
- R2 pricing: https://developers.cloudflare.com/r2/pricing/
- B2 pricing: https://www.backblaze.com/cloud-storage/pricing
- AWS retrieval latency: https://docs.aws.amazon.com/AmazonS3/latest/userguide/restoring-objects-retrieval-options.html
- AWS rate reference: https://aws.amazon.com/blogs/storage/analyze-access-patterns-and-use-the-most-cost-effective-amazon-s3-storage-class/
- Iceberg scope: https://iceberg.apache.org/

No private raw event samples or prompt/reply excerpts are included in the report's durable evidence bundle.
