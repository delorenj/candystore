"""Full-history event rollups: counts kept current at write time.

A read model that needs "every X in the trail, with its count" must not scan
`events` to get it. The trail is 6.1M rows / 41 GB and grows every second, so a
scan's cost tracks history rather than change, and `events` has no orderable
key to resume from (`id` is a producer-supplied UUID). A rollup is a small
table that statement-level triggers on `events` adjust in the same transaction
as each INSERT, DELETE, UPDATE or TRUNCATE (migrations/011_event_rollups.sql).
Reading one costs its key count, a few thousand rows.

Seeding, exact and online
-------------------------
The triggers only see writes made after they exist. The history before them is
added once by `seed()`, without locking out ingest:

1. In one REPEATABLE READ snapshot S, read the full-scan count F(k) and the
   rollup's current value L(k). Trigger writes commit atomically with the event
   rows they describe, so within S the rollup holds exactly the trigger-counted
   share of what F counts (or, for a key whose pre-trigger rows were deleted,
   minus those rows).
2. In a second, READ COMMITTED transaction, add D(k) = F(k) - L(k) to every
   key with an additive upsert and mark the rollup ready.

Every change after S reaches the rollup as a trigger delta, so the final value
is L(S) + D + (deltas after S) = F(S) + (deltas after S): the true count. The
upsert is additive, so it commutes with concurrent trigger upserts. The second
step re-checks `ready` and `generation` under FOR UPDATE, so a concurrent seed,
a reset or a TRUNCATE between the two steps aborts the apply instead of double
counting.

`verify()` proves it: one snapshot, full scan versus rollup, every key.

Adding a rollup
---------------
Write its table and four triggers in a migration (011 is the template), insert
its `event_rollups` row, and add a `Rollup` to `ROLLUPS` with the identical key
expression. tests/test_rollups.py checks the expression appears in the
migration verbatim and that seed/verify agree with a full scan.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg2.extensions

from candystore import db
from candystore.projects import WORK_DIR_EXPR

logger = logging.getLogger("candystore.rollups")

# A seed or verify reads the whole trail once (11-26 s at 6.1M rows). It is a
# named batch operation, not a request, so it carries its own budget rather
# than the API's 5 s interactive cap. A tighter DSN/PGOPTIONS cap still wins
# (db._connect).
SEED_STATEMENT_TIMEOUT_ENV = "CANDYSTORE_SEED_STATEMENT_TIMEOUT_MS"
DEFAULT_SEED_STATEMENT_TIMEOUT_MS = 240_000

TRIGGER_SUFFIXES = ("ins", "del", "upd", "trunc")


@dataclass(frozen=True)
class Rollup:
    name: str
    table: str
    key_column: str
    key_expr: str

    @property
    def triggers(self) -> tuple[str, ...]:
        return tuple(f"{self.table}_{suffix}" for suffix in TRIGGER_SUFFIXES)

    def full_scan_sql(self) -> str:
        """The query the rollup replaces. Kept for seed and verify only."""
        return (
            f"SELECT {self.key_expr} AS key, COUNT(*) FROM events "
            f"WHERE {self.key_expr} IS NOT NULL GROUP BY 1"
        )


WORK_DIRS = Rollup(
    name="work_dirs",
    table="event_work_dirs",
    key_column="work_dir",
    key_expr=WORK_DIR_EXPR,
)

ROLLUPS: dict[str, Rollup] = {rollup.name: rollup for rollup in (WORK_DIRS,)}


class RollupError(RuntimeError):
    """The rollup cannot answer: its schema is missing or broken."""


class RollupMissing(RollupError):
    """Migration 011 has not been applied (the app has not booted the new code)."""


class RollupNotReady(RollupError):
    """The rollup exists but has not been seeded with pre-trigger history."""


def seed_budget_ms() -> int:
    raw = os.environ.get(SEED_STATEMENT_TIMEOUT_ENV, str(DEFAULT_SEED_STATEMENT_TIMEOUT_MS))
    return db._positive_int(raw, SEED_STATEMENT_TIMEOUT_ENV)


def _state(cur, rollup: Rollup, *, lock: bool = False) -> tuple[bool, int]:
    cur.execute("SELECT to_regclass('public.event_rollups'), to_regclass(%s)", (rollup.table,))
    registry, table = cur.fetchone()
    if registry is None or table is None:
        raise RollupMissing(f"rollup {rollup.name}: tables missing (migration 011 not applied)")
    cur.execute(
        "SELECT ready, generation FROM event_rollups WHERE name = %s"
        + (" FOR UPDATE" if lock else ""),
        (rollup.name,),
    )
    row = cur.fetchone()
    if row is None:
        raise RollupMissing(f"rollup {rollup.name}: no event_rollups row")
    return bool(row[0]), int(row[1])


def _require_triggers(cur, rollup: Rollup) -> None:
    """A rollup without all four enabled triggers silently stops counting."""
    cur.execute(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = 'events'::regclass "
        "AND tgname = ANY(%s) AND tgenabled <> 'D'",
        (list(rollup.triggers),),
    )
    present = {row[0] for row in cur.fetchall()}
    missing = sorted(set(rollup.triggers) - present)
    if missing:
        raise RollupError(f"rollup {rollup.name}: triggers missing or disabled: {missing}")


def counts(rollup: Rollup) -> dict[str, int]:
    """Every key with a positive count. Raises unless the rollup is ready."""
    with db.cursor() as cur:
        ready, _ = _state(cur, rollup)
        if not ready:
            raise RollupNotReady(f"rollup {rollup.name} has not been seeded")
        cur.execute(f"SELECT {rollup.key_column}, seen FROM {rollup.table} WHERE seen > 0")
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def full_scan(rollup: Rollup, *, budget_ms: int | None = None) -> dict[str, int]:
    """The pre-rollup answer, straight from `events`. Seed/verify/fallback only."""
    conn = db._connect(statement_timeout_ms=budget_ms or seed_budget_ms())
    try:
        with conn.cursor() as cur:
            cur.execute(rollup.full_scan_sql())
            return {row[0]: int(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()


def seed(
    rollup: Rollup,
    *,
    budget_ms: int | None = None,
    _between_phases: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Add pre-trigger history to an unready rollup. Exact and online; see module doc.

    `_between_phases` is a test seam: it runs after the snapshot is read and
    before the delta is applied, which is where a concurrent write lands.
    """
    started = time.monotonic()
    conn = db._connect(statement_timeout_ms=budget_ms or seed_budget_ms())
    try:
        conn.set_session(
            isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            readonly=True,
        )
        with conn.cursor() as cur:
            ready, generation = _state(cur, rollup)
            if ready:
                conn.rollback()
                return {"rollup": rollup.name, "seeded": False, "reason": "already ready"}
            _require_triggers(cur, rollup)
            cur.execute(rollup.full_scan_sql())
            full = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(f"SELECT {rollup.key_column}, seen FROM {rollup.table}")
            live = {row[0]: int(row[1]) for row in cur.fetchall()}
        conn.rollback()

        delta = {
            key: full.get(key, 0) - live.get(key, 0)
            for key in full.keys() | live.keys()
            if full.get(key, 0) != live.get(key, 0)
        }
        if _between_phases is not None:
            _between_phases()

        conn.set_session(
            isolation_level=psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED,
            readonly=False,
        )
        with conn.cursor() as cur:
            ready_now, generation_now = _state(cur, rollup, lock=True)
            if ready_now or generation_now != generation:
                conn.rollback()
                return {
                    "rollup": rollup.name,
                    "seeded": False,
                    "reason": "raced: another seed, a reset or a TRUNCATE landed first",
                }
            if delta:
                keys = list(delta)
                cur.execute(
                    f"INSERT INTO {rollup.table} AS t ({rollup.key_column}, seen) "
                    "SELECT k, d FROM unnest(%s::text[], %s::bigint[]) AS x(k, d) ORDER BY 1 "
                    f"ON CONFLICT ({rollup.key_column}) "
                    "DO UPDATE SET seen = t.seen + EXCLUDED.seen",
                    (keys, [delta[key] for key in keys]),
                )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            cur.execute(
                "UPDATE event_rollups SET ready = TRUE, seeded_at = NOW(), seed_ms = %s, "
                "seed_keys = %s, seed_events = %s WHERE name = %s",
                (elapsed_ms, len(full), sum(full.values()), rollup.name),
            )
        conn.commit()
    finally:
        conn.close()
    report = {
        "rollup": rollup.name,
        "seeded": True,
        "keys": len(full),
        "events": sum(full.values()),
        "adjusted_keys": len(delta),
        "seconds": round(elapsed_ms / 1000, 2),
    }
    logger.info("rollup seed %s", report)
    return report


