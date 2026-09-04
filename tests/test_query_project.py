"""`GET /projects` and `?project=<slug>` (CANDYS-35, CANDYS-36).

The picker lists the registry projection, and the filter is a slug resolved
through `project_dir_map` -- not a substring of a directory basename.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from candystore.db import insert_event
from candystore.main import Handler
from candystore.query import list_projects


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(host: str, port: int, path: str):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, (json.loads(raw.decode("utf-8")) if raw else None)


def _seed(sample_event) -> dict[str, str]:
    """Three events in three directories the map resolves differently."""
    events = {
        "candystore": sample_event(
            id="550e8400-e29b-41d4-a716-4466554aa001",
            data={"working_directory": "/home/delorenj/code/33GOD/candystore"},
        ),
        "bb": sample_event(
            id="550e8400-e29b-41d4-a716-4466554aa002",
            data={"working_directory": "/home/delorenj/code/33GOD/bloodbank"},
        ),
        # A sibling worktree the map assigns to james-brennan.
        "james-brennan": sample_event(
            id="550e8400-e29b-41d4-a716-4466554aa003",
            data={"working_directory": "/home/delorenj/code/james-brennan-jimb169"},
        ),
        # Unresolved: belongs to no registry project.
        "unresolved": sample_event(
            id="550e8400-e29b-41d4-a716-4466554aa004",
            data={"working_directory": "/tmp/hermes-board-cranker-50"},
        ),
    }
    for event in events.values():
        assert insert_event(event) is True
    return {key: event["id"] for key, event in events.items()}


def test_projects_lists_the_registry_not_the_trail(db, project_map, sample_event):
    """The old answer to this question was /summary/by-project, which derived a
    project list from directory basenames -- so it offered `dist`, `.agents`,
    `mirror` and `james-brennan.git` as things you could pick, and took 11.59 s."""
    _seed(sample_event)
    payload = list_projects(window_hours=24 * 365 * 10)
    slugs = [project["slug"] for project in payload["projects"]]

    assert slugs == sorted(slugs), "stable order, so the picker does not reshuffle"
    assert set(slugs) == {"bb", "candystore", "james-brennan", "vinyl"}

    # None of the shapes the basename derivation used to produce.
    for slug in slugs:
        assert slug not in {"dist", "web", ".agents", "unknown", "mirror", "bloodbank"}
        assert not slug.endswith(".git")

    by_slug = {project["slug"]: project for project in payload["projects"]}
    assert by_slug["candystore"]["ticket_prefix"] == "CANDYS"
    assert by_slug["candystore"]["repo_path"] == "/home/delorenj/code/33GOD/candystore"
    assert by_slug["bb"]["count"] == 1
    assert by_slug["james-brennan"]["count"] == 1

    # A registered project with no events still appears. It has to: otherwise a
    # freshly created project is unselectable until something happens in it,
    # which is exactly when someone goes looking for it.
    assert by_slug["vinyl"]["count"] == 0

    # The window is echoed rather than baked into a field name -- `count_24h`
    # would be a lie as soon as a caller passes ?window=7d.
    assert payload["window_hours"] == 24 * 365 * 10


def test_project_filter_resolves_through_the_map(db, project_map, sample_event):
    ids = _seed(sample_event)
    server, thread = _serve()
    host, port = server.server_address
    early = "from=2000-01-01T00:00:00Z&total=1"

    try:
        # The registry slug is `bb`, not the directory basename `bloodbank`.
        status, body = _get(host, port, f"/events?project=bb&{early}")
        assert status == 200
        assert [event["id"] for event in body["events"]] == [ids["bb"]]

        # A sibling worktree counts as its parent project, because the map says
        # so -- the directory basename contains no `/` boundary match at all.
        status, body = _get(host, port, f"/events?project=james-brennan&{early}")
        assert status == 200
        assert [event["id"] for event in body["events"]] == [ids["james-brennan"]]

        # Substring semantics are gone. `james` used to match `james-brennan`
        # via ILIKE '%james%'; now it is a typo and says so, rather than
        # returning a silently empty feed.
        status, body = _get(host, port, f"/events?project=james&{early}")
        assert status == 400
        assert "unknown project slug" in body["error"]

        # `intelliforia` must not reach `intelliforia-mobile`, and vice versa.
        # Neither is in this fixture's registry, so both are rejected rather
        # than quietly matching each other.
        for slug in ("intelliforia", "intelliforia-mobile"):
            assert _get(host, port, f"/events?project={slug}&{early}")[0] == 400

        # In the registry but with nothing mapped to it: an empty result with
        # HTTP 200. "No events yet" and "no such project" are different answers
        # and only one of them is the caller's mistake.
        status, body = _get(host, port, f"/events?project=vinyl&{early}")
        assert status == 200
        assert body["events"] == []
        assert body["total"] == 0

        # An unresolved directory belongs to no project, so no slug returns it.
        for slug in ("candystore", "bb", "james-brennan", "vinyl"):
            status, body = _get(host, port, f"/events?project={slug}&{early}")
            assert ids["unresolved"] not in [event["id"] for event in body["events"]]
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_projects_endpoint_and_window_validation(db, project_map, sample_event):
    _seed(sample_event)
    server, thread = _serve()
    host, port = server.server_address
    try:
        status, body = _get(host, port, "/projects")
        assert status == 200
        assert body["window_hours"] == 24
        assert {project["slug"] for project in body["projects"]} == {
            "bb",
            "candystore",
            "james-brennan",
            "vinyl",
        }
        # Every project carries the fields the picker renders.
        for project in body["projects"]:
            assert set(project) == {"slug", "name", "repo_path", "ticket_prefix", "count"}

        assert _get(host, port, "/projects?window=7d")[1]["window_hours"] == 24 * 7

        # A closed set of presets, not a duration parser: each extra shape is
        # another way to ask for a scan.
        status, body = _get(host, port, "/projects?window=all")
        assert status == 400
        assert "24h" in body["valid"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
