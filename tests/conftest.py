from __future__ import annotations

import copy
import os
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

import pytest

# ---------------------------------------------------------------------------
# Truncation guardrail
# ---------------------------------------------------------------------------
# The `db` fixture truncates. Until this guard existed it truncated whatever
# `CANDYSTORE_TEST_DATABASE_URL or DATABASE_URL` resolved to, and DATABASE_URL
# on this host names the production audit trail: the better part of a million
# irreplaceable events with no backup referenced anywhere in this repo. The
# loss is silent by construction -- TRUNCATE succeeding is exactly what the
# fixture wants -- so the suite reports green on an emptied corpus and nobody
# finds out until they go looking for last month's sessions.
#
# The route to production is NOT only an unlucky `export`. candystore/db.py
# defines DEFAULT_DATABASE_URL = "...localhost:5432/candystore" and
# database_url() returns `os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)`,
# so an UNSET DATABASE_URL aims the app at production. Deleting the variable
# is not a safe state; it is the default one. Hence `_quarantine_database`
# below, which replaces both the variable and that module constant for the
# whole session so nothing in this suite can reach production by omission.
#
# Several independent facts must ALL hold before a single row is truncated,
# because every one of them can be satisfied by a production database on its
# own: a freshly restored prod DB is empty, and a prod dump loaded into
# `candystore_test` is correctly named. They are ANDed, never ORed. And they
# are re-proven inside the same transaction as the TRUNCATE, on the same
# connection, because a check that runs on a connection it then closes has
# only ever proven something about a connection that no longer exists.

TEST_DATABASE_URL_ENV = "CANDYSTORE_TEST_DATABASE_URL"

# Strictness-only knob: it can turn a skip into a failure, never the reverse.
# Without it, "the test database vanished" and "the DB tests passed" are the
# same green run -- 21 tests can silently stop existing. CI sets this.
REQUIRE_DB_ENV = "CANDYSTORE_REQUIRE_DB"

# The only tables the fixture is allowed to empty. It doubles as the allowlist
# that keeps the interpolated TRUNCATE below free of caller-supplied text.
# Maintenance trap worth knowing: a future migration that adds a table the
# fixture should clear must be added here, or that table is neither counted
# nor truncated and the row check quietly covers less than it appears to.
TRUNCATED_TABLES: tuple[str, ...] = ("events", "dead_letter")

# `project_dir_map` and `projects` (migrations 005/006) are deliberately NOT in
# the list above. They are reference data synced from the pjangler registry, not
# audit data -- truncating them between tests would delete a developer's real
# map, and the row-count guard below would count rows this suite never wrote.
# A test that needs them uses the `project_map` fixture, which seeds exactly the
# directories it names and removes exactly those again.

# Second, independent check (see `_assert_disposable_contents`). The fixture
# truncates at setup AND teardown, so a database used only by this suite holds
# single-digit rows whenever the guard runs -- never thousands. 1000 is
# deliberately slack rather than 0: an interrupted run, a hand-run
# `python -m candystore.main` against the test DB, or a developer poking at a
# row all leave leftovers, and a zero-tolerance gate that cries wolf is a gate
# people learn to switch off. It still sits ~870x below the production table
# it exists to notice. A test that legitimately needs more than this many rows
# to survive setup has no business using a fixture whose job is to destroy
# them; it wants its own non-truncating fixture, not a higher number here.
MAX_PREEXISTING_ROWS = 1000

CONNECT_TIMEOUT_SECONDS = 5

# Where DATABASE_URL points for every test that has NOT proven a disposable
# target. `.invalid` is reserved by RFC 2606 and cannot resolve, so the failure
# is immediate and the hostname is the error message. The point is that a test
# which forgets the `db` fixture gets a loud connection error instead of a
# silent, successful INSERT into the production audit trail -- the collection
# scanner below only looks for destructive SQL, and writing junk rows into the
# real corpus needs none.
UNCONFIGURED_DSN = (
    "postgresql://candystore@candystore-tests-must-use-the-db-fixture.invalid:5432"
    "/candystore_tests_unconfigured?connect_timeout=2"
)

