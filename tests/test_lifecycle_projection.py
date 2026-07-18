from __future__ import annotations

import http.client
import json
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from candystore.db import cursor, insert_event
from candystore.lifecycle import get_lifecycle_projection
from candystore.main import Handler
from tests.schema_validation import validate_with_bloodbank

LIFECYCLE_ID = "11111111-1111-4111-8111-111111111111"
CORRELATION_ID = "22222222-2222-4222-8222-222222222222"
CAUSATION_ID = "33333333-3333-4333-8333-333333333333"
COMMAND_ID = "44444444-4444-4444-8444-444444444444"
COMMAND_EVENT_ID = "55555555-5555-4555-8555-555555555555"


def test_snapshot_projection_is_replay_safe_and_version_ordered(db):
    current = snapshot(
        version=2,
        sequence=4,
        status="active",
        obligations=[obligation("77777777-7777-4777-8777-777777777777")],
    )
    assert insert_event(current) is True
    assert insert_event(current) is False

    older = snapshot(version=1, sequence=2, status="planned")
    assert insert_event(older) is True

    result = get_lifecycle_projection(
        LIFECYCLE_ID,
        as_of=datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
    )
    assert result["state_version"] == 2
    assert result["status"] == "active"
    assert result["authority_state"]["health"] == "nominal"
    assert result["provenance"]["authority"] == "delorenj/lifecycle"
    assert result["source"]["event_id"] == current["id"]
    assert result["source"]["authority_source"] == "urn:33god:service:lifecycle"
    assert result["source"]["subject"] == "bloodbank.evt.v1.lifecycle.snapshot.updated"
    assert result["source"]["correlation_id"] == CORRELATION_ID
    assert result["source"]["causation_id"] == CAUSATION_ID
    assert result["source"]["actor"]["agent_id"] == "delorenj.lifecycle"
    assert result["capabilities"][0]["capability_version"] == 7
    assert result["obligations"][0]["obligation_instance_id"] == (
        "77777777-7777-4777-8777-777777777777"
    )

    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE lifecycle_id = %s",
            (LIFECYCLE_ID,),
        )
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(*) FROM lifecycle_projections")
        assert cur.fetchone()[0] == 1


def test_same_version_only_accepts_later_publication_sequence(db):
    first = snapshot(version=3, sequence=6, phase="build")
    assert insert_event(first)
    later = snapshot(version=3, sequence=8, phase="verify")
    assert insert_event(later)
    delayed = snapshot(version=3, sequence=7, phase="delayed")
    assert insert_event(delayed)

    result = get_lifecycle_projection(
        LIFECYCLE_ID,
        as_of=datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
    )
    assert result["phase"] == "verify"
    assert result["publication"]["event_sequence"] == 8


def test_freshness_and_missing_projection_never_render_healthy_empty(db):
    assert insert_event(snapshot(version=1, sequence=2))

    fresh = get_lifecycle_projection(
        LIFECYCLE_ID,
        as_of=datetime(2026, 7, 18, 12, 9, tzinfo=UTC),
    )
    assert fresh["projection_status"] == "current"
    assert fresh["health"] == "nominal"
    assert fresh["freshness"]["status"] == "fresh"

    stale = get_lifecycle_projection(
        LIFECYCLE_ID,
        as_of=datetime(2026, 7, 18, 12, 11, tzinfo=UTC),
    )
    assert stale["projection_status"] == "stale"
    assert stale["health"] == "degraded"
    assert stale["authority_state"]["health"] == "nominal"
    assert stale["freshness"]["status"] == "stale"

    missing = get_lifecycle_projection(
        "missing-lifecycle",
        as_of=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )
    assert missing["projection_status"] == "missing"
    assert missing["status"] == "unknown"
    assert missing["health"] == "degraded"
    assert missing["authority_state"] is None


def test_stable_command_verdicts_are_projected_without_state_mutation(db):
    assert insert_event(snapshot(version=1, sequence=2))
    assert insert_event(reply(verdict="stale", observed=1))
    assert insert_event(reply(verdict="stale", observed=1)) is False

    result = get_lifecycle_projection(LIFECYCLE_ID)
    assert result["state_version"] == 1
    assert len(result["command_verdicts"]) == 1
    verdict = result["command_verdicts"][0]
    assert verdict["verdict"] == "stale"
    assert verdict["mutated"] is False
    assert verdict["reason_code"] == "EXPECTED_STATE_VERSION_MISMATCH"
    assert verdict["command_id"] == COMMAND_ID


