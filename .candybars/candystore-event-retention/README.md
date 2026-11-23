# candystore-event-retention

Daily Candybar job that keeps the CandyStore `events` table from growing without
bound. For every rule in `rules.txt` it runs `candystore prune`, which, per batch
of 2000 rows in one transaction: locks eligible rows, appends them (and any
embedding) to a gzip JSONL archive, fsyncs it, then deletes exactly those IDs.
Nothing is deleted that is not already durable in an archive.

- Archives: `~/backups/candystore/event-archive/` (`*.jsonl.gz` + `*.manifest.json`
  with counts, cutoff and SHA-256). ~440 bytes per tool event compressed.
- Restore: `uv run candystore restore <archive.jsonl.gz>` (idempotent, skips IDs
  that already exist; round-trip proven byte-identical on 2026-11-23, embeddings included).
- Disk: PostgreSQL DELETE makes space reusable inside the table; it does not
  shrink files. Pruning stops growth. Reclaiming disk needs `pg_repack` or a
  `VACUUM FULL` window (table rewrite, needs free space equal to the table).
- Archives live on the same disk as the database, which is not off-host
  recovery. See `docs/audits/2026-11-11-content-retention.md` for the off-host plan.

Rules (operator, 2026-11-23): `agent.tool.requested`, `agent.hook.updated` and
`llm.allowance.observed` at 30 days. The first manual run removed 303,462
tool.requested rows (archive 136 MB, SHA-256 in its manifest).
