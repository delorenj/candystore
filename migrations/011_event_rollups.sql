-- Full-history aggregates of the trail, kept current as events arrive.
--
-- A read model that wants "every X in the trail, with its count" used to
-- recompute it by scanning `events`. project:sync's directory count did so
-- every hour: at 6.1M rows / 41 GB it is a parallel seq scan that reads ~2.8M
-- buffers (detoasting `data` for every row) in 11-26 s, so its cost grew with
-- history, not with what changed, and it outgrew the API's 5 s statement cap.
-- `events` has no orderable key to resume from (`id` is a producer UUID), so a
-- cursor/watermark cannot make that incremental. A counter maintained by the
-- write itself can: each rollup is a small table that statement-level
-- triggers adjust in the same transaction as the INSERT/DELETE/UPDATE that
-- changed the trail. Readers see exactly the counts a full scan at the same
-- snapshot would produce, and pay for a few thousand rows, not the history.
--
-- `event_rollups` is the registry: one row per rollup. `ready` is false until
-- the rollup has been seeded with the history that predates its triggers
-- (`python -m candystore.rollups seed`, which project:sync also runs on
-- demand). `generation` lets a seed detect a reset or TRUNCATE that raced it.
-- The seed is exact without blocking ingest; candystore/rollups.py explains
-- the arithmetic.
--
-- Purely additive and replay-safe: init_schema() runs every migration on every
-- boot inside one short transaction (candystore/db.py; no ledger, CANDYS-24).
-- CREATE TRIGGER takes SHARE ROW EXCLUSIVE on `events`, so the triggers are
-- created only when missing rather than replaced on every boot.

CREATE TABLE IF NOT EXISTS event_rollups (
    name        TEXT PRIMARY KEY,
    ready       BOOLEAN NOT NULL DEFAULT FALSE,
    generation  BIGINT NOT NULL DEFAULT 0,
    seeded_at   TIMESTAMPTZ,
    seed_ms     INTEGER,
    seed_keys   INTEGER,
    seed_events BIGINT
);

-- Rollup `work_dirs`: events per working directory. The key expression is
-- candystore.projects.WORK_DIR_EXPR, verbatim (tests/test_rollups.py asserts
-- it), so the map's keys, the query filters and these counts can never be
-- derived three different ways.
CREATE TABLE IF NOT EXISTS event_work_dirs (
    work_dir TEXT PRIMARY KEY,
    -- Signed on purpose: before the seed, a DELETE of pre-trigger history
    -- drives a key below zero, and the seed's delta brings it back. Readers
    -- only see `seen > 0`.
    seen     BIGINT NOT NULL DEFAULT 0
);

INSERT INTO event_rollups (name) VALUES ('work_dirs') ON CONFLICT (name) DO NOTHING;

-- Keys are applied in collation order (ORDER BY 1) by every writer, the seed
-- included, so two multi-key writers (a retention batch and a seed) lock rows
-- in the same order and cannot deadlock each other.
CREATE OR REPLACE FUNCTION event_work_dirs_on_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO event_work_dirs AS t (work_dir, seen)
    SELECT COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')) AS work_dir, COUNT(*)
    FROM new_rows
    WHERE COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')) IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    ON CONFLICT (work_dir) DO UPDATE SET seen = t.seen + EXCLUDED.seen;
    RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION event_work_dirs_on_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO event_work_dirs AS t (work_dir, seen)
    SELECT COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')) AS work_dir, -COUNT(*)
    FROM old_rows
    WHERE COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')) IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    ON CONFLICT (work_dir) DO UPDATE SET seen = t.seen + EXCLUDED.seen;
    RETURN NULL;
END
$$;

-- The trail is append-only in practice, but an UPDATE that rewrites `data`
-- must move its count from the old key to the new one, or the rollup drifts.
CREATE OR REPLACE FUNCTION event_work_dirs_on_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO event_work_dirs AS t (work_dir, seen)
    SELECT work_dir, SUM(delta)
    FROM (
        SELECT COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')) AS work_dir, 1 AS delta
        FROM new_rows
        UNION ALL
        SELECT COALESCE(NULLIF(data->>'working_directory', ''), NULLIF(data->'payload'->>'cwd', '')), -1
        FROM old_rows
    ) moved
    WHERE work_dir IS NOT NULL
    GROUP BY 1
    HAVING SUM(delta) <> 0
    ORDER BY 1
    ON CONFLICT (work_dir) DO UPDATE SET seen = t.seen + EXCLUDED.seen;
    RETURN NULL;
END
$$;

-- TRUNCATE leaves nothing to count, so the rollup is exactly empty and ready.
-- The generation bump invalidates any seed computed before the truncate.
CREATE OR REPLACE FUNCTION event_work_dirs_on_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM event_work_dirs;
    UPDATE event_rollups
       SET ready = TRUE, generation = generation + 1, seeded_at = NOW(),
           seed_ms = 0, seed_keys = 0, seed_events = 0
     WHERE name = 'work_dirs';
    RETURN NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'events'::regclass AND tgname = 'event_work_dirs_ins') THEN
        CREATE TRIGGER event_work_dirs_ins AFTER INSERT ON events
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION event_work_dirs_on_insert();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'events'::regclass AND tgname = 'event_work_dirs_del') THEN
        CREATE TRIGGER event_work_dirs_del AFTER DELETE ON events
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT EXECUTE FUNCTION event_work_dirs_on_delete();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'events'::regclass AND tgname = 'event_work_dirs_upd') THEN
        CREATE TRIGGER event_work_dirs_upd AFTER UPDATE ON events
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION event_work_dirs_on_update();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'events'::regclass AND tgname = 'event_work_dirs_trunc') THEN
        CREATE TRIGGER event_work_dirs_trunc AFTER TRUNCATE ON events
            FOR EACH STATEMENT EXECUTE FUNCTION event_work_dirs_on_truncate();
    END IF;
END
$$;
