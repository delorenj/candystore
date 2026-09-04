from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from candystore.db import cursor
from candystore.projects import WORK_DIR_EXPR
from candystore.summarize import UNRESOLVED_PROJECT, canonical_type, summarize

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

# The project an event belongs to: a PJangler registry slug, resolved through
# project_dir_map (migrations 005/006), or NULL when nothing claims it.
#
# This replaced a basename fallback chain that reported worktrees, subdirectories
# and bare-repo suffixes as projects. It also replaced a SECOND, DIFFERENT
# implementation in summarize.py, which stripped `.git` where this one did not --
# so /events said `intelliforia.git` on the card while /events/<id>/summary said
# `intelliforia` for the same row, on 399 rows in 7 days.
#
# `data.project` is consulted, but only if it names a real registry project.
# Trusting it outright is what the obvious ladder would do, and it is wrong here:
# measured over the whole table, three of its five distinct values are entire
# JSON objects serialized as text ({"name": "James Brennan", "slug": ..., ...}),
# and its highest-volume plain string is `wax` (2,803 rows), which is not a
# registered project at all. Validating against the registry turns all of that
# into an honest NULL instead of a JSON blob rendered as a project name.
#
# Cost: two correlated lookups, both primary-key probes rather than the prefix
# scan a naive version would do. Measured 3.5 ms for a 200-row page over the
# default window (compare 141 s for a correlated longest-prefix subquery, which
# is exactly why the prefix matching lives in the map instead of here).
PROJECT_EXPR = f"""COALESCE(
    (SELECT m.slug FROM project_dir_map m WHERE m.work_dir = {WORK_DIR_EXPR}),
    (SELECT p.slug FROM projects p WHERE p.slug = NULLIF(data->>'project', ''))
)"""

# For GROUP BY, where a NULL bucket has no name to render. The row-level
# expression stays nullable on purpose -- a consumer should be able to tell
# "no project" from a project literally called "unassigned" -- so the label is
# applied only where a chart axis needs a string.
PROJECT_LABEL_EXPR = f"COALESCE({PROJECT_EXPR}, '{UNRESOLVED_PROJECT}')"

# Free-text search (`q`) runs against the generated `search_text` column and its
# trigram index (migrations/003_search.sql).
#
# A trigram index cannot answer a pattern with no complete 3-gram in it, so a
# 1- or 2-character term silently degrades to a sequential scan of every row --
# 869k rows and ~4 GB of TOAST at the time of writing, i.e. minutes per
# keystroke from a search-as-you-type box. Short terms are refused instead of
# served slowly; the caller gets a 400 that says so.
SEARCH_MIN_TERM = 3

# An unbounded `/events` is a sequential scan over the whole table -- 886k rows
# and ~4.9 GB of TOAST at the time of writing -- because the useful filters are
# jsonb-derived expressions the planner cannot estimate. Measured on the live
# table: a project-filtered page cost 8.45 s, split 4,024 ms for the COUNT(*)
# and 4,049 ms for the LIMIT 100 SELECT, against 84 ms for a bare
# `COUNT(*) FROM events`. The same query bound to 24 h with LIMIT 200 and no
# count came back in 166 ms.
#
# So interactive browsing gets a default window, and the count becomes opt-in
# and capped. Both are policies of the *browse*, not of the query: a caller
# that names its own range means it, and `list_events` keeps answering exactly
# what it is asked. `applied_window()` is where the policy lives so that the
# HTTP layer and any future CLI share one definition of "recent".
DEFAULT_WINDOW_HOURS = 24

# Cap for an interactive count. Answering "more than 10,000" costs an index
# scan that stops early; answering "118,745" costs a full scan of the matches.
# Nothing in the UI renders the difference, so it is not worth 4 seconds.
COUNT_CAP = 10_000


class SearchError(ValueError):
    """A `q` the search index cannot serve. Distinct from a bare ValueError so
    main.py can answer 400 for it without also swallowing an unrelated internal
    ValueError and reporting a server bug as the caller's fault."""

