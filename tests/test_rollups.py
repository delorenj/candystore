"""Full-history rollups (candystore.rollups, migrations/011_event_rollups.sql).

The rollup has one job: answer exactly what a full scan of `events` would, at
the cost of its own key count. So every DB test here ends the same way, by
asserting `verify()` -- the old full scan and the rollup read in one snapshot --
reports them equal.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from candystore import rollups
from candystore.db import cursor, insert_event
from candystore.projects import WORK_DIR_EXPR, observed_work_dirs

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "011_event_rollups.sql"


def test_trigger_key_expression_is_work_dir_expr_verbatim():
    """The triggers, the map, and the query filters must derive the key one way."""
    sql = MIGRATION.read_text(encoding="utf-8")
    # insert, delete and both halves of update, each in SELECT and WHERE (or
    # the update's outer WHERE on the projected column).
    assert sql.count(WORK_DIR_EXPR) >= 6
    for function in ("on_insert", "on_delete", "on_update", "on_truncate"):
        assert f"event_work_dirs_{function}()" in sql
    for trigger in rollups.WORK_DIRS.triggers:
        assert f"tgname = '{trigger}'" in sql


def test_full_scan_sql_is_the_query_it_replaces():
    assert rollups.WORK_DIRS.full_scan_sql() == (
        f"SELECT {WORK_DIR_EXPR} AS key, COUNT(*) FROM events "
        f"WHERE {WORK_DIR_EXPR} IS NOT NULL GROUP BY 1"
    )


def _post(sample_event, work_dir=None, *, cwd=None, event_id=None):
    data = {"working_directory": work_dir}
    if cwd is not None:
        data["payload"] = {"cwd": cwd}
    event = sample_event(id=event_id or str(uuid.uuid4()), data=data)
    assert insert_event(event) in (True, False)
    return event["id"]


def _assert_equal():
    report = rollups.verify(rollups.WORK_DIRS)
    assert report["equal"], report
    return report


def test_truncate_leaves_the_rollup_ready_and_empty(db):
    # The db fixture TRUNCATEs events, which the trigger answers exactly.
    assert rollups.counts(rollups.WORK_DIRS) == {}
    report = _assert_equal()
    assert report["keys"] == 0


def test_writes_keep_the_rollup_equal_to_a_full_scan(db, sample_event):
    a, b = "/home/delorenj/code/33GOD/candystore", "/home/delorenj/code/client-portal/.worktrees/x"
    first = _post(sample_event, a)
    _post(sample_event, a)
    _post(sample_event, b)
    # codex nests cwd one level deeper; an empty working_directory falls through.
    _post(sample_event, "", cwd="/home/delorenj/code/codex-only")
    # No directory at all: counted nowhere, by either side.
    _post(sample_event, None)
    # A redelivered event id is ON CONFLICT DO NOTHING and must not count twice.
    _post(sample_event, a, event_id=first)

    assert rollups.counts(rollups.WORK_DIRS) == {a: 2, b: 1, "/home/delorenj/code/codex-only": 1}
    _assert_equal()

    with cursor() as cur:
        # Retention deletes; a rewrite moves a row between keys.
        cur.execute("DELETE FROM events WHERE id = %s", (first,))  # sql-guard: reviewed
        cur.execute(
            "UPDATE events SET data = jsonb_set(data, '{working_directory}', to_jsonb(%s::text)) "
            "WHERE data->>'working_directory' = %s",
            ("/home/delorenj/code/moved", b),
        )
        # An UPDATE that does not touch the key changes nothing.
        cur.execute("UPDATE events SET subject = 'x'")
    assert rollups.counts(rollups.WORK_DIRS) == {
        a: 1,
        "/home/delorenj/code/moved": 1,
        "/home/delorenj/code/codex-only": 1,
    }
    _assert_equal()


def test_seed_recovers_history_the_triggers_never_saw(db, sample_event):
    """Simulate pre-trigger history by scribbling over the rollup, then seed."""
    dirs = [f"/home/delorenj/code/seed-{index}" for index in range(5)]
    ids = [_post(sample_event, dirs[index % 5]) for index in range(23)]
    with cursor() as cur:
        cur.execute("UPDATE event_work_dirs SET seen = seen * 7 - 3")
        cur.execute(
            "DELETE FROM event_work_dirs WHERE work_dir = %s", (dirs[0],)
        )  # sql-guard: reviewed
        cur.execute("INSERT INTO event_work_dirs VALUES ('/home/delorenj/code/ghost', -4)")
    rollups.reset(rollups.WORK_DIRS)
    with pytest.raises(rollups.RollupNotReady):
        rollups.counts(rollups.WORK_DIRS)

    def concurrent_writes():
        # Lands between the snapshot and the apply: exactly the window the
        # additive delta has to survive.
        _post(sample_event, dirs[1])
        _post(sample_event, "/home/delorenj/code/brand-new")
        with cursor() as cur:
            cur.execute("DELETE FROM events WHERE id = %s", (ids[2],))  # sql-guard: reviewed

    report = rollups.seed(rollups.WORK_DIRS, _between_phases=concurrent_writes)
    assert report["seeded"] and report["events"] == 23

    counts = rollups.counts(rollups.WORK_DIRS)
    assert counts[dirs[1]] == 6
    assert counts[dirs[2]] == 4
    assert counts["/home/delorenj/code/brand-new"] == 1
    assert "/home/delorenj/code/ghost" not in counts
    _assert_equal()
    # Idempotent: a second seed is a no-op, not a double count.
    assert rollups.seed(rollups.WORK_DIRS)["seeded"] is False
    _assert_equal()


def test_a_truncate_during_seed_aborts_the_apply(db, sample_event, guarded_truncate):
    _post(sample_event, "/home/delorenj/code/a")
    rollups.reset(rollups.WORK_DIRS)
    report = rollups.seed(rollups.WORK_DIRS, _between_phases=guarded_truncate)
    assert report["seeded"] is False and "raced" in report["reason"]
    assert rollups.counts(rollups.WORK_DIRS) == {}
    _assert_equal()


def test_scheduled_sync_reads_the_rollup_not_the_trail(db, sample_event, monkeypatch):
    """project:sync runs hourly. Its directory count must cost the rollup's
    size: no statement it issues may read `events`."""
    _post(sample_event, "/home/delorenj/code/33GOD/candystore")
    _post(sample_event, "/home/delorenj/code/33GOD/candystore")
    _post(sample_event, "", cwd="/home/delorenj/code/x")

    import candystore.db as db_module

    seen_sql: list[str] = []
    original = db_module._BoundedCursor.execute

    def recording(self, query, vars=None):
        seen_sql.append(str(query))
        return original(self, query, vars)

    monkeypatch.setattr(db_module._BoundedCursor, "execute", recording)
    monkeypatch.setattr(
        rollups, "full_scan", lambda *a, **k: pytest.fail("sync fell back to a full scan")
    )
    observed = observed_work_dirs()

    assert observed == {"/home/delorenj/code/33GOD/candystore": 2, "/home/delorenj/code/x": 1}
    assert seen_sql, "observed_work_dirs issued no SQL through db.cursor()"
    assert not [sql for sql in seen_sql if re.search(r"\bFROM\s+events\b", sql)], seen_sql


def test_sync_seeds_an_unready_rollup_once(db, sample_event):
    _post(sample_event, "/home/delorenj/code/a")
    rollups.reset(rollups.WORK_DIRS)
    assert observed_work_dirs() == {"/home/delorenj/code/a": 1}
    assert rollups.status()[0]["ready"] is True
    _assert_equal()


def test_seed_refuses_a_rollup_whose_triggers_are_missing(db):
    with cursor() as cur:
        cur.execute("ALTER TABLE events DISABLE TRIGGER event_work_dirs_ins")
    try:
        rollups.reset(rollups.WORK_DIRS)
        with pytest.raises(rollups.RollupError, match="event_work_dirs_ins"):
            rollups.seed(rollups.WORK_DIRS)
    finally:
        with cursor() as cur:
            cur.execute("ALTER TABLE events ENABLE TRIGGER event_work_dirs_ins")
