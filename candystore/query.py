from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from candystore.db import cursor
from candystore.summarize import summarize

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
    events = get_session_events(correlationid)
    event_types = Counter(event["type"] for event in events)
    started_at = events[0]["time"] if events else None
    ended_at = events[-1]["time"] if events else None
    duration_seconds = _duration_seconds(started_at, ended_at)
    session_end = next(
        (event for event in reversed(events) if event["type"].endswith("cli.session.ended")),
        None,
    )
    data = (session_end or {}).get("data") or {}
    actor = ((session_end or events[0])["actor"] if events else {}) or {}

    return {
        "session_id": correlationid,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": data.get("duration_seconds", duration_seconds),
        "cli": actor.get("cli"),
        "project": _project_from_data(data),
        "events_count": len(events),
        "turns": data.get(
            "total_turns",
            event_types.get("bloodbank.v1.conversation.turn.started", 0),
        ),
        "tools_requested": event_types.get("bloodbank.v1.tool.tool_call.requested", 0),
        "tools_invoked": event_types.get("bloodbank.v1.tool.tool_call.invoked", 0),
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
                # scope.level — match exactly at positions 3 and 4
                conditions.append(
                    "split_part(type, '.', 3) = %s AND split_part(type, '.', 4) = %s"
                )
                params.extend(parts)
            elif len(parts) == 1:
                # scope only — match position 3
                conditions.append("split_part(type, '.', 3) = %s")
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
