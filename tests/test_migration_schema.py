"""Static checks that raw SQL migrations stay aligned with ORM insert types."""

from pathlib import Path

from sqlalchemy import JSON, String

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
        column_types[parts[0]] = parts[1].upper()
    return column_types


def test_events_migration_uses_orm_compatible_insert_types() -> None:
    """Migration column types must accept values emitted by SQLAlchemy String/JSON mappings."""
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
        assert column_types[column_name] == "TEXT"

    json_insert_columns = {"actor", "data", "raw", "payload"}
    for column_name in json_insert_columns:
        assert isinstance(StoredEvent.__table__.c[column_name].type, JSON)
        assert column_types[column_name] == "JSON"
