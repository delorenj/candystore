from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg2.extras import Json

from candystore.db import cursor

SNAPSHOT_TYPE = "bloodbank.v1.lifecycle.snapshot.updated"
INTENT_REPLY_TYPE = "bloodbank.v1.lifecycle.intent.submit"
INTENT_REPLY_SUBJECT = "bloodbank.rpy.v1.lifecycle.intent.submit"


def project_lifecycle_envelope(cur: Any, envelope: dict[str, Any]) -> None:
    """Project one canonical Lifecycle publication in the event transaction.

    A receipt makes replay idempotent even when an event predates this
    projection migration. Snapshot replacement is version ordered; an older
    delivery is retained in the audit trail but cannot roll the read model back.
    """

    event_type = str(envelope.get("type", ""))
    subject = str(envelope.get("subject", ""))
    if event_type == SNAPSHOT_TYPE:
        _project_snapshot(cur, envelope)
    elif event_type == INTENT_REPLY_TYPE and subject == INTENT_REPLY_SUBJECT:
        _project_verdict(cur, envelope)


def get_lifecycle_projection(
    lifecycle_id: str,
    *,
    as_of: datetime | None = None,
    verdict_limit: int = 50,
) -> dict[str, Any]:
    as_of = _utc(as_of or datetime.now(UTC))
    with cursor() as cur:
        cur.execute(
            """
            SELECT lifecycle_id, repo, spec_version, state_version,
                   previous_state_version, status, health, phase,
                   progress_percent, state_fingerprint, legal_frontier,
                   obligations, blockers, gates, capabilities, provenance,
                   freshness, publication, source_event_id, source_event_type,
                   source_event_time, source_ordering_key, projected_at
            FROM lifecycle_projections
            WHERE lifecycle_id = %s
            """,
            (lifecycle_id,),
        )
        row = cur.fetchone()
        verdicts = _fetch_verdicts(cur, lifecycle_id, verdict_limit)
    if row is None:
        return _unknown_projection(lifecycle_id, as_of, verdicts)
    return _projection_from_row(row, as_of, verdicts)


