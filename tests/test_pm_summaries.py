"""The PM / ops summarizers (CANDYS-43).

By volume these families are a rounding error -- 1,644 of 166,723 rows over 7
days, and the deep-dive's "68% render generic" claim is long dead at 0.98%. But
volume-weighted coverage was the wrong metric: 100% of the lens a person
actually clicks lived in that residual, so every PM event rendered as a bare
type string while `ticket_key`, `phase` and `decision` sat unread one level
down.

Assertions run against the captured live corpus, so a producer changing shape
breaks a test rather than quietly degrading a row to its type name.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from candystore.summarize import SUMMARIZERS, canonical_type, summarize

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "envelopes.jsonl"

# The families a person clicks a lens to see. Every type a producer emits in
# one of these MUST have a summarizer -- that is the actual contract, and it is
# what the last test enforces as new types appear.
LENS_FAMILIES = (
    "repo.task.",
    "repo.decision.",
    "repo.board.",
    "repo.intake.",
    "repo.maintenance.",
    "system.process.",
    "project.activity.",
    "reporting.report.",
)


def _corpus() -> list[dict]:
    if not CORPUS.exists():
        pytest.skip(f"{CORPUS} absent; run tests/fixtures/capture_envelopes.py")
    with CORPUS.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _of_type(canonical: str) -> list[dict]:
    return [env for env in _corpus() if canonical_type(env.get("type")) == canonical]


UUIDISH = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def test_a_state_move_says_where_it_landed_and_nothing_more():
    """`previous_phase` is misnamed on the wire and there is no dependable
    "from" phase in this data. Measured over all 381 rows carrying it: 185 are
    a raw state UUID, 131 contain HTML, 136 exceed 40 chars (max 5,518), and
    the short remainder are values like `urgent`, `high`, `440000` -- it is the
    previous value of whichever field changed, not the previous phase.

    So a state move renders as an arrival, and never as a transition."""
    rows = [
        env
        for env in _of_type("bloodbank.repo.task.updated")
        if {"state", "state_id"} & set((env.get("data") or {}).get("changed_fields") or [])
    ]
    assert rows, "corpus has no state moves to check"
    for env in rows:
        title = summarize(env)["title"]
        assert "←" not in title, title
        assert "<" not in title, title
        assert not UUIDISH.search(title), title


def test_a_non_state_update_names_the_fields_that_changed():
    """A description or label edit is not a transition, and saying so beats
    printing the type."""
    rows = [
        env
        for env in _of_type("bloodbank.repo.task.updated")
        if (env.get("data") or {}).get("changed_fields")
        and not {"state", "state_id"} & set((env.get("data") or {}).get("changed_fields") or [])
    ]
    assert rows, "corpus has no non-state updates to check"
    for env in rows:
        title = summarize(env)["title"]
        assert title.endswith(" changed"), title
        assert "←" not in title and "→" not in title, title


def test_no_task_headline_ever_leaks_an_id_or_markup():
    """The blanket guarantee, over every task row in the corpus. This is the
    assertion that would have caught `Backlog <- <p>Filed by the ack wiring
    test...` before it reached a row."""
    rows = [
        env
        for env in _corpus()
        if canonical_type(env.get("type")).startswith("bloodbank.repo.task.")
    ]
    assert rows
    for env in rows:
        title = summarize(env)["title"]
        assert "<" not in title, title
        assert not UUIDISH.search(title), title
        assert len(title) < 200, title


def test_a_comment_leads_with_the_comment():
    """repo.task.appended is the one member of the family with NO ticket_key --
    measured 0 of 245, against 245 of 245 carrying a body. Keyed off the ticket
    ref it rendered as the useless "ticket - appended"."""
    rows = _of_type("bloodbank.repo.task.appended")
    assert rows
    for env in rows:
        title = summarize(env)["title"]
        assert title.startswith("Comment · "), title
        assert len(title) > len("Comment · ")
        # Plane sends HTML; a 28px row wants the sentence.
        assert "<p>" not in title and "</" not in title, title


def test_a_decision_names_who_decided_and_what():
    rows = _of_type("bloodbank.repo.decision.recorded")
    assert rows
    for env in rows:
        summary = summarize(env)
        assert summary["title"].strip()
        assert not summary["title"].startswith("bloodbank."), summary["title"]
    # The body must add something, not restate the decision the headline
    # already carries -- so it falls through to the reasoning.
    with_reasoning = [env for env in rows if (env.get("data") or {}).get("reasoning")]
    if with_reasoning:
        row = summarize(with_reasoning[0], None, "pm_agent")["row"]
        assert row["body"], "a decision with reasoning should show it"
        assert row["body"][:40] not in row["headline"]


def test_an_expected_container_exit_is_not_reported_as_a_failure():
    """227 rows a week are traefik-deathwatch restarting things on purpose. If
    every one reads as a failure you stop reading the class.

    `data.status` on these rows is the container's state AFTER the restart
    ("running"), which on an event titled "exited" is a contradiction -- so the
    row's status is the verdict instead."""
    rows = _of_type("bloodbank.system.process.exited")
    assert rows
    for env in rows:
        row = summarize(env, None, "service")["row"]
        expected = (env.get("data") or {}).get("expected_exit")
        assert row["status"] in {"expected", "unexpected"}, row["status"]
        assert row["ok"] is bool(expected)
        assert "exited" in row["headline"]
        assert row["headline"] != env.get("type")


def test_no_pm_or_ops_type_falls_through_to_generic():
    """The regression that matters as producers add types: a new
    `repo.task.*` or `system.process.*` must not silently render as its own
    type string inside a lens someone clicks."""
    missing = sorted(
        {
            canonical_type(env.get("type"))
            for env in _corpus()
            if any(
                canonical_type(env.get("type")).startswith(f"bloodbank.{family}")
                for family in LENS_FAMILIES
            )
        }
        - set(SUMMARIZERS)
    )
    assert not missing, f"no summarizer for {missing}"


def test_no_row_body_echoes_its_own_headline():
    """A body repeating the headline wastes the row's second line and reads as
    a rendering bug. Equality is not enough to catch it -- the two are
    truncated at different limits, so they differ by an ellipsis while saying
    the same thing."""
    offenders = []
    for env in _corpus():
        row = summarize(env, "candystore", "agent")["row"]
        body, headline = row["body"], row["headline"]
        if not body:
            continue
        fragment = body.rstrip("…").strip()[:48]
        if fragment and fragment in headline:
            offenders.append((headline, body))
    assert not offenders, offenders[:3]
