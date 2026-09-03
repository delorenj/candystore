-- Free-text search over the event trail (the /events `q` parameter).
--
-- Idempotent (IF NOT EXISTS / OR REPLACE) to match 001 and 002: init_schema()
-- replays every migration on each boot.

-- Trigram matching is what an audit trail actually needs. Its search terms are
-- file paths, tool names, branch names and command fragments -- `query.py`,
-- `33GOD/candystore`, `--no-verify`. A tsvector/FTS index word-stems and
-- tokenizes those into uselessness; trigrams match an arbitrary substring.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One denormalized, lowercased haystack per event.
--
-- GENERATED ... STORED rather than an expression index, for two reasons:
--   1. It cannot drift. An expression index would force the exact same
--      expression to be repeated in query.py, and a silent mismatch there does
--      not error -- it just stops using the index and full-scans 869k rows.
--   2. `data` is TOASTed (avg 2.7 KB, max 599 KB). An expression index has to
--      detoast every candidate row to recheck the match; a stored column is
--      read inline off the heap page.
--
-- Field order is load-bearing. The 512-char cap is a hard bound on index size
-- (~365 MB of text at the current 869k rows), and the free-text fields at the
-- bottom -- `arguments` averages 755 B, `payload` 3.6 KB -- would otherwise
-- consume the whole budget and truncate away the short identity and error
-- fields that carry the most search signal per byte. Cheap, high-signal fields
-- first; individually-capped prose last.
ALTER TABLE events ADD COLUMN IF NOT EXISTS search_text TEXT
GENERATED ALWAYS AS (
    left(
        lower(
            coalesce(type, '') || ' ' ||
            coalesce(subject, '') || ' ' ||
            coalesce(producer, '') || ' ' ||
            coalesce(service, '') || ' ' ||
            coalesce(domain, '') || ' ' ||
            coalesce(actor ->> 'cli', '') || ' ' ||
            coalesce(actor ->> 'provider', '') || ' ' ||
            coalesce(data ->> 'project', '') || ' ' ||
            coalesce(data ->> 'working_directory', '') || ' ' ||
            coalesce(data ->> 'git_branch', '') || ' ' ||
            coalesce(data ->> 'git_remote', '') || ' ' ||
            coalesce(data ->> 'repo', '') || ' ' ||
            coalesce(data ->> 'hook', '') || ' ' ||
            coalesce(data ->> 'tool_name', '') || ' ' ||
            coalesce(data ->> 'name', '') || ' ' ||
            coalesce(data ->> 'agent', '') || ' ' ||
            coalesce(data ->> 'agent_name', '') || ' ' ||
            coalesce(data ->> 'model', '') || ' ' ||
            coalesce(data ->> 'status', '') || ' ' ||
            coalesce(data ->> 'outcome', '') || ' ' ||
            coalesce(data ->> 'final_status', '') || ' ' ||
            coalesce(data ->> 'end_reason', '') || ' ' ||
            coalesce(data ->> 'stop_reason', '') || ' ' ||
            left(coalesce(data ->> 'error', ''), 200) || ' ' ||
            left(coalesce(data ->> 'error_message', ''), 200) || ' ' ||
            left(coalesce(data ->> 'prompt_text', ''), 256) || ' ' ||
            left(coalesce(data ->> 'input_preview', ''), 256) || ' ' ||
            left(coalesce(data ->> 'arguments', ''), 256) || ' ' ||
            left(coalesce(data ->> 'payload', ''), 256)
        ),
        512
    )
) STORED;

-- gin_trgm_ops answers ILIKE '%term%'. Note the index can only help a pattern
-- of 3+ characters -- shorter terms have no full trigram and degrade to a seq
-- scan, which is why query.py refuses to run one.
CREATE INDEX IF NOT EXISTS idx_events_search_trgm ON events USING GIN (search_text gin_trgm_ops);
