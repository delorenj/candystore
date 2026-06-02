#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required for migration_test.sh" >&2
  exit 2
fi

python - <<'PY'
from candystore.db import cursor, init_schema

init_schema()
with cursor() as cur:
    cur.execute("SELECT to_regclass('public.events')")
    table = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pg_indexes WHERE tablename = 'events'")
    indexes = cur.fetchone()[0]

assert table == "events", table
assert indexes >= 12, indexes
print("migration ok")
PY