# libpq reads these out of the environment for any parameter the connection
# string leaves unset, and `parse_dsn` -- which parses a string and nothing
# else -- cannot see them. PGOPTIONS is the dangerous one: it carries
# `-c search_path=...`, so it can silently point a bare connection at a
# different schema's `events` than the one this guard inspected. They are
# stripped for the session rather than modelled, because modelling libpq's
# precedence correctly is exactly the kind of thing a guard gets subtly wrong.
LIBPQ_ROUTING_ENV = (
    "PGDATABASE",
    "PGOPTIONS",
    "PGSERVICE",
    "PGSERVICEFILE",
)


class _Target(NamedTuple):
    """A connection the guard has an opinion about."""

    url: str
    dbname: str
    host: str
    port: str
    # The URL's own `options`, carried so that every connection this module
    # opens resolves table names identically. psycopg2 lets keyword arguments
    # win over the DSN, so a bare options= would drop an
    # `options=-c search_path=...` from the URL and leave the guard inspecting
    # `public.events` while the truncate emptied another schema's.
    options: str

    @property
    def redacted(self) -> str:
        # Refusals are printed into CI logs and pasted into tickets; the URL
        # carries a password, so the display form never does.
        return f"postgresql://{self.host}:{self.port}/{self.dbname}"

    @property
    def identity(self) -> tuple[str, str, str]:
        # What "the same database" means when comparing two connection
        # strings. Comparing the raw strings instead would refuse a URL that
        # merely gained an `?application_name=x` while still accepting two
        # spellings that reach different servers.
        return (self.dbname, self.host, self.port)


def _is_disposable(dbname: str) -> bool:
    """Is this database name obviously throwaway?

    "Obviously" means a token boundary, not a substring. `"test" in name`
    accepts `latest`, `contest`, `attest` and `bloodbank_latest`; `"test" in
    url` is far worse, because it also accepts every database on a host named
    `testbed` or reached as user `tester` -- which is exactly how a URL naming
    the production `candystore` database reads as disposable. Only the database
    NAME is consulted, and only at its edges.
    """
    name = dbname.strip().lower()
    return bool(name) and (
        name == "test" or name.startswith("test_") or name.endswith(("_test", "_tests"))
    )


