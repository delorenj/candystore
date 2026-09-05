"""Provenance classification (candystore.query.PROVENANCE_EXPR) -- CANDYS-46.

The classifier lives in SQL because the feed clicks a dot to filter and the
timeline strip needs `GROUP BY class`. That makes it hard to unit-test, and it
makes a silent divergence cheap: SQL that quietly stops matching does not raise,
it just returns different rows. So the SQL is asserted against an independent
Python reimplementation over the same envelopes -- the discipline
`003_search.sql` established for the generated search column.
"""

from __future__ import annotations

import pytest

from candystore.db import insert_event
from candystore.query import PROVENANCE_CLASSES, list_events
from candystore.summarize import summarize

# One envelope per branch of the CASE, each modelled on a real producer.
# `expected` is what BOTH implementations must say.
CASES = [
    (
        "subagent",
        # codex-cli stamps payload.agent_id on delegated work. Checked first
        # because these are agent-CLI events too -- the `agent` branch would
        # otherwise swallow all 15,415 of them.
        {
            "producer": "codex-cli",
            "source": "urn:33god:agent:codex-cli",
            "actor": {"type": "agent_cli", "cli": "codex"},
            "data": {
                "payload": {
                    "agent_id": "01a06bed-5207-7093-b211-63abdb201dd5",
                    "agent_type": "code-reviewer",
                    "session_id": "01a03b88-4ea9-7de2-9751-cda367c59444",
                }
            },
        },
    ),
    (
        "subagent",
        # agent_type='default' is an UNNAMED subagent, not the orchestrator:
        # these rows carry hook SubagentStop and agent_id never equals
        # session_id (0 of 22,284 measured). CANDYS-50.
        {
            "producer": "codex-cli",
            "source": "urn:33god:agent:codex-cli",
            "actor": {"type": "agent_cli", "cli": "codex"},
            "data": {
                "hook": "SubagentStop",
                "payload": {
                    "agent_id": "01a068f3-0f0b-7710-a8e7-f0ce69d9f84a",
                    "agent_type": "default",
                    "session_id": "01a06854-5179-7d72-be53-dcff47ae9885",
                },
            },
        },
    ),
    (
        "pm_agent",
        {
            "producer": "hermes-agent:33god-pm",
            "source": "hermes://agent/33god-pm",
            "actor": {"type": "agent_cli", "cli": "claude"},
            "data": {},
        },
    ),
    (
        "agent",
        # Bare `hermes-agent` is the fleet agent doing ordinary work. The colon
        # is the entire difference from the case above.
        {
            "producer": "hermes-agent",
            "source": "urn:33god:agent:hermes",
            "actor": {"type": "agent_cli", "cli": "claude"},
            "data": {},
        },
    ),
    (
        "ticket_webhook",
        # Arrives through n8n, but its provenance is the board -- so it must be
        # matched before the n8n branch or every ticket event reads as generic
        # workflow traffic.
        {
            "producer": "n8n-plane-webhook",
            "source": "urn:33god:integration:n8n:plane-webhook",
            "actor": {"type": "ticket_provider", "provider": "plane"},
            "data": {"ticket_key": "CANDYS-46"},
        },
    ),
    (
        "n8n_workflow",
        {
            "producer": "n8n",
            "source": "urn:33god:service:n8n-inbox-transcribe",
            "actor": {"type": "service"},
            "data": {},
        },
    ),
    (
        "agent",
        {
            "producer": "claude-code",
            "source": "urn:33god:agent:claude-code",
            "actor": {"type": "agent_cli", "cli": "claude"},
            "data": {},
        },
    ),
    (
        "service",
        {
            "producer": "traefik-deathwatch",
            "source": "urn:33god:service:traefik-deathwatch",
            "actor": {"type": "service"},
            "data": {},
        },
    ),
    (
        "service",
        {
            "producer": "activity-report",
            "source": "urn:33god:skill:activity-report",
            "actor": {},
            "data": {},
        },
    ),
    (
        "operator",
        {
            "producer": "wax",
            "source": "//big-chungus/wax",
            "actor": {"type": "operator"},
            "data": {},
        },
    ),
    (
        "other",
        # A non-conforming source with no actor type. `other` must stay
        # reachable: if it ever starts growing, a producer changed shape.
        {
            "producer": "t",
            "source": "urn:t",
            "actor": {},
            "data": {},
        },
    ),
]


