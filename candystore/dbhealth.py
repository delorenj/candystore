"""Index health: amcheck every btree index, and report collation drift.

    python -m candystore.dbhealth          # or: mise run db:check

`bt_index_check` verifies each btree's ordering against the collation the
server uses now -- the invariant a libc change breaks silently. Indexes on
tables under `--heap-limit` also get `heapallindexed`, proving every heap
tuple is findable; on `events` (41 GB) that would rescan the heap once per
index, so it is skipped there unless the limit is raised. Only AccessShareLock
is taken: ingest and readers are never blocked. Exit status 1 means at least
one index is corrupt or the database's collation version has drifted.

Why this exists: the cluster was initdb'd on postgres:16-alpine (musl, byte
order) and moved to pgvector/pgvector:pg16 (glibc 2.36) on 2026-10-06 without
a REINDEX. `project_dir_map_pkey` then stopped finding existing keys and the
map grew 63 duplicate primary keys (see db.collation_drift).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import psycopg2

from candystore import db

DEFAULT_HEAP_LIMIT_BYTES = 2 * 1024**3


def check(heap_limit_bytes: int = DEFAULT_HEAP_LIMIT_BYTES) -> dict:
    # A batch check, not a request: each index gets up to 10 minutes.
    conn = db._connect(statement_timeout_ms=600_000)
    conn.autocommit = True
    results = []
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS amcheck")
            cur.execute(
                "SELECT datcollversion, pg_database_collation_actual_version(oid) "
                "FROM pg_database WHERE datname = current_database()"
            )
            recorded, actual = cur.fetchone()
            cur.execute(
                """
                SELECT i.indexrelid::regclass::text, c.relname,
                       pg_relation_size(i.indexrelid), pg_table_size(c.oid),
                       EXISTS (SELECT 1 FROM unnest(i.indcollation) coll WHERE coll <> 0)
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_class ic ON ic.oid = i.indexrelid
                JOIN pg_am am ON am.oid = ic.relam
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE am.amname = 'btree' AND n.nspname = current_schema() AND i.indisvalid
                ORDER BY pg_relation_size(i.indexrelid)
                """
            )
            indexes = cur.fetchall()
            for name, table, index_bytes, table_bytes, collatable in indexes:
                heap = table_bytes < heap_limit_bytes
                started = time.monotonic()
                try:
                    cur.execute("SELECT bt_index_check(%s::regclass, %s)", (name, heap))
                    status, error = "ok", None
                except psycopg2.Error as exc:
                    code = exc.pgcode or ""
                    status = "corrupt" if code.startswith("XX") else f"error {code}"
                    error = (exc.pgerror or str(exc)).strip()
                results.append(
                    {
                        "index": name,
                        "table": table,
                        "collatable": collatable,
                        "index_bytes": index_bytes,
                        "heapallindexed": heap,
                        "status": status,
                        "error": error,
                        "seconds": round(time.monotonic() - started, 2),
                    }
                )
    finally:
        conn.close()
    bad = [row["index"] for row in results if row["status"] != "ok"]
    return {
        "collation": {"recorded": recorded, "actual": actual, "drift": recorded != actual},
        "checked": len(results),
        "unhealthy": bad,
        "indexes": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m candystore.dbhealth")
    parser.add_argument(
        "--heap-limit",
        type=int,
        default=DEFAULT_HEAP_LIMIT_BYTES,
        help="run heapallindexed on tables smaller than this many bytes",
    )
    args = parser.parse_args(argv)
    report = check(args.heap_limit)
    print(json.dumps(report, indent=2, default=str))
    return 1 if report["unhealthy"] or report["collation"]["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