def _require_db() -> bool:
    return os.environ.get(REQUIRE_DB_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _absent(reason: str) -> NoReturn:
    """No test database, or no Postgres. An absence, not a misconfiguration.

    Skipping is honest here -- nothing was at risk and nothing was destroyed --
    but it is also indistinguishable from "the DB tests all passed", which is
    how 21 tests can disappear without anyone noticing. REQUIRE_DB_ENV turns
    the absence into a failure for callers (CI) that know the stack is up.
    """
    if _require_db():
        pytest.fail(
            f"{reason}\n\n{REQUIRE_DB_ENV} is set, so a skip here is a failure.",
            pytrace=False,
        )
    pytest.skip(reason)


def _refuse(reason: str, target: _Target) -> NoReturn:
    """Hard-fail with everything the reader needs to fix it and nothing else.

    Never a skip. "No Postgres installed" and "you aimed the destructive
    fixture at the production audit trail" must not land in the same green
    run -- a guard that refuses and goes green reproduces the exact "nothing
    looked wrong" property that made the original hazard invisible.
    """
    host, port = target.host, target.port
    good_url = f"postgresql://candystore:candystore@{host}:{port}/candystore_test"
    pytest.fail(
        "\n".join(
            (
                "REFUSING TO TRUNCATE -- the candystore test-database guard rejected this target.",
                "",
                f"  target   {target.redacted}",
                f"  reason   {reason}",
                "",
                "The `db` fixture runs `TRUNCATE events, dead_letter` at setup AND at teardown.",
                "The production database on this host holds the entire audit trail, with no",
                "backup referenced anywhere in this repo, and a successful TRUNCATE is exactly",
                "what the fixture expects -- so the run would report GREEN on an emptied audit",
                "trail and nothing would tell you until you went looking for the history.",
                "",
                "Create a throwaway database and point the suite at that instead:",
                "",
                f"    createdb -h {host} -p {port} candystore_test",
                f"    export {TEST_DATABASE_URL_ENV}={good_url!r}",
                "    mise run test",
                "",
                "The target must satisfy ALL of the following, on every truncate:",
                f"  1. it comes from {TEST_DATABASE_URL_ENV}, and is not the DATABASE_URL the",
                "     application is configured with. DATABASE_URL is the PRODUCTION connection.",
                "  2. the database is named `test`, `test_*`, `*_test`, or `*_tests`.",
                "  3. the name in the URL matches the name the server reports it connected to,",
                "     re-asked on the connection that is about to issue the TRUNCATE.",
                "  4. `events` and `dead_letter` are ordinary local tables, not foreign tables.",
                f"  5. neither table already holds more than {MAX_PREEXISTING_ROWS} rows.",
                "",
                "If check 5 fired at TEARDOWN, the likely cause is a test that seeded more than",
                f"{MAX_PREEXISTING_ROWS} rows. Do not raise the limit -- that weakens the check",
                "for all 21 DB tests to serve one. Give that test its own non-truncating fixture.",
                "",
                "Do not widen the name pattern, raise the row limit, or re-add a DATABASE_URL",
                "fallback to make this pass. Create the database; it is one command.",
            )
        ),
        pytrace=False,
    )


def _options(base: str, *settings: str) -> str:
    """Compose a libpq `options` string that is never empty.

    Never empty on purpose: libpq fills an UNSET parameter from the
    environment, so handing it no options at all re-opens PGOPTIONS as a way to
    inject `-c search_path=...` into the connection that truncates. Later
    settings win, so the guard's own flags always come last.
    """
    return " ".join(part for part in (base, *settings) if part)


def _parse(url: str) -> _Target:
    """Read a connection string the way libpq will, not the way it looks."""
    import psycopg2
    from psycopg2 import extensions

    # Parsing is delegated to libpq itself (`parse_dsn` wraps PQconninfoParse)
    # rather than urlsplit, because the value is not a URL: it is a libpq
    # conninfo URI whose query string carries connection PARAMETERS that
    # override the hierarchical part. Measured on psycopg2 2.9.12:
    #     postgresql://u@h:5434/candystore_test?dbname=candystore
    #       -> {'dbname': 'candystore', 'host': 'h', 'port': '5434'}
    # A hand-rolled `urlsplit(url).path.lstrip("/")` reads that as
    # `candystore_test`, approves it, and truncates production. Percent-encoding
    # is the same trap from the other side: `/cand%79store` decodes to
    # `candystore`, and `?options=-c%20search_path%3D...` decodes to a real
    # search_path that would otherwise never be seen.
    try:
        parsed = extensions.parse_dsn(url)
    except psycopg2.Error as exc:
        pytest.fail(
            f"{TEST_DATABASE_URL_ENV} is not a valid libpq connection string: {exc}",
            pytrace=False,
        )

    declared = (parsed.get("dbname") or "").strip()
    target = _Target(
        url=url,
        dbname=declared or "<none>",
        host=parsed.get("host") or parsed.get("hostaddr") or "localhost",
        port=str(parsed.get("port") or "5432"),
        options=parsed.get("options") or "",
    )

    # An omitted dbname is not a shrug, it is a wildcard: libpq falls back to
    # $PGDATABASE, then to the user name, so `postgresql://candystore@host/`
    # opens the `candystore` database. Require the name to be spelled out.
    if not declared:
        _refuse(
            "the connection string does not name a database, so libpq would pick one "
            "from $PGDATABASE or the user name",
            target,
        )

    if not _is_disposable(declared):
        _refuse(f'database name "{declared}" is not obviously disposable', target)

    return target


def _assert_target_identity(cur: Any, target: _Target) -> None:
    """Ask the server where this connection actually landed.

    libpq's parser does not know about $PGDATABASE, a `service=` reference, or
    ~/.pg_service.conf, any of which can supply or replace the database name
    after parse_dsn has spoken. The parsed name is therefore only the first
    opinion; the authority is the server, asked over the very connection that
    is about to issue the statement. A disagreement is not a tie to break, it
    IS the refusal -- it means the string describes one database and opens
    another.
    """
    cur.execute("SELECT current_database()")
    actual = str(cur.fetchone()[0])
    if actual != target.dbname:
        _refuse(
            f'the URL names "{target.dbname}" but the server reports it connected to "{actual}"',
            target._replace(dbname=actual),
        )
    if not _is_disposable(actual):
        _refuse(f'server-reported database "{actual}" is not obviously disposable', target)


def _assert_disposable_contents(cur: Any, target: _Target, tables: Sequence[str]) -> None:
    """Prove each table is local, and small enough to be nobody's corpus."""
    from psycopg2 import sql

    for table in tables:
        # to_regclass resolves through the same search_path the TRUNCATE will
        # use, so the guard and the statement cannot be pointed at two
        # different tables of the same name.
        cur.execute("SELECT relkind FROM pg_class WHERE oid = to_regclass(%s)", (table,))
        row = cur.fetchone()
        if row is None:
            # Fresh database, migrations not applied yet. Nothing to destroy,
            # and init_schema() is about to create it.
            continue
        # postgres_fdw has supported TRUNCATE on foreign tables since
        # PostgreSQL 14, so a foreign `events` inside a correctly named and
        # provably empty test database forwards the statement to whatever it
        # points at -- the one shape that lets a truncate leave the database
        # current_database() just vouched for.
        if row[0] not in ("r", "p"):
            _refuse(
                f'"{table}" is not an ordinary local table (pg_class.relkind = {row[0]!r}); '
                "a foreign table would forward the TRUNCATE elsewhere",
                target,
            )
        # Bounded on purpose. An unbounded COUNT(*) here is a sequential scan
        # of the better part of a million rows and several GB of TOAST on the
        # database this most needs to reject; a guard that costs minutes is a
        # guard someone disables.
        cur.execute(
            sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {} LIMIT %s) AS probe").format(
                sql.Identifier(table)
            ),
            (MAX_PREEXISTING_ROWS + 1,),
        )
        if int(cur.fetchone()[0]) > MAX_PREEXISTING_ROWS:
            _refuse(
                f'"{table}" already holds more than {MAX_PREEXISTING_ROWS} rows, so this is '
                "not a database whose contents are disposable",
                target,
            )


