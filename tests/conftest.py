from __future__ import annotations

import copy
import os
import uuid

import pytest


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    url = os.environ.get("CANDYSTORE_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("set CANDYSTORE_TEST_DATABASE_URL or DATABASE_URL for Postgres-backed tests")
    monkeypatch.setenv("DATABASE_URL", url)

    from candystore.db import cursor, init_schema

    try:
        init_schema()
        with cursor() as cur:
            cur.execute("TRUNCATE events")
    except Exception as exc:
        pytest.skip(f"Postgres unavailable for candystore tests: {exc}")

    yield

    with cursor() as cur:
        cur.execute("TRUNCATE events")


@pytest.fixture
def sample_event():
    def make_event(**overrides):
        event_id = overrides.pop("id", str(uuid.uuid4()))
        correlationid = overrides.pop("correlationid", str(uuid.uuid4()))
        data = {
            "session_id": correlationid,
            "project": "candystore",
            "working_directory": "/home/delorenj/code/33GOD/candystore",
            "git_branch": "main",
            "duration_seconds": 95,
            "total_turns": 3,
            "tools_used": ["apply_patch", "pytest"],
            "final_status": "success",
        }
        data.update(overrides.pop("data", {}))
        actor = {"cli": "claude", "provider": "anthropic"}
        actor.update(overrides.pop("actor", {}))
        env = {
            "id": event_id,
            "specversion": "1.0",
            "source": "urn:33god:test",
            "type": "bloodbank.v1.cli.session.ended",
            "time": "2026-05-24T16:00:00Z",
            "producer": "test-producer",
            "service": "test-service",
            "domain": "cli",
            "kind": "event",
            "correlationid": correlationid,
            "causationid": str(uuid.uuid4()),
            "actor": actor,
            "data": data,
        }
        env.update(overrides)
        return copy.deepcopy(env)

    return make_event
