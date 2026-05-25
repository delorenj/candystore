"""Static checks that raw SQL migrations stay aligned with ORM metadata."""

from pathlib import Path

from sqlalchemy import JSON, Float, String
from sqlalchemy.sql.sqltypes import DateTime

from candystore.models import StoredEvent

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "001_events.sql"


def _migration_column_types() -> dict[str, str]:
    """Return normalized column types from the events CREATE TABLE migration."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    start = sql.index("CREATE TABLE IF NOT EXISTS events (")
    body = sql[start:].split(");", 1)[0]

    column_types: dict[str, str] = {}
    for raw_line in body.splitlines()[1:]:
        line = raw_line.split("--", 1)[0].strip().rstrip(",")
        if not line:
            continue
        parts = line.split()
        if parts[0].upper() in {"PRIMARY", "UNIQUE", "CONSTRAINT", "FOREIGN", "CHECK"}:
            continue
        type_tokens: list[str] = []
        for token in parts[1:]:
            if token.upper() in {"NOT", "NULL", "DEFAULT", "PRIMARY", "REFERENCES", "CONSTRAINT"}:
                break
            type_tokens.append(token.upper())
        column_types[parts[0]] = " ".join(type_tokens)
    return column_types


def _migration_indexes() -> dict[str, tuple[str, ...]]:
    """Return normalized index definitions from the events migration."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    indexes: dict[str, tuple[str, ...]] = {}
    for statement in sql.split(";"):
        normalized = " ".join(statement.split())
        if not normalized.upper().startswith("CREATE INDEX IF NOT EXISTS "):
            continue
        prefix = "CREATE INDEX IF NOT EXISTS "
        name, rest = normalized[len(prefix) :].split(" ON events", 1)
        columns = rest.strip().removeprefix("(").removesuffix(")")
        indexes[name] = tuple(column.strip() for column in columns.split(","))
    return indexes


def test_events_migration_uses_orm_compatible_insert_types() -> None:
    """Migration column types must accept values emitted by SQLAlchemy mappings."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8").upper()
    column_types = _migration_column_types()

    assert "UUID" not in sql
    assert "JSONB" not in sql
    assert "JSONB_PATH_OPS" not in sql

    string_insert_columns = {
        "id",
        "correlationid",
        "causationid",
        "session_id",
        "correlation_id",
    }
    for column_name in string_insert_columns:
        assert isinstance(StoredEvent.__table__.c[column_name].type, String)
        assert column_types[column_name].startswith("VARCHAR")

    json_insert_columns = {"actor", "data", "raw", "payload"}
    for column_name in json_insert_columns:
        assert isinstance(StoredEvent.__table__.c[column_name].type, JSON)
        assert column_types[column_name] == "JSON"


def test_events_migration_columns_match_orm_metadata() -> None:
    """The raw migration columns must stay aligned with SQLAlchemy create_all metadata."""
    migration_columns = _migration_column_types()
    orm_columns = StoredEvent.__table__.c

    assert set(migration_columns) == set(orm_columns.keys())

    for column_name, column in orm_columns.items():
        migration_type = migration_columns[column_name]
        orm_type = column.type
        if isinstance(orm_type, String):
            assert migration_type == f"VARCHAR({orm_type.length})"
        elif isinstance(orm_type, DateTime):
            assert migration_type == "TIMESTAMP WITH TIME ZONE"
        elif isinstance(orm_type, JSON):
            assert migration_type == "JSON"
        elif isinstance(orm_type, Float):
            assert migration_type == "FLOAT"
        else:  # pragma: no cover - forces this test to evolve with new column types.
            raise AssertionError(f"Unhandled ORM type for {column_name}: {orm_type!r}")


def test_events_migration_indexes_match_orm_metadata() -> None:
    """The raw migration must not define indexes absent from SQLAlchemy metadata."""
    migration_indexes = _migration_indexes()
    orm_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in StoredEvent.__table__.indexes
    }

    assert migration_indexes == orm_indexes