def _verify(url: str, production_url: str | None) -> _Target:
    """Prove `url` names a database it is safe to empty, or refuse."""
    import psycopg2

    target = _parse(url)

    # The one shape the name rule cannot catch: production that happens to be
    # named `*_test`. If the string is the very one the application is
    # configured to run against, it is production by definition, whatever it
    # is called. This is also the exact copy-paste the skip message invites
    # (`export CANDYSTORE_TEST_DATABASE_URL="$DATABASE_URL"`).
    if production_url and url == production_url:
        _refuse(
            "this is byte-identical to DATABASE_URL, the connection the application runs "
            "against. A disposable database is a DIFFERENT database, not the same one under "
            "a second variable name",
            target,
        )

    # The inspection session is read-only, so the guard is structurally
    # incapable of the damage it exists to prevent: the same session answers
    # `TRUNCATE events` with ReadOnlySqlTransaction.
    try:
        conn = psycopg2.connect(
            url,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=_options(
                target.options,
                "-c application_name=candystore-test-guard",
                "-c default_transaction_read_only=on",
            ),
        )
    except psycopg2.Error as exc:
        _absent(f"Postgres unavailable for candystore tests: {exc}")

    try:
        with conn.cursor() as cur:
            _assert_target_identity(cur, target)
            _assert_disposable_contents(cur, target, TRUNCATED_TABLES)
    finally:
        conn.close()

    return target