def ensure_ready(rollup: Rollup) -> dict[str, Any] | None:
    """Seed the rollup if it is not ready. Returns the seed report, if one ran."""
    with db.cursor() as cur:
        ready, _ = _state(cur, rollup)
    if ready:
        return None
    report = seed(rollup)
    if not report["seeded"]:
        # Lost a race to a concurrent seed or a TRUNCATE: either way it is ready
        # now, or the next caller seeds it. Re-check rather than guess.
        with db.cursor() as cur:
            ready, _ = _state(cur, rollup)
        if not ready:
            raise RollupNotReady(
                f"rollup {rollup.name}: seed did not complete ({report['reason']})"
            )
    return report


def verify(rollup: Rollup, *, budget_ms: int | None = None, sample: int = 20) -> dict[str, Any]:
    """Compare the rollup with a full scan taken in the same snapshot."""
    started = time.monotonic()
    conn = db._connect(statement_timeout_ms=budget_ms or seed_budget_ms())
    try:
        conn.set_session(
            isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
            readonly=True,
        )
        with conn.cursor() as cur:
            ready, generation = _state(cur, rollup)
            cur.execute(rollup.full_scan_sql())
            full = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(f"SELECT {rollup.key_column}, seen FROM {rollup.table} WHERE seen <> 0")
            rolled = {row[0]: int(row[1]) for row in cur.fetchall()}
        conn.rollback()
    finally:
        conn.close()
    mismatched = sorted(
        key for key in full.keys() | rolled.keys() if full.get(key, 0) != rolled.get(key, 0)
    )
    return {
        "rollup": rollup.name,
        "ready": ready,
        "generation": generation,
        "equal": ready and not mismatched,
        "keys": len(full),
        "events": sum(full.values()),
        "rollup_keys": len(rolled),
        "rollup_events": sum(rolled.values()),
        "mismatched_keys": len(mismatched),
        "mismatches": [
            {"key": key, "full_scan": full.get(key, 0), "rollup": rolled.get(key, 0)}
            for key in mismatched[:sample]
        ],
        "seconds": round(time.monotonic() - started, 2),
    }


