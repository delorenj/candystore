from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from candystore.db import cursor
from candystore.summarize import canonical_type, summarize

# The `type` column holds two shapes forever: the historical five-token
# `bloodbank.v1.<domain>.<entity>.<action>` on ~718k rows already in the table,
# and the current version-free four-token `bloodbank.<domain>.<entity>.<action>`
# on everything published since. A positional `split_part(type, '.', 3/4)` is
# agnostic to the version token's VALUE, not to its PRESENCE -- drop the token
# and domain/entity shift one slot left, so a --scope filter silently returns
# the WRONG rows instead of erroring (measured: `--scope agent.tool` matched 0
# of the 1128 new-shape rows, and those rows answered `--scope tool` instead).
# Strip the prefix and its optional version first; then domain and entity sit
# at positions 1 and 2 for both shapes.
SCOPE_TYPE_EXPR = "regexp_replace(type, '^bloodbank\\.(v[0-9]+\\.)?', '')"

# The session summary counts events by CANONICAL type. Two independent
# normalizations are needed and `canonical_type()` (summarize.py) already does
# both, so this module keys on its output instead of encoding the knowledge a
# third time:
#   1. the version token -- `bloodbank.v1.conversation.turn.started` and
#      `bloodbank.conversation.turn.started` are the same event;
#   2. the `tool.tool_call.* -> agent.tool.*` ENTITY rename, which survives
#      version-stripping. The live table holds 29,341 v1.tool.tool_call.requested
#      AND 332,443 agent.tool.requested rows across both shapes; counting only
#      the v1 tool_call spelling reported 0 tools for every session recorded
#      since the rename.
TURN_STARTED_TYPE = "bloodbank.conversation.turn.started"
TOOL_REQUESTED_TYPE = "bloodbank.agent.tool.requested"
TOOL_INVOKED_TYPE = "bloodbank.agent.tool.invoked"

# A session ends under EITHER spelling. `agent.session.ended` supersedes
# `cli.session.ended` for agent CLIs (bloodbank/docs/event-naming.md) but both
# schemas are live and both are in the corpus, so this is a two-member set, not
# a rename. The old probe was `type.endswith("cli.session.ended")`, which found
# the 592 v1.cli rows and missed all 4,980 agent.session.ended ones -- so for
# every modern session the authoritative `data.total_turns` /
# `data.duration_seconds` payload was never read. `audio.session.ended` is a
# different domain and is deliberately not in here.
SESSION_END_TYPES = frozenset(
    {
        "bloodbank.agent.session.ended",
        "bloodbank.cli.session.ended",
    }
)

PROJECT_EXPR = (
    "COALESCE(NULLIF(data->>'project', ''), "
    "NULLIF(regexp_replace(COALESCE(data->>'git_remote', ''), '.*/', ''), ''), "
    "NULLIF(regexp_replace(COALESCE(data->>'working_directory', ''), '.*/', ''), ''), "
    "'unknown')"
)


def list_events(
    *,
    type: str | None = None,
    domain: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    correlationid: str | None = None,
    producer: str | None = None,
    service: str | None = None,
    cli: str | None = None,
    project: str | None = None,
    scope: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    where, params = _filters(
        type=type,
        domain=domain,
        from_time=from_time,
        to_time=to_time,
        correlationid=correlationid,
        producer=producer,
        service=service,
        cli=cli,
        project=project,
        scope=scope,
    )
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    count_sql = f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}"
    select_sql = f"""
    SELECT id, type, time, producer, service, domain, actor, data, correlationid, raw,
           {PROJECT_EXPR} AS project
    FROM events
    WHERE {' AND '.join(where)}
    ORDER BY time DESC
    LIMIT %s OFFSET %s
    """

    with cursor() as cur:
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]
        cur.execute(select_sql, [*params, limit, offset])
        rows = cur.fetchall()

    events = [_preview_from_row(row) for row in rows]
    return {"events": events, "total": total, "limit": limit, "offset": offset}


def get_event_record(event_id: str) -> dict[str, Any] | None:
    sql = f"""
    SELECT id, type, time, producer, service, domain, actor, data, correlationid, raw,
           {PROJECT_EXPR} AS project
    FROM events
    WHERE id = %s
    """
    with cursor() as cur:
        cur.execute(sql, (event_id,))
        row = cur.fetchone()
    return _preview_from_row(row) if row else None