def _truncate(target: _Target, tables: Sequence[str] = TRUNCATED_TABLES) -> None:
    """The only sanctioned way to empty a table in this suite.

    It opens its own connection from the verified string rather than going
    through `candystore.db.cursor()`, which re-derives its target from
    `os.environ` and a module-level DEFAULT_DATABASE_URL at every call. Routing
    the destructive statement through that helper means a test that
    monkeypatches `candystore.db.database_url`, or `psycopg2.connect`, or
    simply leaves PGOPTIONS set, redirects the TRUNCATE without touching
    anything this guard inspected -- and `db`'s teardown runs while those
    patches are still installed, because `db` requests monkeypatch first and so
    finalises before it. One connection recipe, built from one verified string,
    is the only version of this that can be reasoned about.
    """
    import psycopg2
    from psycopg2 import sql

    unknown = sorted(set(tables) - set(TRUNCATED_TABLES))
    if unknown:
        raise ValueError(f"not a guarded table: {', '.join(unknown)}")

    # The application under test reads DATABASE_URL, so if it has drifted the
    # test asserted against one database while this clears another. Compared as
    # a parsed identity, not as a string: exact equality refuses a URL that
    # merely gained `?application_name=x` while still accepting two spellings
    # that reach different servers.
    live = os.environ.get("DATABASE_URL")
    if not live or _parse_identity_quietly(live) != target.identity:
        pytest.fail(
            "DATABASE_URL no longer points at the verified test database; refusing to "
            f"truncate. Verified {target.redacted}, environment now points somewhere else. "
            "A test must restore DATABASE_URL (use monkeypatch, never os.environ directly).",
            pytrace=False,
        )

    conn = psycopg2.connect(
        target.url,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        options=_options(target.options, "-c application_name=candystore-test-truncate"),
    )
    try:
        with conn.cursor() as cur:
            # Re-proven here, in the transaction that destroys the rows, rather
            # than trusted from the session-scoped check. That check ran on a
            # connection it then closed; between then and now a pooler routing
            # by name, a DNS change, or someone restoring a dump into
            # `candystore_test` can change what this string reaches. Asking the
            # server again, on this connection, inside this transaction, closes
            # the window instead of measuring it.
            _assert_target_identity(cur, target)
            _assert_disposable_contents(cur, target, tables)
            cur.execute(
                sql.SQL("TRUNCATE {}").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in tables)
                )
            )
        conn.commit()
    finally:
        conn.close()


def _parse_identity_quietly(url: str) -> tuple[str, str, str] | None:
    """parse_dsn without the refusal machinery, for comparing two strings."""
    import psycopg2
    from psycopg2 import extensions

    try:
        parsed = extensions.parse_dsn(url)
    except psycopg2.Error:
        return None
    return (
        (parsed.get("dbname") or "").strip(),
        parsed.get("host") or parsed.get("hostaddr") or "localhost",
        str(parsed.get("port") or "5432"),
    )


@pytest.fixture(scope="session", autouse=True)
def _quarantine_database() -> Iterator[str | None]:
    """Make production unreachable by omission, for every test in the session.

    Autouse and session-scoped because the hazard is not confined to the `db`
    fixture. `candystore.db.database_url()` returns
    `os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)`, and that constant is
    the production DSN -- so a NEW test that forgets the fixture and calls
    `insert_event()` or `cursor()` reaches the real audit trail with DATABASE_URL
    unset, and needs no destructive SQL to corrupt it. The collection scanner
    below cannot see that: an INSERT is not a destructive statement.

    So both the variable and the constant are replaced with a DSN that cannot
    resolve. Tests that want a database ask for `db`, which points DATABASE_URL
    at the verified target for the duration of that test and no longer. It
    yields the original DATABASE_URL so `_verify` can recognise it.

    This deliberately does NOT skip when nothing is configured: the 28 tests
    that never touch Postgres must keep reporting honestly.
    """
    original = os.environ.get("DATABASE_URL")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("DATABASE_URL", UNCONFIGURED_DSN)
        for name in LIBPQ_ROUTING_ENV:
            monkeypatch.delenv(name, raising=False)
        try:
            import candystore.db

            monkeypatch.setattr(candystore.db, "DEFAULT_DATABASE_URL", UNCONFIGURED_DSN)
        except ImportError:
            # Nothing importable to protect; the env replacement still stands.
            pass
        yield original
    finally:
        monkeypatch.undo()