def list_lifecycle_projections(
    *,
    as_of: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    as_of = _utc(as_of or datetime.now(UTC))
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM lifecycle_projections")
        total = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT lifecycle_id, repo, spec_version, state_version,
                   previous_state_version, status, health, phase,
                   progress_percent, state_fingerprint, legal_frontier,
                   obligations, blockers, gates, capabilities, provenance,
                   freshness, publication, source_event_id, source_event_type,
                   source_event_time, source_ordering_key, projected_at
            FROM lifecycle_projections
            ORDER BY lifecycle_id
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = cur.fetchall()
        items = [
            _projection_from_row(row, as_of, _fetch_verdicts(cur, str(row[0]), 10)) for row in rows
        ]
    return {"lifecycles": items, "total": total, "limit": limit, "offset": offset}


def _project_snapshot(cur: Any, envelope: dict[str, Any]) -> None:
    data = _data(envelope)
    lifecycle_id = _required_text(data, "lifecycle_id")
    state_version = _required_int(data, "state_version")
    if not _claim_receipt(cur, envelope, lifecycle_id, state_version):
        return

    state = _required_object(data, "state")
    publication = _required_object(data, "publication")
    event_sequence = _required_int(publication, "event_sequence")
    source_event_id = _required_text(envelope, "id")
    source_event_time = _required_text(envelope, "time")
    source_ordering_key = _required_text(envelope, "ordering_key")
    params = (
        lifecycle_id,
        _required_text(data, "repo"),
        _required_int(data, "spec_version"),
        state_version,
        data.get("previous_state_version"),
        _required_text(state, "status"),
        _required_text(state, "health"),
        state.get("phase"),
        state.get("progress_percent", 0),
        state.get("state_fingerprint") or state.get("fingerprint"),
        Json(_required_list(data, "legal_frontier")),
        Json(_required_list(data, "obligations")),
        Json(_required_list(data, "blockers")),
        Json(_required_list(data, "gates")),
        Json(_required_list(data, "capabilities")),
        Json(_required_object(data, "provenance")),
        Json(_required_object(data, "freshness")),
        Json(publication),
        source_event_id,
        _required_text(envelope, "type"),
        source_event_time,
        source_ordering_key,
        event_sequence,
    )
    cur.execute(
        """
        INSERT INTO lifecycle_projections (
            lifecycle_id, repo, spec_version, state_version,
            previous_state_version, status, health, phase, progress_percent,
            state_fingerprint, legal_frontier, obligations, blockers, gates,
            capabilities, provenance, freshness, publication, source_event_id,
            source_event_type, source_event_time, source_ordering_key
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (lifecycle_id) DO UPDATE SET
            repo = EXCLUDED.repo,
            spec_version = EXCLUDED.spec_version,
            state_version = EXCLUDED.state_version,
            previous_state_version = EXCLUDED.previous_state_version,
            status = EXCLUDED.status,
            health = EXCLUDED.health,
            phase = EXCLUDED.phase,
            progress_percent = EXCLUDED.progress_percent,
            state_fingerprint = EXCLUDED.state_fingerprint,
            legal_frontier = EXCLUDED.legal_frontier,
            obligations = EXCLUDED.obligations,
            blockers = EXCLUDED.blockers,
            gates = EXCLUDED.gates,
            capabilities = EXCLUDED.capabilities,
            provenance = EXCLUDED.provenance,
            freshness = EXCLUDED.freshness,
            publication = EXCLUDED.publication,
            source_event_id = EXCLUDED.source_event_id,
            source_event_type = EXCLUDED.source_event_type,
            source_event_time = EXCLUDED.source_event_time,
            source_ordering_key = EXCLUDED.source_ordering_key,
            projected_at = NOW()
        WHERE EXCLUDED.state_version > lifecycle_projections.state_version
           OR (
               EXCLUDED.state_version = lifecycle_projections.state_version
               AND %s > COALESCE(
                   (lifecycle_projections.publication->>'event_sequence')::BIGINT,
                   0
               )
           )
        """,
        params,
    )


def _project_verdict(cur: Any, envelope: dict[str, Any]) -> None:
    data = _data(envelope)
    lifecycle_id = _required_text(data, "lifecycle_id")
    observed_version = _required_int(data, "observed_state_version")
    if not _claim_receipt(cur, envelope, lifecycle_id, observed_version):
        return
    cur.execute(
        """
        INSERT INTO lifecycle_command_verdicts (
            reply_event_id, lifecycle_id, repo, command_event_id, command_id,
            idempotency_key, expected_state_version, observed_state_version,
            verdict, mutated, resulting_state_version, applied_event_id,
            capability_id, reason_code, correlation_id, causation_id,
            responded_at, source
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT DO NOTHING
        """,
        (
            _required_text(envelope, "id"),
            lifecycle_id,
            _required_text(data, "repo"),
            _required_text(data, "reply_to_command_event_id"),
            _required_text(data, "command_id"),
            _required_text(data, "idempotency_key"),
            _required_int(data, "expected_state_version"),
            observed_version,
            _required_text(data, "verdict"),
            bool(data.get("mutated")),
            data.get("resulting_state_version"),
            data.get("applied_event_id"),
            data.get("capability_id"),
            _required_text(data, "reason_code"),
            envelope.get("correlationid"),
            envelope.get("causationid"),
            _required_text(data, "responded_at"),
            Json(envelope),
        ),
    )


def _claim_receipt(
    cur: Any,
    envelope: dict[str, Any],
    lifecycle_id: str,
    state_version: int,
) -> bool:
    cur.execute(
        """
        INSERT INTO lifecycle_projection_receipts
            (event_id, lifecycle_id, event_type, state_version)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        (
            _required_text(envelope, "id"),
            lifecycle_id,
            _required_text(envelope, "type"),
            state_version,
        ),
    )
    return cur.fetchone() is not None


def _fetch_verdicts(cur: Any, lifecycle_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT reply_event_id, command_event_id, command_id, idempotency_key,
               expected_state_version, observed_state_version, verdict, mutated,
               resulting_state_version, applied_event_id, capability_id,
               reason_code, correlation_id, causation_id, responded_at
        FROM lifecycle_command_verdicts
        WHERE lifecycle_id = %s
        ORDER BY responded_at DESC, reply_event_id DESC
        LIMIT %s
        """,
        (lifecycle_id, max(1, min(int(limit), 200))),
    )
    return [
        {
            "reply_event_id": str(row[0]),
            "command_event_id": str(row[1]),
            "command_id": str(row[2]),
            "idempotency_key": row[3],
            "expected_state_version": row[4],
            "observed_state_version": row[5],
            "verdict": row[6],
            "mutated": row[7],
            "resulting_state_version": row[8],
            "applied_event_id": str(row[9]) if row[9] else None,
            "capability_id": row[10],
            "reason_code": row[11],
            "correlation_id": str(row[12]) if row[12] else None,
            "causation_id": str(row[13]) if row[13] else None,
            "responded_at": _iso(row[14]),
        }
        for row in cur.fetchall()
    ]


def _projection_from_row(
    row: tuple[Any, ...],
    as_of: datetime,
    verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    freshness = row[16] or {}
    stale = _is_stale(freshness, as_of)
    authority_state = {
        "status": row[5],
        "health": row[6],
        "phase": row[7],
        "progress_percent": float(row[8]),
        "fingerprint": row[9],
    }
    return {
        "lifecycle_id": row[0],
        "repo": row[1],
        "spec_version": row[2],
        "state_version": row[3],
        "previous_state_version": row[4],
        "status": authority_state["status"],
        "health": "degraded" if stale else authority_state["health"],
        "phase": authority_state["phase"],
        "progress_percent": authority_state["progress_percent"],
        "fingerprint": authority_state["fingerprint"],
        "projection_status": "stale" if stale else "current",
        "authority_state": authority_state,
        "legal_frontier": row[10] or [],
        "obligations": row[11] or [],
        "blockers": row[12] or [],
        "gates": row[13] or [],
        "capabilities": row[14] or [],
        "provenance": row[15] or {},
        "freshness": {
            **freshness,
            "status": "stale" if stale else freshness.get("status", "fresh"),
            "as_of": _iso(as_of),
        },
        "publication": row[17] or {},
        "source": {
            "event_id": str(row[18]),
            "event_type": row[19],
            "event_time": _iso(row[20]),
            "ordering_key": row[21],
            "projected_at": _iso(row[22]),
        },
        "command_verdicts": verdicts,
    }


def _unknown_projection(
    lifecycle_id: str,
    as_of: datetime,
    verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "lifecycle_id": lifecycle_id,
        "repo": None,
        "spec_version": None,
        "state_version": None,
        "previous_state_version": None,
        "status": "unknown",
        "health": "degraded",
        "phase": None,
        "progress_percent": None,
        "fingerprint": None,
        "projection_status": "missing",
        "authority_state": None,
        "legal_frontier": [],
        "obligations": [],
        "blockers": [],
        "gates": [],
        "capabilities": [],
        "provenance": None,
        "freshness": {"status": "unknown", "as_of": _iso(as_of)},
        "publication": None,
        "source": None,
        "command_verdicts": verdicts,
    }


def _is_stale(freshness: dict[str, Any], as_of: datetime) -> bool:
    if freshness.get("status") != "fresh":
        return True
    observed = _parse_time(freshness.get("observed_through"))
    max_age = freshness.get("max_age_seconds")
    if observed is None or not isinstance(max_age, int):
        return True
    return as_of > observed + timedelta(seconds=max_age)


def _data(envelope: dict[str, Any]) -> dict[str, Any]:
    return _required_object(envelope, "data")


def _required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise ValueError(f"Lifecycle publication field {key!r} must be an object")
    return result


def _required_list(value: dict[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise ValueError(f"Lifecycle publication field {key!r} must be an array")
    return result


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"Lifecycle publication field {key!r} must be non-empty text")
    return result


def _required_int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 1:
        raise ValueError(f"Lifecycle publication field {key!r} must be an integer >= 1")
    return result


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    return str(value)