def reset(rollup: Rollup) -> None:
    """Mark a rollup unready so the next seed recomputes it from the trail."""
    with db.cursor() as cur:
        _state(cur, rollup, lock=True)
        cur.execute(
            "UPDATE event_rollups SET ready = FALSE, generation = generation + 1 WHERE name = %s",
            (rollup.name,),
        )


def status() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('public.event_rollups')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute(
            "SELECT name, ready, generation, seeded_at, seed_ms, seed_keys, seed_events "
            "FROM event_rollups ORDER BY name"
        )
        rows = cur.fetchall()
    return [
        {
            "rollup": row[0],
            "ready": row[1],
            "generation": row[2],
            "seeded_at": row[3].isoformat() if row[3] else None,
            "seed_ms": row[4],
            "seed_keys": row[5],
            "seed_events": row[6],
        }
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m candystore.rollups", description=__doc__.split("\n")[0]
    )
    parser.add_argument("action", choices=("status", "seed", "verify", "reset"))
    parser.add_argument(
        "rollup", nargs="?", default=None, help=f"one of {', '.join(ROLLUPS)} (default: all)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(message)s")

    if args.action == "status":
        print(json.dumps(status(), indent=2))
        return 0
    names = [args.rollup] if args.rollup else list(ROLLUPS)
    unknown = [name for name in names if name not in ROLLUPS]
    if unknown:
        parser.error(f"unknown rollup: {', '.join(unknown)}")
    failed = False
    for name in names:
        rollup = ROLLUPS[name]
        if args.action == "seed":
            result: Any = seed(rollup)
        elif args.action == "verify":
            result = verify(rollup)
            failed = failed or not result["equal"]
        else:
            reset(rollup)
            result = {"rollup": name, "reset": True}
        print(json.dumps(result, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