@pytest.fixture(scope="session")
def verified_test_database(_quarantine_database: str | None) -> _Target:
    """Resolve and vet the test database once per session.

    Session-scoped so the round trip is paid once, and so pytest replays the
    cached Failed/Skipped for every dependent test instead of re-probing. The
    per-truncate checks in `_truncate` are what make that caching safe.

    Absence skips; misdirection fails. "No test database configured" and
    "Postgres is not running" are environmental absences -- the run honestly
    reports N skipped and no data was ever at risk. "You aimed the destructive
    fixture at a database I will not destroy" is neither: it is a configuration
    that was one check away from deleting the audit trail, and a skip would
    file it under the same green run as a laptop with no Postgres installed.
    """
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        hint = ""
        if _quarantine_database:
            # Almost always the real story: DATABASE_URL is set, and the reader
            # is about to "fix" the skip by restoring the fallback that this
            # guard deleted on purpose, or by copying DATABASE_URL into the
            # test variable. Both are named here so neither looks like an idea.
            hint = (
                f" DATABASE_URL is set, but it is deliberately NOT used here: it is the "
                f"production connection and truncating it would destroy the audit trail. "
                f"Point {TEST_DATABASE_URL_ENV} at a throwaway database -- a DIFFERENT "
                f"database, not a copy of DATABASE_URL under a second name."
            )
        _absent(f"set {TEST_DATABASE_URL_ENV} for Postgres-backed tests.{hint}")
    return _verify(url, _quarantine_database)


