"""Working-directory to registry-project resolution (candystore.projects).

The matching rules are pure, so they are asserted against real directories
pulled from the live corpus without needing a database or pjangler installed.
Every path below was measured in the trail; the comment gives its 7-day event
count so a rule that regresses says what it costs.
"""

from __future__ import annotations

import pytest

from candystore.projects import Project, RegistryError, parse_registry, resolve

# The registry as `pjangler project list --json` actually returns it, trimmed to
# the projects these rules turn on. Note `bb`: the slug is not the directory
# basename, which is why basename-derived project names cannot be repaired by
# tidying them up.
REGISTRY = {
    "projects": {
        "project": {
            "name": "33GOD",
            "repo_path": "/home/delorenj/code/33GOD",
            "ticket_provider": {"identifier": "33GOD"},
        },
        "bb": {
            "name": "bb",
            "repo_path": "/home/delorenj/code/33GOD/bloodbank",
            "ticket_provider": {"identifier": "BB"},
        },
        "candystore": {
            "name": "Candystore",
            "repo_path": "/home/delorenj/code/33GOD/candystore",
            "ticket_provider": {"identifier": "CANDYS"},
        },
        "intelliforia": {
            "name": "intelliforia",
            "repo_path": "/home/delorenj/code/intelliforia",
            "ticket_provider": {"identifier": "INT"},
        },
        "intelliforia-mobile": {
            "name": "intelliforia-mobile",
            "repo_path": "/home/delorenj/code/intelliforia-mobile",
            "ticket_provider": {"identifier": "INTE"},
        },
        "james-brennan": {
            "name": "james-brennan",
            "repo_path": "/home/delorenj/code/james-brennan",
            "ticket_provider": {"identifier": "JIMB"},
        },
        "docsidian": {
            "name": "docsidian",
            "repo_path": "/home/delorenj/code/docsidian",
            # Really has no identifier in the live registry.
            "ticket_provider": {"identifier": ""},
        },
    }
}


@pytest.fixture
def projects() -> list[Project]:
    return parse_registry(REGISTRY)


def test_the_repo_itself_resolves(projects):
    assert resolve("/home/delorenj/code/33GOD/candystore", projects) == (
        "candystore",
        "repo-path",
    )
    # A trailing slash is the same directory.
    assert resolve("/home/delorenj/code/33GOD/candystore/", projects)[0] == "candystore"


def test_the_innermost_project_wins_not_the_outermost(projects):
    """/home/delorenj/code/33GOD is a prefix of four other registry paths, so a
    first-match-wins loop files bloodbank's history under 33GOD. The registry
    slug here is `bb`, not `bloodbank` -- basename repair could not produce it."""
    assert resolve("/home/delorenj/code/33GOD/bloodbank", projects) == ("bb", "repo-path")
    assert resolve("/home/delorenj/code/33GOD", projects) == ("project", "repo-path")


def test_a_sibling_project_is_not_absorbed_by_its_name_prefix(projects):
    """The trap that makes a bare startswith unusable: `intelliforia` is a
    string prefix of `intelliforia-mobile` (1,758 rows/7d), and both are real
    registered projects -- so the mistake would look like a correct answer."""
    assert resolve("/home/delorenj/code/intelliforia-mobile", projects) == (
        "intelliforia-mobile",
        "repo-path",
    )
    assert resolve("/home/delorenj/code/intelliforia", projects) == (
        "intelliforia",
        "repo-path",
    )


@pytest.mark.parametrize(
    "work_dir",
    [
        "/home/delorenj/code/james-brennan/.worktrees/feat-cartesia-agents",  # 3,568
        "/home/delorenj/code/james-brennan/.claude/worktrees/jimb-254-barge-in",  # 1,162
        "/home/delorenj/code/james-brennan/.worktrees/222-dev-approvals/apps/relay",  # 62
        "/home/delorenj/code/james-brennan/.claude/worktrees/daily-updates",  # 503
    ],
)
def test_nested_worktrees_and_subdirs_resolve_to_the_parent(projects, work_dir):
    """These are what basename reported as the projects `feat-cartesia-agents`,
    `jimb-254-barge-in`, `relay` and `daily-updates`."""
    assert resolve(work_dir, projects) == ("james-brennan", "repo-subpath")


def test_a_sibling_worktree_is_confirmed_by_the_board_prefix(projects):
    """/home/delorenj/code/james-brennan-jimb169 (1,745 rows/7d) sits beside the
    repo, not inside it, so prefix matching cannot see it. The token `jimb169`
    starting with the project's board prefix JIMB is what makes claiming it a
    confirmation rather than a guess."""
    assert resolve("/home/delorenj/code/james-brennan-jimb169", projects) == (
        "james-brennan",
        "sibling-worktree",
    )


def test_a_sibling_without_the_board_prefix_stays_unresolved(projects):
    """The rule must not absorb a genuinely different project that happens to
    share a name prefix. Left unresolved, which is visible and correctable;
    absorbed, it would be silent and permanent."""
    assert resolve("/home/delorenj/code/james-brennan-somebody-elses-repo", projects) == (
        None,
        "unresolved",
    )
    # A project with no board prefix cannot confirm a sibling at all.
    assert resolve("/home/delorenj/code/docsidian-experiment", projects) == (
        None,
        "unresolved",
    )


@pytest.mark.parametrize(
    "work_dir",
    [
        "/tmp/jimb-169-prod-incident-20260901",  # 2,940 -- CANDYS-39 decides these
        "/tmp/hermes-board-cranker-50",  # 1,014
        "/home/delorenj/code/.worktrees/hermes-agent/global-instruction-files",  # 885
        "/home/delorenj/code/skillex",  # 5,485 -- unregistered, CANDYS-68
        "/home/delorenj/.local/state/intelliforia-pr734/repo",  # 99
        "",
    ],
)
def test_what_cannot_be_resolved_is_left_unresolved(projects, work_dir):
    """Not a failure. A directory outside every registry path is either an
    unregistered repo (CANDYS-68) or an ephemeral clone (CANDYS-39), and both
    want a decision rather than a guess."""
    assert resolve(work_dir, projects) == (None, "unresolved")


def test_a_project_with_no_repo_path_is_skipped_not_fatal():
    payload = {
        "projects": {
            "planned": {"name": "planned", "repo_path": ""},
            "real": {"name": "real", "repo_path": "/home/delorenj/code/real"},
        }
    }
    assert [p.slug for p in parse_registry(payload)] == ["real"]


def test_an_unreadable_registry_raises_rather_than_emptying_the_map():
    """Syncing against an empty registry would mark every directory unresolved
    and erase a working map, so "the registry is broken" must not look like
    "nothing resolves any more"."""
    with pytest.raises(RegistryError):
        parse_registry({})
    with pytest.raises(RegistryError):
        parse_registry({"projects": {}})
    with pytest.raises(RegistryError):
        parse_registry({"projects": {"a": {"repo_path": ""}}})


def test_dir_name_reads_the_last_path_segment():
    project = Project(
        slug="bb",
        name="bb",
        repo_path="/home/delorenj/code/33GOD/bloodbank",
        ticket_prefix="BB",
    )
    assert project.dir_name == "bloodbank"
    assert project.slug != project.dir_name