def get_event(event_id: str) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT raw FROM events WHERE id = %s", (event_id,))
        row = cur.fetchone()
    return row[0] if row else None


def get_session_events(correlationid: str) -> list[dict[str, Any]]:
    sql = """
    SELECT id, type, time, producer, service, domain, actor, data, correlationid, raw
    FROM events
    WHERE correlationid::text = %s
    ORDER BY time ASC
    """
    with cursor() as cur:
        cur.execute(sql, (correlationid,))
        rows = cur.fetchall()

    events = []
    for row in rows:
        raw = row[9]
        events.append(
            {
                "id": str(row[0]),
                "type": row[1],
                "time": _iso(row[2]),
                "producer": row[3],
                "service": row[4],
                "domain": row[5],
                "actor": row[6],
                "data": row[7],
                "correlationid": str(row[8]) if row[8] else None,
                "summary": summarize(raw),
                "raw": raw,
            }
        )
    return events


def get_session_summary(correlationid: str) -> dict[str, Any]:
    return session_summary(correlationid, get_session_events(correlationid))


def session_summary(correlationid: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll a session's timeline up into counts. Pure: no DB, so it is testable
    without CANDYSTORE_TEST_DATABASE_URL (see tests/test_session_summary.py)."""
    # `events_by_type` keeps the RAW spelling -- it is a report of what is
    # literally in the table, and collapsing it would hide a producer still
    # emitting a retired shape. Every derived COUNT below reads the canonical
    # tally instead, so it sees both wire shapes as one event.
    event_types = Counter(event["type"] for event in events)
    canonical_counts = Counter(canonical_type(event["type"]) for event in events)

    started_at = events[0]["time"] if events else None
    ended_at = events[-1]["time"] if events else None
    duration_seconds = _duration_seconds(started_at, ended_at)
    session_end = next(
        (event for event in reversed(events) if canonical_type(event["type"]) in SESSION_END_TYPES),
        None,
    )
    data = (session_end or {}).get("data") or {}
    actor = ((session_end or events[0])["actor"] if events else {}) or {}

    # The session-end payload is authoritative when it is there, but a session
    # still in flight (or one whose end event never landed) has no payload at
    # all -- fall back to counting the timeline. `is None` rather than dict
    # default: a present-but-null field must fall back too.
    total_turns = data.get("total_turns")
    if total_turns is None:
        total_turns = canonical_counts.get(TURN_STARTED_TYPE, 0)
    reported_duration = data.get("duration_seconds")
    if reported_duration is None:
        reported_duration = duration_seconds

    return {
        "session_id": correlationid,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": reported_duration,
        "cli": actor.get("cli"),
        "project": _project_from_data(data),
        "events_count": len(events),
        "turns": total_turns,
        "tools_requested": canonical_counts.get(TOOL_REQUESTED_TYPE, 0),
        "tools_invoked": canonical_counts.get(TOOL_INVOKED_TYPE, 0),
        "events_by_type": dict(event_types),
    }


def heatmap(
    group_by: str = "project",
    from_time: str | None = None,
    to_time: str | None = None,
) -> list[dict[str, Any]]:
    group_col = {
        "project": PROJECT_EXPR,
        "cli": "COALESCE(actor->>'cli', 'unknown')",
        "domain": "domain",
    }.get(group_by, "domain")

    sql = f"""
    SELECT DATE_TRUNC('hour', time) AS hour, {group_col} AS bucket, COUNT(*) AS count
    FROM events
    WHERE time >= %s AND time <= %s
    GROUP BY hour, bucket
    ORDER BY hour DESC, count DESC
    """
    with cursor() as cur:
        cur.execute(sql, (from_time or "1970-01-01", to_time or "2099-01-01"))
        rows = cur.fetchall()

    return [
        {
            "hour": _iso(row[0]),
            "bucket": row[1],
            group_by if group_by in {"project", "cli", "domain"} else "domain": row[1],
            "count": row[2],
        }
        for row in rows
    ]


def daily(from_time: str | None = None, to_time: str | None = None) -> list[dict[str, Any]]:
    sql = """
    SELECT DATE_TRUNC('day', time) AS day, COUNT(*) AS count
    FROM events
    WHERE time >= %s AND time <= %s
    GROUP BY day
    ORDER BY day DESC
    """
    with cursor() as cur:
        cur.execute(sql, (from_time or "1970-01-01", to_time or "2099-01-01"))
        rows = cur.fetchall()
    return [{"day": _iso(row[0]), "count": row[1]} for row in rows]


def by_cli(from_time: str | None = None, to_time: str | None = None) -> list[dict[str, Any]]:
    sql = """
    SELECT COALESCE(actor->>'cli', 'unknown') AS cli, COUNT(*) AS count
    FROM events
    WHERE time >= %s AND time <= %s
    GROUP BY cli
    ORDER BY count DESC, cli ASC
    """
    with cursor() as cur:
        cur.execute(sql, (from_time or "1970-01-01", to_time or "2099-01-01"))
        rows = cur.fetchall()
    return [{"cli": row[0], "count": row[1]} for row in rows]


def by_project(from_time: str | None = None, to_time: str | None = None) -> list[dict[str, Any]]:
    sql = f"""
    SELECT {PROJECT_EXPR} AS project, COUNT(*) AS count
    FROM events
    WHERE time >= %s AND time <= %s
    GROUP BY project
    ORDER BY count DESC, project ASC
    """
    with cursor() as cur:
        cur.execute(sql, (from_time or "1970-01-01", to_time or "2099-01-01"))
        rows = cur.fetchall()
    return [{"project": row[0], "count": row[1]} for row in rows]


def _filters(**kwargs: str | None) -> tuple[list[str], list[Any]]:
    where = ["1=1"]
    params: list[Any] = []

    event_type = kwargs["type"]
    if event_type:
        values = [value.strip() for value in event_type.split(",") if value.strip()]
        if len(values) == 1:
            where.append("type = %s")
            params.append(values[0])
        elif values:
            placeholders = ", ".join(["%s"] * len(values))
            where.append(f"type IN ({placeholders})")
            params.extend(values)
    scope = kwargs.get("scope")
    if scope:
        values = [value.strip() for value in scope.split(",") if value.strip()]
        conditions = []
        for value in values:
            parts = value.split(".")
            if len(parts) == 2:
                # domain.entity — match both, version-agnostically
                conditions.append(
                    f"split_part({SCOPE_TYPE_EXPR}, '.', 1) = %s "
                    f"AND split_part({SCOPE_TYPE_EXPR}, '.', 2) = %s"
                )
                params.extend(parts)
            elif len(parts) == 1:
                # domain only
                conditions.append(f"split_part({SCOPE_TYPE_EXPR}, '.', 1) = %s")
                params.append(parts[0])
        if conditions:
            where.append(f"({' OR '.join(conditions)})")
    if kwargs["domain"]:
        where.append("domain = %s")
        params.append(kwargs["domain"])
    if kwargs["from_time"]:
        where.append("time >= %s")
        params.append(kwargs["from_time"])
    if kwargs["to_time"]:
        where.append("time <= %s")
        params.append(kwargs["to_time"])
    if kwargs["correlationid"]:
        where.append("correlationid::text = %s")
        params.append(kwargs["correlationid"])
    if kwargs["producer"]:
        where.append("producer = %s")
        params.append(kwargs["producer"])
    if kwargs["service"]:
        where.append("service = %s")
        params.append(kwargs["service"])
    if kwargs["cli"]:
        where.append("actor->>'cli' = %s")
        params.append(kwargs["cli"])
    if kwargs["project"]:
        where.append(f"{PROJECT_EXPR} ILIKE %s")
        params.append(f"%{kwargs['project']}%")

    return where, params


def _preview_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    raw = row[9]
    summary = summarize(raw)
    actor = row[6] or {}
    return {
        "id": str(row[0]),
        "type": row[1],
        "time": _iso(row[2]),
        "producer": row[3],
        "service": row[4],
        "domain": row[5],
        "actor": actor,
        "data": row[7],
        "correlationid": str(row[8]) if row[8] else None,
        "cli": actor.get("cli"),
        "project": row[10],
        "summary": summary,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _duration_seconds(started_at: str | None, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((end - start).total_seconds())


def _project_from_data(data: dict[str, Any]) -> str:
    if data.get("project"):
        return str(data["project"])
    if data.get("git_remote"):
        return str(data["git_remote"]).rstrip("/").split("/")[-1].replace(".git", "")
    if data.get("working_directory"):
        return str(data["working_directory"]).rstrip("/").split("/")[-1]
    return "unknown"