@pytest.fixture
def db(verified_test_database: _Target, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    target = verified_test_database
    monkeypatch.setenv("DATABASE_URL", target.url)

    from candystore.db import init_schema

    try:
        init_schema()
    except Exception as exc:
        _absent(f"Postgres unavailable for candystore tests: {exc}")

    _truncate(target)

    yield

    # Both tables, unlike the previous teardown which truncated only `events`.
    # tests/test_poison_safety.py writes dead_letter rows, so they used to
    # survive into the next test's setup and only vanish there -- which meant
    # dead_letter row counts leaked across tests in file order.
    _truncate(target)


@pytest.fixture
def guarded_truncate(
    # Depends on `db` purely for ordering: nothing may truncate before the
    # guard has run and the schema exists.
    db: None,
    verified_test_database: _Target,
) -> Callable[..., None]:
    """Reusable truncate for tests that need to reset mid-test.

    Handed out as a fixture rather than an importable helper so reuse costs
    nothing (`def test_x(guarded_truncate)`) while the unguarded alternative
    costs a failing collection -- see `pytest_collection_modifyitems`.
    """

    def _run(*tables: str) -> None:
        _truncate(verified_test_database, tables or TRUNCATED_TABLES)

    return _run


# A guard that only covers one fixture is a guard with a door next to it. The
# fixture is where truncation belongs today, but nothing stops the next test --
# or the next agent writing one -- from opening a cursor and running the SQL
# directly, and that path answers to nobody. So the destructive statements are
# banned from the test tree outright and the ban is enforced at collection
# time, before any test body runs, regardless of which tests were selected.
_DESTRUCTIVE_SQL = re.compile(
    r"\b(?:TRUNCATE\b"
    r"|DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED)\b"
    r"|DELETE\s+FROM\b"
    # The shell spellings, which reach the same database with none of the SQL
    # keywords on the line. `psql` itself is deliberately NOT here: it is the
    # ordinary way to READ this database and the word appears in comments, docs
    # and shell helpers constantly, so banning the bare token fails the entire
    # suite at collection over a mention. The destructive verbs above still
    # match inside a `psql -c "..."` string, which is the case that matters.
    r"|\bdropdb\b|\bpg_restore\b)",
    re.IGNORECASE,
)
_SQL_GUARD_PRAGMA = "sql-guard: reviewed"
_SCANNED_SUFFIXES = (".py", ".sh", ".sql", ".bash", ".psql")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    offenders: list[str] = []
    here = Path(__file__).resolve().parent
    myself = Path(__file__).resolve()
    for path in sorted(p for p in here.rglob("*") if p.suffix in _SCANNED_SUFFIXES):
        # Only THIS file is exempt, matched by resolved path rather than by
        # name. Exempting every file called conftest.py would exempt
        # `tests/integration/conftest.py` -- the single most natural place for
        # someone to park a "reset the database" helper, and a file that can
        # define fixtures of its own.
        if path.resolve() == myself:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Previously a silent `continue`. A file under tests/ that cannot
            # be read cannot be cleared either, and "unreadable" is a cheap way
            # to be unscanned.
            offenders.append(f"  {path}: unreadable, so it cannot be shown to be safe ({exc})")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _DESTRUCTIVE_SQL.search(line) and _SQL_GUARD_PRAGMA not in line:
                offenders.append(f"  {path}:{lineno}: {line.strip()}")

    if offenders:
        raise pytest.UsageError(
            "\n".join(
                [
                    "Unguarded destructive SQL in the test tree:",
                    *offenders,
                    "",
                    "Every truncate must go through the `db` or `guarded_truncate` fixture, "
                    "which proves the target database is disposable before emptying it. "
                    "Running this statement directly bypasses that proof and can destroy the "
                    "production audit trail, for which this repo references no backup.",
                    f"If the match is a false positive, add a `# {_SQL_GUARD_PRAGMA}` comment "
                    "on that line and say in the commit why it is safe.",
                ]
            )
        )


@pytest.fixture
def project_map(db: None) -> Iterator[None]:
    """Seed the registry projection for the directories the fixtures use.

    Scoped rather than truncating: these tables hold reference data, so the
    fixture inserts the rows it needs and deletes those same rows back out
    (see TRUNCATED_TABLES). Every path here is real -- `bb` in particular is a
    registry slug that is not its directory's basename, which is the case a
    basename-derived project name can never get right.
    """
    from candystore.db import cursor as db_cursor

    rows = (
        ("/home/delorenj/code/33GOD/candystore", "candystore", "repo-path"),
        ("/home/delorenj/code/33GOD/bloodbank", "bb", "repo-path"),
        ("/home/delorenj/code/james-brennan", "james-brennan", "repo-path"),
        ("/home/delorenj/code/james-brennan-jimb169", "james-brennan", "sibling-worktree"),
        ("/tmp/hermes-board-cranker-50", None, "unresolved"),
    )
    registry = (
        ("candystore", "Candystore", "/home/delorenj/code/33GOD/candystore", "CANDYS"),
        ("bb", "bb", "/home/delorenj/code/33GOD/bloodbank", "BB"),
        ("james-brennan", "james-brennan", "/home/delorenj/code/james-brennan", "JIMB"),
        # In the registry with no events -- the picker must still list it.
        ("vinyl", "vinyl", "/home/delorenj/code/vinyl", "VINY"),
    )
    with db_cursor() as cur:
        cur.executemany(
            "INSERT INTO project_dir_map (work_dir, slug, rule) VALUES (%s, %s, %s) "
            "ON CONFLICT (work_dir) DO UPDATE SET slug = EXCLUDED.slug, rule = EXCLUDED.rule",
            rows,
        )
        cur.executemany(
            "INSERT INTO projects (slug, name, repo_path, ticket_prefix) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (slug) DO UPDATE "
            "SET name = EXCLUDED.name, repo_path = EXCLUDED.repo_path, "
            "ticket_prefix = EXCLUDED.ticket_prefix",
            registry,
        )
    try:
        yield
    finally:
        with db_cursor() as cur:
            cur.execute("DELETE FROM project_dir_map WHERE work_dir = ANY(%s)",
                        ([row[0] for row in rows],))
            cur.execute("DELETE FROM projects WHERE slug = ANY(%s)",
                        ([row[0] for row in registry],))


@pytest.fixture
def sample_event() -> Callable[..., dict[str, Any]]:
    def make_event(**overrides: Any) -> dict[str, Any]:
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