def classify(env: dict) -> str:
    """Independent Python reimplementation of PROVENANCE_EXPR.

    Deliberately a separate reading of the same rules rather than a shared
    helper -- a shared helper would agree with the SQL by construction and
    prove nothing.
    """
    data = env.get("data") or {}
    payload = data.get("payload")
    actor = env.get("actor") or {}
    actor_type = actor.get("type") or ""
    producer = env.get("producer") or ""
    source = env.get("source") or ""

    if isinstance(payload, dict) and "agent_id" in payload:
        return "subagent"
    if producer.startswith("hermes-agent:"):
        return "pm_agent"
    if actor_type == "ticket_provider":
        return "ticket_webhook"
    if producer == "n8n" or source.startswith("urn:33god:integration:n8n:"):
        return "n8n_workflow"
    if actor_type == "agent_cli":
        return "agent"
    if (
        actor_type == "service"
        or source.startswith("urn:33god:service:")
        or source.startswith("urn:33god:skill:")
    ):
        return "service"
    if actor_type == "operator":
        return "operator"
    return "other"


@pytest.mark.parametrize(
    ("index", "expected", "overrides"),
    [(i, expected, overrides) for i, (expected, overrides) in enumerate(CASES)],
    ids=[f"{expected}-{i}" for i, (expected, _) in enumerate(CASES)],
)
def test_sql_and_python_agree_per_branch(db, sample_event, index, expected, overrides):
    env = sample_event(id=f"550e8400-e29b-41d4-a716-4466554bb{index:03d}", **overrides)
    assert insert_event(env) is True

    assert classify(env) == expected, "the Python reference disagrees with the case"

    listed = list_events(limit=10, from_time="2000-01-01T00:00:00Z")
    row = next(event for event in listed["events"] if event["id"] == env["id"])
    assert row["class"] == expected, "SQL disagrees with the Python reference"
    # The summary carries the same class, rather than a second derivation.
    assert row["summary"]["class"] == expected


def test_every_branch_is_reachable_and_declared():
    """A class the CASE can emit but PROVENANCE_CLASSES does not describe would
    render as an undefined colour; one that is described but unreachable would
    be a permanently empty dot. Neither is acceptable."""
    produced = {expected for expected, _ in CASES}
    assert produced <= set(PROVENANCE_CLASSES), "the CASE emits an undeclared class"
    assert set(PROVENANCE_CLASSES) - produced == set(), "a declared class has no case"


def test_class_filter_ors_within_the_facet(db, sample_event):
    """Multi-select within one facet is OR; across facets it is AND. That rule
    is the substitute for a query grammar, so it is asserted rather than assumed."""
    agent = sample_event(
        id="550e8400-e29b-41d4-a716-4466554bc001",
        producer="claude-code",
        actor={"type": "agent_cli", "cli": "claude"},
        data={},
    )
    sub = sample_event(
        id="550e8400-e29b-41d4-a716-4466554bc002",
        producer="codex-cli",
        actor={"type": "agent_cli", "cli": "codex"},
        data={"payload": {"agent_id": "a", "agent_type": "worker"}},
    )
    service = sample_event(
        id="550e8400-e29b-41d4-a716-4466554bc003",
        producer="tiller",
        source="urn:33god:service:tiller",
        actor={"type": "service"},
        data={},
    )
    for env in (agent, sub, service):
        assert insert_event(env) is True

    early = {"from_time": "2000-01-01T00:00:00Z", "limit": 50}

    def ids(**kwargs):
        return {event["id"] for event in list_events(**early, **kwargs)["events"]}

    assert ids(provenance="subagent") == {sub["id"]}
    assert ids(provenance="agent,subagent") == {agent["id"], sub["id"]}
    assert ids(provenance="agent, subagent ") == {agent["id"], sub["id"]}, "whitespace tolerated"
    # AND across facets: a class that is real but paired with a non-matching
    # cli returns nothing rather than falling back to the broader set.
    assert ids(provenance="subagent", cli="claude") == set()
    assert ids(provenance="service") == {service["id"]}


def test_summarize_reads_the_class_rather_than_recomputing_it(sample_event):
    """summarize() runs per row in Python after the rows return, so it can never
    serve a WHERE or a GROUP BY. It takes the class rather than deriving one,
    which is what stops the dot you click and the dot you see from drifting."""
    env = sample_event(producer="codex-cli", data={"payload": {"agent_id": "a"}})
    # Whatever it is handed is what it reports -- there is no second opinion.
    assert summarize(env, None, "subagent")["class"] == "subagent"
    assert summarize(env)["class"] is None
