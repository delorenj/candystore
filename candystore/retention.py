"""Archive-then-delete retention for CandyStore events.

Every batch runs in one transaction:

1. lock a bounded set of eligible rows (`type`, arrival older than the cutoff),
2. append them, with any embedding, to a gzip JSONL archive and fsync it,
3. delete exactly those IDs (embeddings cascade) and commit.

A crash after the fsync but before the commit leaves rows both archived and
still present; the next run archives them again, and `restore` skips IDs that
already exist. Nothing is deleted that is not already durable in an archive.

Eligibility is by arrival (`received_at`), with one cutoff fixed per run from
the database clock, so late-arriving or future-dated events are never early
candidates. PostgreSQL DELETE makes space reusable inside the table; it does
not shrink the files on disk. Pruning caps growth, it does not reclaim disk.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

from candystore import db

DEFAULT_ARCHIVE_DIR = Path(
    os.environ.get("CANDYSTORE_ARCHIVE_DIR", Path.home() / "backups/candystore/event-archive")
)
BATCH_STATEMENT_TIMEOUT = "300s"
_TYPE = re.compile(r"[A-Za-z0-9_.:-]+")


def _columns(cur) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = 'events'"
        " AND is_generated = 'NEVER' ORDER BY ordinal_position"
    )
    return [row[0] for row in cur.fetchall()]


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prune(
    types: Sequence[str],
    older_than_days: int,
    archive_dir: Path = DEFAULT_ARCHIVE_DIR,
    *,
    batch: int = 5000,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Archive then delete events of `types` that arrived over N days ago."""
    if not types or any(not _TYPE.fullmatch(t) for t in types):
        raise ValueError("at least one event type is required, e.g. bloodbank.agent.tool.requested")
    if not 1 <= older_than_days <= 3650:
        raise ValueError("--older-than must be between 1 and 3650 days")
    if not 1 <= batch <= 50_000:
        raise ValueError("--batch must be between 1 and 50000")
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (BATCH_STATEMENT_TIMEOUT,))
            cur.execute("SELECT now() - make_interval(days => %s), now()", (older_than_days,))
            cutoff, started = cur.fetchone()
        conn.commit()
        report: dict[str, Any] = {
            "types": sorted(set(types)),
            "older_than_days": older_than_days,
            "cutoff": cutoff.astimezone(UTC).isoformat(),
            "dry_run": dry_run,
            "deleted": {},
        }
        if dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT type, count(*) FROM events WHERE type = ANY(%s) AND received_at < %s"
                    " GROUP BY type ORDER BY type",
                    (list(types), cutoff),
                )
                report["eligible"] = dict(cur.fetchall())
            conn.rollback()
            return report

        archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        slug = "+".join(t.rsplit(".", 2)[-2] + "." + t.rsplit(".", 1)[-1] for t in report["types"])
        archive = archive_dir / f"events-{slug}-before-{cutoff:%Y%m%d}-{stamp}.jsonl.gz"
        report["archive"] = str(archive)
        remaining = limit
        for event_type in report["types"]:
            deleted = 0
            while remaining is None or remaining > 0:
                size = batch if remaining is None else min(batch, remaining)
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = %s", (BATCH_STATEMENT_TIMEOUT,))
                    # (type, time) index walks oldest-first; eligible rows come first.
                    cur.execute(
                        "SELECT id FROM events WHERE type = %s AND received_at < %s"
                        " ORDER BY time, id LIMIT %s FOR UPDATE SKIP LOCKED",
                        (event_type, cutoff, size),
                    )
                    ids = [row[0] for row in cur.fetchall()]
                    if not ids:
                        conn.rollback()
                        break
                    cur.execute(
                        "SELECT jsonb_build_object('event', to_jsonb(e) - 'search_text',"
                        " 'embedding', m.embedding::text)::text"
                        " FROM events e LEFT JOIN event_embeddings m ON m.event_id = e.id"
                        " WHERE e.id = ANY(%s::uuid[]) ORDER BY e.time, e.id",
                        (ids,),
                    )
                    lines = [row[0] for row in cur.fetchall()]
                    if len(lines) != len(ids):
                        raise RuntimeError(f"archived {len(lines)} rows for {len(ids)} locked IDs")
                    # One complete gzip member per batch; multi-member gzip reads as one stream.
                    with archive.open("ab") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as out:
                        for line in lines:
                            out.write(line.encode("utf-8") + b"\n")
                    with archive.open("rb") as raw:
                        os.fsync(raw.fileno())
                    _fsync_dir(archive_dir)
                    cur.execute("DELETE FROM events WHERE id = ANY(%s::uuid[])", (ids,))
                    if cur.rowcount != len(ids):
                        raise RuntimeError(
                            f"deleted {cur.rowcount} rows for {len(ids)} archived IDs"
                        )
                conn.commit()
                deleted += len(ids)
                if remaining is not None:
                    remaining -= len(ids)
            report["deleted"][event_type] = deleted
        if archive.exists():
            report["archive_sha256"] = _sha256(archive)
            report["archive_bytes"] = archive.stat().st_size
            manifest = archive.with_suffix("").with_suffix(".manifest.json")
            manifest.write_text(json.dumps(report, indent=2) + "\n")
            report["manifest"] = str(manifest)
        else:
            report["archive"] = None
        return report
    finally:
        conn.close()


def _records(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def restore(path: Path, *, batch: int = 1000) -> dict[str, int]:
    """Re-insert archived events (and embeddings); IDs already present are skipped."""
    conn = db._connect()
    seen = inserted = embeddings = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (BATCH_STATEMENT_TIMEOUT,))
            columns = _columns(cur)
        conn.commit()
        cols = ", ".join(f'"{c}"' for c in columns)
        pending: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal inserted, embeddings
            with conn.cursor() as cur:
                for record in pending:
                    cur.execute(
                        f"INSERT INTO events ({cols}) SELECT {cols}"
                        " FROM jsonb_populate_record(NULL::events, %s::jsonb)"
                        " ON CONFLICT (id) DO NOTHING",
                        (json.dumps(record["event"]),),
                    )
                    inserted += cur.rowcount
                    if record.get("embedding"):
                        cur.execute(
                            "INSERT INTO event_embeddings (event_id, embedding)"
                            " VALUES (%s, %s::vector) ON CONFLICT (event_id) DO NOTHING",
                            (record["event"]["id"], record["embedding"]),
                        )
                        embeddings += cur.rowcount
            conn.commit()
            pending.clear()

        for record in _records(path):
            seen += 1
            pending.append(record)
            if len(pending) >= batch:
                flush()
        if pending:
            flush()
        return {"archived": seen, "inserted": inserted, "embeddings": embeddings}
    finally:
        conn.close()