def test_invalid_authority_candidate_is_audited_but_excluded_from_projection(db):
    invalid = snapshot(version=1, sequence=2)
    del invalid["data"]["provenance"]

    assert insert_event(invalid) is True

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (invalid["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (invalid["id"],),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM lifecycle_projections")
        assert cur.fetchone()[0] == 0


def test_operational_projection_failure_rolls_back_audit_and_receipt(db, monkeypatch):
    canonical = snapshot(version=1, sequence=2)

    def fail_projection(cur, envelope):
        raise RuntimeError("injected projection write failure")

    monkeypatch.setattr("candystore.lifecycle._project_snapshot", fail_projection)
    with pytest.raises(RuntimeError, match="injected projection write failure"):
        insert_event(canonical)

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (canonical["id"],))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (canonical["id"],),
        )
        assert cur.fetchone()[0] == 0


def test_preexisting_audit_row_survives_projection_failure_and_retries_canonically(db, monkeypatch):
    canonical = snapshot(version=1, sequence=2)
    assert insert_event(canonical)
    with cursor() as cur:
        cur.execute("DELETE FROM lifecycle_projections WHERE lifecycle_id = %s", (LIFECYCLE_ID,))
        cur.execute(
            "DELETE FROM lifecycle_projection_receipts WHERE event_id = %s",
            (canonical["id"],),
        )

    with monkeypatch.context() as scoped:

        def fail_retry(cur, envelope):
            raise RuntimeError("retryable failure")

        scoped.setattr("candystore.lifecycle._project_snapshot", fail_retry)
        with pytest.raises(RuntimeError, match="retryable failure"):
            insert_event(canonical)

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (canonical["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (canonical["id"],),
        )
        assert cur.fetchone()[0] == 0

    assert insert_event(canonical) is False
    result = get_lifecycle_projection(LIFECYCLE_ID)
    assert result["source"]["event_id"] == canonical["id"]
    assert result["state_version"] == 1


def test_spoofed_snapshot_is_audited_but_cannot_create_or_replace_projection(db):
    canonical = snapshot(version=2, sequence=4, status="active", phase="build")
    assert insert_event(canonical)

    spoof = snapshot(version=99, sequence=999, status="completed", phase="spoofed")
    spoof["source"] = "urn:attacker"
    spoof["subject"] = "evil.subject"
    spoof["producer"] = "attacker"
    spoof["service"] = "attacker"
    spoof["actor"] = {"type": "service", "agent_id": "attacker"}
    spoof["data"]["provenance"]["authority"] = "attacker"
    assert insert_event(spoof)

    result = get_lifecycle_projection(LIFECYCLE_ID)
    assert result["state_version"] == 2
    assert result["status"] == "active"
    assert result["phase"] == "build"
    assert result["source"]["event_id"] == canonical["id"]
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (spoof["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (spoof["id"],),
        )
        assert cur.fetchone()[0] == 0


def test_spoofed_reply_is_audited_but_cannot_create_or_replace_verdict(db):
    assert insert_event(snapshot(version=2, sequence=4, status="active"))
    canonical_reply = reply(verdict="stale", observed=2)
    assert insert_event(canonical_reply)

    spoof = reply(verdict="illegal", observed=2)
    spoof["id"] = str(uuid.uuid4())
    spoof["data"]["command_id"] = str(uuid.uuid4())
    spoof["data"]["reply_to_command_event_id"] = str(uuid.uuid4())
    spoof["causationid"] = spoof["data"]["reply_to_command_event_id"]
    spoof["source"] = "urn:attacker"
    spoof["subject"] = "evil.subject"
    spoof["producer"] = "attacker"
    spoof["service"] = "attacker"
    spoof["actor"] = {"type": "service", "agent_id": "attacker"}
    assert insert_event(spoof)

    result = get_lifecycle_projection(LIFECYCLE_ID)
    assert len(result["command_verdicts"]) == 1
    assert result["command_verdicts"][0]["reply_event_id"] == canonical_reply["id"]
    assert result["command_verdicts"][0]["verdict"] == "stale"
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (spoof["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (spoof["id"],),
        )
        assert cur.fetchone()[0] == 0


def test_conflicting_duplicate_projects_only_the_canonical_persisted_raw_row(db):
    canonical = snapshot(version=1, sequence=2, status="planned", phase="plan")
    assert insert_event(canonical) is True

    # Simulate an event persisted before the projection migration/catch-up.
    with cursor() as cur:
        cur.execute("DELETE FROM lifecycle_projections WHERE lifecycle_id = %s", (LIFECYCLE_ID,))
        cur.execute(
            "DELETE FROM lifecycle_projection_receipts WHERE event_id = %s",
            (canonical["id"],),
        )

    conflicting = deepcopy(canonical)
    conflicting["data"]["state_version"] = 99
    conflicting["data"]["previous_state_version"] = 98
    conflicting["data"]["state"]["status"] = "completed"
    conflicting["data"]["state"]["phase"] = "spoofed"
    conflicting["data"]["capabilities"][0]["capability_version"] = 999
    conflicting["data"]["publication"]["aggregate_version"] = 99
    conflicting["data"]["publication"]["event_sequence"] = 999

    assert insert_event(conflicting) is False
    result = get_lifecycle_projection(
        LIFECYCLE_ID,
        as_of=datetime(2026, 7, 18, 12, 5, tzinfo=UTC),
    )
    assert result["state_version"] == 1
    assert result["status"] == "planned"
    assert result["phase"] == "plan"
    assert result["capabilities"][0]["capability_version"] == 7
    assert result["publication"]["event_sequence"] == 2
    assert result["source"]["event_id"] == canonical["id"]

    with cursor() as cur:
        cur.execute("SELECT raw FROM events WHERE id = %s", (canonical["id"],))
        assert cur.fetchone()[0] == canonical
        cur.execute("SELECT COUNT(*) FROM events WHERE id = %s", (canonical["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT COUNT(*) FROM lifecycle_projection_receipts WHERE event_id = %s",
            (canonical["id"],),
        )
        assert cur.fetchone()[0] == 1


def test_http_surface_is_read_only_and_explicit_for_unknown(db):
    assert insert_event(snapshot(version=1, sequence=2))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        status, body = request(
            host,
            port,
            "GET",
            f"/lifecycles/{LIFECYCLE_ID}?as_of=2026-07-18T12:05:00Z",
        )
        assert status == 200
        assert body["state_version"] == 1
        assert body["source"]["ordering_key"] == f"lifecycle:{LIFECYCLE_ID}"

        status, body = request(host, port, "GET", "/lifecycles/not-observed")
        assert status == 200
        assert body["projection_status"] == "missing"

        status, body = request(
            host,
            port,
            "POST",
            f"/lifecycles/{LIFECYCLE_ID}/actions",
            {"intent": "transition"},
        )
        assert status == 404
        assert body is None
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_reply_component_is_durable_jetstream_consumer():
    component = (
        Path(__file__).resolve().parents[1] / "dapr-components" / "lifecycle-replies.yaml"
    ).read_text(encoding="utf-8")
    assert 'streamName\n      value: "BLOODBANK_COMMANDS"' in component
    assert 'durableName\n      value: "candystore-lifecycle-replies"' in component
    assert 'deliverPolicy\n      value: "all"' in component


def test_lifecycle_projection_fixtures_match_exact_local_bloodbank_schemas():
    validate_with_bloodbank(snapshot(version=2, sequence=4, status="active"))
    validate_with_bloodbank(reply(verdict="stale", observed=2))


def snapshot(
    *,
    version: int,
    sequence: int,
    status: str = "planned",
    phase: str | None = "plan",
    obligations: list[dict] | None = None,
) -> dict:
    event_id = str(uuid.uuid4())
    previous = None if version == 1 else version - 1
    target = "active" if status == "planned" else "waiting"
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": "urn:33god:service:lifecycle",
        "type": "bloodbank.v1.lifecycle.snapshot.updated",
        "subject": "bloodbank.evt.v1.lifecycle.snapshot.updated",
        "time": "2026-07-18T12:00:00Z",
        "datacontenttype": "application/json",
        "dataschema": ("apicurio://holyfields/bloodbank.v1.lifecycle.snapshot.updated/versions/3"),
        "correlationid": CORRELATION_ID,
        "causationid": CAUSATION_ID,
        "producer": "delorenj/lifecycle",
        "service": "lifecycle",
        "kind": "event",
        "domain": "lifecycle",
        "schemaref": "bloodbank.v1.lifecycle.snapshot.updated.v3",
        "ordering_key": f"lifecycle:{LIFECYCLE_ID}",
        "actor": {
            "type": "service",
            "agent_id": "delorenj.lifecycle",
            "instance": "test-authority",
        },
        "data": {
            "contract_version": 3,
            "lifecycle_id": LIFECYCLE_ID,
            "repo": "delorenj/33GOD",
            "spec_version": 1,
            "state_version": version,
            "previous_state_version": previous,
            "state": {
                "status": status,
                "health": "nominal",
                "phase": phase,
                "progress_percent": 25,
            },
            "legal_frontier": [
                {
                    "id": f"transition:{status}:{target}",
                    "kind": "state_transition",
                    "action": "transition",
                    "allowed": True,
                    "capability_required": "lifecycle.intent.submit",
                    "reason_code": "LEGAL_TRANSITION",
                    "expected_state_version": version,
                }
            ],
            "obligations": obligations or [],
            "blockers": [],
            "gates": [],
            "capabilities": [
                {
                    "capability_id": "momo-lifecycle",
                    "capability_version": 7,
                    "actor_id": "momo",
                    "scope": f"lifecycle:{LIFECYCLE_ID}",
                    "actions": ["lifecycle.intent.submit"],
                    "issued_at": "2026-07-18T11:00:00Z",
                    "expires_at": None,
                    "state_version": version,
                }
            ],
            "provenance": {
                "authority": "delorenj/lifecycle",
                "authority_instance": "test-authority",
                "reconciliation_id": str(uuid.uuid4()),
                "policy_version": "1.0.0",
                "source_observation_ids": [],
            },
            "freshness": {
                "observed_through": "2026-07-18T12:00:00Z",
                "evaluated_at": "2026-07-18T12:00:00Z",
                "status": "fresh",
                "max_age_seconds": 600,
            },
            "publication": {
                "outbox_id": sequence,
                "aggregate_id": LIFECYCLE_ID,
                "aggregate_version": version,
                "event_sequence": sequence,
            },
        },
    }


def obligation(instance_id: str) -> dict:
    return {
        "id": "independent-review",
        "obligation_instance_id": instance_id,
        "activated_at": "2026-07-18T11:55:00Z",
        "kind": "independent_review",
        "status": "pending",
        "description": "Complete an independent review",
        "skill_ref": {
            "name": "independent-review",
            "selector": "1.0.0",
        },
        "owner_id": "momo",
        "due_at": None,
        "source_observation_ids": [],
    }


def reply(*, verdict: str, observed: int) -> dict:
    return {
        "specversion": "1.0",
        "id": "66666666-6666-4666-8666-666666666666",
        "source": "urn:33god:service:lifecycle",
        "type": "bloodbank.v1.lifecycle.intent.submit",
        "subject": "bloodbank.rpy.v1.lifecycle.intent.submit",
        "time": "2026-07-18T12:01:00Z",
        "datacontenttype": "application/json",
        "dataschema": (
            "apicurio://holyfields/bloodbank.v1.lifecycle.intent.submit.reply/versions/1"
        ),
        "correlationid": CORRELATION_ID,
        "causationid": COMMAND_EVENT_ID,
        "producer": "delorenj/lifecycle",
        "service": "lifecycle",
        "kind": "reply",
        "domain": "lifecycle",
        "schemaref": "bloodbank.v1.lifecycle.intent.submit.reply.v1",
        "actor": {
            "type": "service",
            "agent_id": "delorenj.lifecycle",
            "instance": "test-authority",
        },
        "data": {
            "contract_version": 1,
            "lifecycle_id": LIFECYCLE_ID,
            "repo": "delorenj/33GOD",
            "reply_to_command_event_id": COMMAND_EVENT_ID,
            "command_id": COMMAND_ID,
            "idempotency_key": "momo:independent-review:1",
            "expected_state_version": 2,
            "observed_state_version": observed,
            "verdict": verdict,
            "mutated": False,
            "resulting_state_version": None,
            "applied_event_id": None,
            "capability_id": None,
            "reason_code": "EXPECTED_STATE_VERSION_MISMATCH",
            "responded_at": "2026-07-18T12:01:00Z",
        },
    }


def request(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: dict | None = None,
):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8")) if raw else None