# LIKE metacharacters in a user's term are literal text, not syntax: searching
# `file_path` must not match `filexpath`, and `100%` must not match everything.
# psycopg2 parameterizes the VALUE, which stops injection but leaves `%` and `_`
# meaningful to the pattern matcher, so they are escaped here. Backslash first,
# or it would double-escape the escapes added after it. LIKE's default escape
# character is backslash, so no ESCAPE clause is needed.
_LIKE_ESCAPES = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


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
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    total: bool = True,
    count_cap: int | None = None,
) -> dict[str, Any]:
    """List events matching the filters, newest first.

    `total=False` skips the count entirely and returns `total: None`.
    `count_cap=N` stops counting at N+1 and sets `total_capped`, so the answer
    becomes "at least N" instead of an exact figure. Leaving `count_cap=None`
    on a query with no time bound is the 4-second, 25 GB-of-buffers path
    described above -- deliberate for a caller that genuinely needs the exact
    number, wrong for anything interactive. The HTTP layer passes
    `count_cap=COUNT_CAP`.
    """
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
        q=q,
    )
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))

    clause = " AND ".join(where)
    select_sql = f"""
    SELECT id, type, time, producer, service, domain, actor, data, correlationid, raw,
           {PROJECT_EXPR} AS project
    FROM events
    WHERE {clause}
    ORDER BY time DESC
    LIMIT %s OFFSET %s
    """

    # Counting through a bounded subquery lets Postgres stop as soon as the cap
    # is reached instead of finding every match. `count_cap` is an int from
    # this module, never caller text, but it is still bound as a parameter
    # rather than interpolated -- the next person to touch this line should not
    # have to work out which of the two f-strings is safe.
    if count_cap is None:
        count_sql = f"SELECT COUNT(*) FROM events WHERE {clause}"
        count_params = list(params)
    else:
        count_sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM events WHERE {clause} LIMIT %s) capped"
        count_params = [*params, count_cap + 1]

    with cursor() as cur:
        counted: int | None = None
        if total:
            cur.execute(count_sql, count_params)
            counted = cur.fetchone()[0]
        cur.execute(select_sql, [*params, limit, offset])
        rows = cur.fetchall()

    events = [_preview_from_row(row) for row in rows]
    return {
        "events": events,
        "total": counted,
        # True means "at least `total`", not "exactly". A consumer that renders
        # the number must render the distinction too, or it reports 10,000
        # events as fact when there are 118,745.
        "total_capped": bool(count_cap is not None and counted == count_cap + 1),
        "limit": limit,
        "offset": offset,
        # The applied window, echoed so a caller can see the default it got
        # rather than inferring it. Both null means "the whole trail".
        "window": {"from": from_time, "to": to_time},
    }


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


def get_event_with_project(event_id: str) -> tuple[dict[str, Any], str | None] | None:
    """The raw envelope plus its resolved project slug.

    /events/<id>/summary needs both: the envelope to summarize, and the slug so
    its `project` agrees with the one /events reported for the same row.
    """
    with cursor() as cur:
        cur.execute(f"SELECT raw, {PROJECT_EXPR} FROM events WHERE id = %s", (event_id,))
        row = cur.fetchone()
    return (row[0], row[1]) if row else None


def get_event(event_id: str) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT raw FROM events WHERE id = %s", (event_id,))
        row = cur.fetchone()
    return row[0] if row else None


def get_session_events(correlationid: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT id, type, time, producer, service, domain, actor, data, correlationid, raw,
           {PROJECT_EXPR} AS project
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
                "project": row[10],
                "summary": summarize(raw, row[10]),
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
        # Read off the rows, which arrive already resolved from
        # get_session_events. session_summary stays pure (no DB), and there is
        # still exactly one definition of "project" in the codebase.
        "project": _session_project(events),
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
        "project": PROJECT_LABEL_EXPR,
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


def list_projects(window_hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """The PJangler registry, each project with its event count in the window.

    Reads the `projects` projection rather than deriving a project list from
    the trail. `/summary/by-project` does the latter and it is why the old
    picker offered `dist`, `.agents`, `mirror` and `james-brennan.git` as
    choices -- and took 11.59 s to do it.

    A registry project with no events is included with count 0. It has to be:
    a freshly created project is unselectable otherwise, and that is exactly
    when someone goes looking for it.
    """
    counts_sql = f"""
    SELECT m.slug, COUNT(*) AS events
    FROM events e
    JOIN project_dir_map m ON m.work_dir = {WORK_DIR_EXPR}
    WHERE e.time >= NOW() - make_interval(hours => %s)
      AND m.slug IS NOT NULL
    GROUP BY m.slug
    """
    with cursor() as cur:
        cur.execute(counts_sql, (window_hours,))
        counts = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute(
            "SELECT slug, name, repo_path, ticket_prefix FROM projects ORDER BY slug"
        )
        registry = cur.fetchall()

    projects = [
        {
            "slug": row[0],
            "name": row[1],
            "repo_path": row[2],
            "ticket_prefix": row[3],
            "count": counts.get(row[0], 0),
        }
        for row in registry
    ]
    return {
        "projects": projects,
        # Echoed rather than baked into the field name: calling it `count_24h`
        # would be a lie the moment someone passes ?window=7d.
        "window_hours": window_hours,
    }


def known_project_slugs() -> set[str]:
    with cursor() as cur:
        cur.execute("SELECT slug FROM projects")
        return {row[0] for row in cur.fetchall()}


def by_project(from_time: str | None = None, to_time: str | None = None) -> list[dict[str, Any]]:
    sql = f"""
    SELECT {PROJECT_LABEL_EXPR} AS project, COUNT(*) AS count
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
        # A registry slug, resolved through project_dir_map -- not a substring
        # of a basename. The old `PROJECT_EXPR ILIKE '%...%'` was wrong in both
        # directions: it matched `intelliforia` against `intelliforia-mobile`,
        # and it missed every worktree whose basename does not contain the
        # project name. It was also non-sargable, so it cost a full scan
        # (measured 8.45 s; the planner estimated 37 rows against 118,745).
        #
        # The subquery returns a few dozen directories at most, which the
        # work_dir expression can be compared against directly.
        where.append(
            f"{WORK_DIR_EXPR} IN (SELECT work_dir FROM project_dir_map WHERE slug = %s)"
        )
        params.append(kwargs["project"])
    if kwargs.get("q") and kwargs["q"].strip():
        clause, search_params = _search_clause(kwargs["q"])
        where.append(clause)
        params.extend(search_params)

    return where, params


def _search_clause(q: str) -> tuple[str, list[Any]]:
    """Build the WHERE fragment for a free-text `q`.

    Raises SearchError on a term the trigram index cannot serve; main.py turns
    that into a 400 rather than letting it become a minutes-long scan.
    """
    text = q.strip()

    # A bare UUID is an id, not prose. Pasting an event id or a session
    # (correlation) id into the search box is the single most common thing an
    # operator does with one, and both columns are btree-indexed -- so answer it
    # exactly and instantly instead of trigram-scanning for a 36-character
    # string that `search_text` deliberately does not even contain (UUID
    # trigrams are near-uniform hex and would bloat the index for nothing).
    event_id = _as_uuid(text)
    if event_id:
        return "(id = %s::uuid OR correlationid = %s::uuid)", [event_id, event_id]

    # Whitespace separates terms that must ALL match, rather than one literal
    # phrase: `search_text` is a concatenation of fields, so `candystore Edit`
    # meaning "an Edit tool call in candystore" is both the useful reading and
    # the only one that can match across two fields.
    terms = text.split()
    short = sorted({term for term in terms if len(term) < SEARCH_MIN_TERM})
    if short:
        raise SearchError(
            f"search terms must be at least {SEARCH_MIN_TERM} characters: "
            f"{', '.join(repr(term) for term in short)}"
        )

    clause = " AND ".join(["search_text ILIKE %s"] * len(terms))
    params = [f"%{term.translate(_LIKE_ESCAPES)}%" for term in terms]
    return f"({clause})", params


def applied_window(
    from_time: str | None,
    to_time: str | None,
    *,
    correlationid: str | None = None,
    q: str | None = None,
    hours: int = DEFAULT_WINDOW_HOURS,
    now: datetime | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the time window an interactive browse should actually run over.

    Returns the (from, to) to hand to `list_events`. Pure: no DB, so the policy
    is testable without a database (see tests/test_window.py).

    Exactly one thing is ever invented: a missing `from`. `to` is passed
    through untouched, because a floor is what makes a query cheap and a
    ceiling is not -- `?to=2026-01-01` with no floor is still a scan of every
    row before that date, which is the case this function exists to prevent.

    The invented floor is anchored to `to_time` when there is one, not to
    wall-clock now. A now-relative floor under `?to=2026-01-01` would describe
    a window that ends eight months before it starts, return nothing, and read
    as "no events" instead of "you asked for an empty range".

    An explicit `from` is never overridden -- naming a floor is a statement
    that you want that floor.

    Point lookups are exempt, and this is the part that is easy to get wrong.
    `correlationid` and a bare-UUID `q` both resolve through a btree index to a
    handful of rows, and they are how the UI answers its two most common
    questions: pasting an event or session id into the search box, and
    following a session drill-down. Windowing them would break "paste an id,
    find the event" for anything older than a day -- which, on an audit trail,
    is nearly everything.
    """
    if from_time:
        return from_time, to_time
    if correlationid or (q and _as_uuid(q.strip())):
        return None, to_time
    end = _parse_time(to_time) or (now or datetime.now(UTC))
    return (end - timedelta(hours=hours)).isoformat(), to_time


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _preview_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    raw = row[9]
    summary = summarize(raw, row[10])
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


def _session_project(events: list[dict[str, Any]]) -> str | None:
    """The session's project: the first resolved slug among its events.

    Scanning rather than reading events[0] because a session can legitimately
    start outside a registered project and move into one -- 472 of 2,204
    measured sessions span more than one directory. `None` when nothing in the
    session resolves.
    """
    for event in events:
        if event.get("project"):
            return str(event["project"])
    return None


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


