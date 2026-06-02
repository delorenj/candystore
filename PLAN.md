# Candystore Implementation Plan

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan task-by-task.  
> **Architecture reference:** `docs/plans/candystore-architecture.md`

**Goal:** Build Candystore — a durable event audit trail for Bloodbank v3 with REST API, pretty-printed summaries, and a React web UI.

**Architecture:** Single Python process (ingestion + API) behind a Dapr sidecar, PostgreSQL for persistence, React SPA for visualization. Follows bloodbank service conventions (stdlib HTTP server, CloudEvents 1.0, Docker Compose profile).

**Tech Stack:** Python 3.11, psycopg2, PostgreSQL 16, Vite + React 19, Tailwind 3, Recharts.

---

## Phase 0 — Bootstrap

### Task 1: Create project scaffold

**Objective:** Empty `candystore/` directory gets a working Python project layout.

**Files:**
- Create: `candystore/pyproject.toml`
- Create: `candystore/mise.toml`
- Create: `candystore/Dockerfile`
- Create: `candystore/.gitignore`
- Create: `candystore/candystore/__init__.py`

**Step 1: Write pyproject.toml**

```toml
[project]
name = "candystore"
version = "0.1.0"
description = "Bloodbank event audit trail and observability"
requires-python = ">=3.11"
dependencies = [
    "psycopg2-binary>=2.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Step 2: Write mise.toml**

```toml
[env]
_.file = [".env"]

[tasks.install]
description = "Install Python dependencies"
run = "pip install -e '.[dev]'"

[tasks.start]
description = "Start candystore API + ingestion server"
run = "python -m candystore.main"

[tasks.test]
description = "Run pytest suite"
run = "pytest tests/ -v"

[tasks.test:schema]
description = "Validate DB migrations on ephemeral Postgres"
run = "bash tests/migration_test.sh"

[tasks.lint]
description = "Run ruff"
run = "ruff check candystore/ tests/"

[tasks.build:ui]
description = "Build React SPA into static/"
run = "cd web && npm run build"
```

**Step 3: Write Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e "."
COPY candystore/ ./candystore/
COPY migrations/ ./migrations/
COPY static/ ./static/
ENV PYTHONUNBUFFERED=1
ENV APP_PORT=3001
EXPOSE 3001
CMD ["python", "-m", "candystore.main"]
```

**Step 4: Verify scaffold**

Run: `cd ~/code/33GOD/candystore && ls -la`
Expected: `pyproject.toml mise.toml Dockerfile candystore/ migrations/ tests/ web/ static/`

**Step 5: Commit**

```bash
git add candystore/
git commit -m "feat(candystore): bootstrap project scaffold"
```

---

### Task 2: Database migrations

**Objective:** Create the `events` table and indexes.

**Files:**
- Create: `candystore/migrations/001_events.sql`
- Create: `candystore/candystore/db.py`
- Create: `candystore/tests/test_db.py`

**Step 1: Write migration**

```sql
-- migrations/001_events.sql
CREATE TABLE IF NOT EXISTS events (
    id                  UUID PRIMARY KEY,
    specversion         TEXT NOT NULL DEFAULT '1.0',
    source              TEXT NOT NULL,
    type                TEXT NOT NULL,
    subject             TEXT,
    time                TIMESTAMPTZ NOT NULL,
    datacontenttype     TEXT,
    dataschema          TEXT,
    correlationid       UUID,
    causationid         UUID,
    producer            TEXT NOT NULL,
    service             TEXT NOT NULL,
    domain              TEXT NOT NULL,
    schemaref           TEXT,
    traceparent         TEXT,
    kind                TEXT NOT NULL,
    actor               JSONB,
    data                JSONB,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ordering_key        TEXT,
    raw                 JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(time DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_domain ON events(domain);
CREATE INDEX IF NOT EXISTS idx_events_correlationid ON events(correlationid);
CREATE INDEX IF NOT EXISTS idx_events_producer ON events(producer);
CREATE INDEX IF NOT EXISTS idx_events_service ON events(service);
CREATE INDEX IF NOT EXISTS idx_events_actor_cli ON events((actor->>'cli'));
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, time DESC);
CREATE INDEX IF NOT EXISTS idx_events_domain_time ON events(domain, time DESC);
CREATE INDEX IF NOT EXISTS idx_events_correlation_time ON events(correlationid, time);
CREATE INDEX IF NOT EXISTS idx_events_time_domain ON events(time, domain);
CREATE INDEX IF NOT EXISTS idx_events_time_actorcli ON events(time, (actor->>'cli'));
CREATE INDEX IF NOT EXISTS idx_events_data_gin ON events USING GIN (data jsonb_path_ops);
```

**Step 2: Write db.py**

```python
# candystore/db.py
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import Json

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://candystore:candystore@localhost:5432/candystore",
)


def _connect():
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def cursor() -> Iterator[Any]:
    conn = _connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """Run all migrations/*.sql in lexical order."""
    import pathlib
    migrations_dir = pathlib.Path(__file__).with_name("migrations")
    if not migrations_dir.exists():
        return
    with cursor() as cur:
        for path in sorted(migrations_dir.glob("*.sql")):
            cur.execute(path.read_text())


def insert_event(envelope: dict) -> bool:
    """Insert envelope. Returns True if inserted, False if duplicate."""
    sql = """
    INSERT INTO events (
        id, specversion, source, type, subject, time, datacontenttype,
        dataschema, correlationid, causationid, producer, service, domain,
        schemaref, traceparent, kind, actor, data, ordering_key, raw
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
    RETURNING id
    """
    actor = envelope.get("actor")
    data = envelope.get("data")
    params = (
        envelope.get("id"),
        envelope.get("specversion", "1.0"),
        envelope.get("source"),
        envelope.get("type"),
        envelope.get("subject"),
        envelope.get("time"),
        envelope.get("datacontenttype"),
        envelope.get("dataschema"),
        _uuid_or_none(envelope.get("correlationid")),
        _uuid_or_none(envelope.get("causationid")),
        envelope.get("producer"),
        envelope.get("service"),
        envelope.get("domain"),
        envelope.get("schemaref"),
        envelope.get("traceparent"),
        envelope.get("kind"),
        Json(actor) if actor is not None else None,
        Json(data) if data is not None else None,
        envelope.get("ordering_key"),
        Json(envelope),
    )
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() is not None


def _uuid_or_none(val: Any) -> uuid.UUID | None:
    if not val:
        return None
    try:
        return uuid.UUID(str(val))
    except ValueError:
        return None
```

**Step 3: Write failing test**

```python
# tests/test_db.py
import uuid
import pytest
from candystore.db import init_schema, insert_event


def test_insert_event():
    init_schema()
    env = {
        "id": str(uuid.uuid4()),
        "specversion": "1.0",
        "source": "urn:33god:test",
        "type": "bloodbank.v1.cli.session.ended",
        "time": "2026-05-24T16:00:00Z",
        "producer": "test",
        "service": "test",
        "domain": "cli",
        "kind": "event",
        "correlationid": str(uuid.uuid4()),
        "causationid": str(uuid.uuid4()),
        "actor": {"cli": "claude"},
        "data": {"session_id": "s1"},
    }
    assert insert_event(env) is True
    assert insert_event(env) is False  # idempotent
```

**Step 4: Run test**

Run: `cd ~/code/33GOD/candystore && pytest tests/test_db.py -v`
Expected: FAIL — "ModuleNotFoundError: candystore" (need pip install)

**Step 5: Install and re-run**

Run: `pip install -e ".[dev]" && pytest tests/test_db.py -v`
Expected: PASS (requires local Postgres running; use `docker run -d --name cs-postgres -e POSTGRES_PASSWORD=candystore -e POSTGRES_USER=candystore -e POSTGRES_DB=candystore -p 5432:5432 postgres:16-alpine`)

**Step 6: Commit**

```bash
git add candystore/migrations/ candystore/db.py tests/test_db.py
git commit -m "feat(candystore): events table + insert path"
```

---

## Phase 1 — Ingestion

### Task 3: Dapr subscription handler

**Objective:** Receive CloudEvents from Dapr and persist them.

**Files:**
- Create: `candystore/candystore/ingest.py`
- Create: `candystore/tests/test_ingest.py`
- Modify: `candystore/candystore/main.py`

**Step 1: Write ingest.py**

```python
# candystore/ingest.py
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any

from candystore.db import insert_event

SUBSCRIBE_PUBSUB = "bloodbank-pubsub"
# Fallback: if Dapr rejects wildcard, enumerate explicit topics.
TOPICS = [
    ("bloodbank.evt.v1.cli.session.started", "/events/cli_session_started"),
    ("bloodbank.evt.v1.cli.session.ended", "/events/cli_session_ended"),
    ("bloodbank.evt.v1.conversation.turn.started", "/events/turn_started"),
    ("bloodbank.evt.v1.tool.tool_call.requested", "/events/tool_requested"),
    ("bloodbank.evt.v1.tool.tool_call.invoked", "/events/tool_invoked"),
    ("bloodbank.evt.v1.tool.tool_call.completed", "/events/tool_completed"),
    ("bloodbank.evt.v1.agent.invocation.completed", "/events/agent_completed"),
    ("bloodbank.evt.v1.agent.invocation.failed", "/events/agent_failed"),
    ("bloodbank.evt.v1.system.heartbeat.received", "/events/heartbeat"),
]


def subscribe_response() -> list[dict]:
    return [
        {"pubsubname": SUBSCRIBE_PUBSUB, "topic": topic, "route": route}
        for topic, route in TOPICS
    ]


def handle_event(body: bytes) -> dict:
    envelope = json.loads(body.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be a dict")
    ok = insert_event(envelope)
    return {"status": "SUCCESS", "inserted": ok}
```

**Step 2: Write main.py bootstrap**

```python
# candystore/main.py
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from candystore.db import init_schema
from candystore.ingest import handle_event, subscribe_response

APP_PORT = int(os.environ.get("APP_PORT", "3001"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/dapr/subscribe":
            self._send_json(200, subscribe_response())
            return
        if self.path == "/healthz":
            self._send_empty(204)
            return
        if self.path == "/readyz":
            # TODO: check DB connectivity
            self._send_empty(204)
            return
        self._send_empty(404)

    def do_POST(self) -> None:
        known_routes = {route for _, route in subscribe_response()}
        if self.path in known_routes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                result = handle_event(raw)
                self._send_json(200, result)
                print(
                    f"candystore: ingested type={json.loads(raw).get('type')} id={json.loads(raw).get('id')}",
                    flush=True,
                )
                return
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
        self._send_empty(404)


def main() -> int:
    init_schema()
    server = ThreadingHTTPServer(("0.0.0.0", APP_PORT), Handler)
    print(f"candystore: listening on 0.0.0.0:{APP_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 3: Run server locally**

Run: `cd ~/code/33GOD/candystore && python -m candystore.main &`
Test: `curl http://localhost:3001/healthz`
Expected: 204 No Content

**Step 4: Test ingest path**

Run:
```bash
curl -X POST http://localhost:3001/events/cli_session_ended \
  -H "Content-Type: application/json" \
  -d '{"id":"550e8400-e29b-41d4-a716-446655440000","specversion":"1.0","source":"urn:33god:test","type":"bloodbank.v1.cli.session.ended","time":"2026-05-24T16:00:00Z","producer":"test","service":"test","domain":"cli","kind":"event","correlationid":"550e8400-e29b-41d4-a716-446655440001","causationid":"550e8400-e29b-41d4-a716-446655440002","actor":{"cli":"claude"},"data":{"session_id":"s1"}}'
```
Expected: `{"status": "SUCCESS", "inserted": true}`

**Step 5: Commit**

```bash
git add candystore/ingest.py candystore/main.py
git commit -m "feat(candystore): Dapr ingest handler + HTTP bootstrap"
```

---

## Phase 2 — Query API

### Task 4: Event list endpoint

**Objective:** `GET /events` with filters and pagination.

**Files:**
- Create: `candystore/candystore/query.py`
- Modify: `candystore/candystore/main.py`
- Create: `candystore/tests/test_query.py`

**Step 1: Write query.py**

```python
# candystore/query.py
from __future__ import annotations

from typing import Any

from candystore.db import cursor


def list_events(
    *,
    type: str | None = None,
    domain: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    correlationid: str | None = None,
    producer: str | None = None,
    service: str | None = None,
    cli: str | None = None,
    project: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []

    if type:
        where.append("type = %s")
        params.append(type)
    if domain:
        where.append("domain = %s")
        params.append(domain)
    if from_time:
        where.append("time >= %s")
        params.append(from_time)
    if to_time:
        where.append("time <= %s")
        params.append(to_time)
    if correlationid:
        where.append("correlationid = %s")
        params.append(correlationid)
    if producer:
        where.append("producer = %s")
        params.append(producer)
    if service:
        where.append("service = %s")
        params.append(service)
    if cli:
        where.append("actor->>'cli' = %s")
        params.append(cli)
    if project:
        where.append("(data->>'git_remote' ILIKE %s OR data->>'working_directory' ILIKE %s)")
        params.append(f"%{project}%")
        params.append(f"%{project}%")

    count_sql = f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}"
    select_sql = f"""
    SELECT id, type, time, producer, actor, data, correlationid
    FROM events
    WHERE {' AND '.join(where)}
    ORDER BY time DESC
    LIMIT %s OFFSET %s
    """
    params_select = params + [limit, offset]

    with cursor() as cur:
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        cur.execute(select_sql, params_select)
        rows = cur.fetchall()

    events = []
    for row in rows:
        events.append({
            "id": str(row[0]),
            "type": row[1],
            "time": row[2].isoformat() if row[2] else None,
            "producer": row[3],
            "actor": row[4],
            "data": row[5],
            "correlationid": str(row[6]) if row[6] else None,
        })

    return {"events": events, "total": total, "limit": limit, "offset": offset}


def get_event(event_id: str) -> dict | None:
    with cursor() as cur:
        cur.execute(
            "SELECT raw FROM events WHERE id = %s",
            (event_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0]
```

**Step 2: Wire into main.py**

Add to `do_GET` in `Handler`:

```python
        if self.path.startswith("/events?") or self.path == "/events":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            def _first(key: str) -> str | None:
                vals = qs.get(key)
                return vals[0] if vals else None
            result = list_events(
                type=_first("type"),
                domain=_first("domain"),
                from_time=_first("from"),
                to_time=_first("to"),
                correlationid=_first("correlationid"),
                producer=_first("producer"),
                service=_first("service"),
                cli=_first("cli"),
                project=_first("project"),
                limit=int(_first("limit") or "100"),
                offset=int(_first("offset") or "0"),
            )
            self._send_json(200, result)
            return
        if self.path.startswith("/events/"):
            event_id = self.path.split("/")[-1]
            ev = get_event(event_id)
            if ev is None:
                self._send_empty(404)
                return
            self._send_json(200, ev)
            return
```

**Step 3: Test**

Run: `curl "http://localhost:3001/events?cli=claude&limit=10"`
Expected: JSON with `events` array and `total` count.

**Step 4: Commit**

```bash
git add candystore/query.py
git commit -m "feat(candystore): /events list + detail API"
```

---

### Task 5: Session and summary endpoints

**Objective:** `/sessions/:id`, `/summary/heatmap`, `/summary/by-cli`.

**Files:**
- Modify: `candystore/candystore/query.py`
- Modify: `candystore/candystore/main.py`
- Create: `candystore/tests/test_summary.py`

**Step 1: Add session + summary queries**

```python
# Append to query.py

def get_session_events(correlationid: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, type, time, producer, actor, data, raw
            FROM events
            WHERE correlationid = %s
            ORDER BY time ASC
            """,
            (correlationid,),
        )
        return [
            {
                "id": str(row[0]),
                "type": row[1],
                "time": row[2].isoformat() if row[2] else None,
                "producer": row[3],
                "actor": row[4],
                "data": row[5],
                "raw": row[6],
            }
            for row in cur.fetchall()
        ]


def heatmap(group_by: str = "project", from_time: str | None = None, to_time: str | None = None) -> list[dict]:
    group_col = {
        "project": "COALESCE(data->>'git_remote', data->>'working_directory', 'unknown')",
        "cli": "COALESCE(actor->>'cli', 'unknown')",
        "domain": "domain",
    }.get(group_by, "domain")

    sql = f"""
    SELECT DATE_TRUNC('hour', time) AS hour, {group_col} AS bucket, COUNT(*) AS count
    FROM events
    WHERE time >= %s AND time <= %s
    GROUP BY hour, bucket
    ORDER BY hour DESC, count DESC
    """
    with cursor() as cur:
        cur.execute(sql, (from_time or "1970-01-01", to_time or "2099-01-01"))
        return [
            {"hour": row[0].isoformat(), "bucket": row[1], "count": row[2]}
            for row in cur.fetchall()
        ]
```

**Step 2: Wire routes**

Add to `do_GET`:

```python
        if self.path.startswith("/sessions/"):
            parts = self.path.split("/")
            if len(parts) == 3:
                cid = parts[2]
                self._send_json(200, {"session_id": cid, "events": get_session_events(cid)})
                return
        if self.path == "/summary/heatmap":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            group = (qs.get("group") or ["project"])[0]
            result = heatmap(group_by=group)
            self._send_json(200, {"buckets": result, "group_by": group})
            return
```

**Step 3: Commit**

```bash
git commit -am "feat(candystore): session + heatmap API"
```

---

## Phase 3 — Pretty Print

### Task 6: Event summarizers

**Objective:** Pretty-print session.end and other core event types.

**Files:**
- Create: `candystore/candystore/summarize.py`
- Modify: `candystore/candystore/query.py`
- Modify: `candystore/candystore/main.py`

**Step 1: Write summarize.py**

```python
# candystore/summarize.py
from __future__ import annotations

from typing import Any, Callable


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def _session_ended(env: dict) -> dict:
    data = env.get("data", {})
    actor = env.get("actor", {})
    return {
        "title": f"Session ended — {data.get('git_branch', 'unknown')}",
        "cli": actor.get("cli"),
        "provider": actor.get("provider"),
        "duration": _fmt_duration(data.get("duration_seconds")),
        "turns": data.get("total_turns"),
        "tools_used": len(data.get("tools_used", [])),
        "files_modified": data.get("files_modified"),
        "git_commits": data.get("git_commits"),
        "final_status": data.get("final_status"),
        "end_reason": data.get("end_reason"),
        "working_directory": data.get("working_directory"),
    }


def _session_started(env: dict) -> dict:
    data = env.get("data", {})
    return {
        "title": f"Session started — {data.get('git_branch', 'unknown')}",
        "working_directory": data.get("working_directory"),
        "git_branch": data.get("git_branch"),
        "git_remote": data.get("git_remote"),
    }


def _heartbeat(env: dict) -> dict:
    data = env.get("data", {})
    return {
        "title": f"Heartbeat — {data.get('producer_id', 'unknown')}",
        "tick_seq": data.get("tick_seq"),
        "producer_id": data.get("producer_id"),
    }


def _generic(env: dict) -> dict:
    return {
        "title": env.get("type", "unknown event"),
        "producer": env.get("producer"),
        "domain": env.get("domain"),
    }


SUMMARIZERS: dict[str, Callable[[dict], dict]] = {
    "bloodbank.v1.cli.session.ended": _session_ended,
    "bloodbank.v1.cli.session.started": _session_started,
    "system.heartbeat.tick": _heartbeat,
}


def summarize(env: dict) -> dict:
    fn = SUMMARIZERS.get(env.get("type", ""), _generic)
    return fn(env)
```

**Step 2: Add summary endpoint**

In `main.py`, add route `/events/:id/summary`:

```python
        if self.path.startswith("/events/") and self.path.endswith("/summary"):
            event_id = self.path.split("/")[-2]
            ev = get_event(event_id)
            if ev is None:
                self._send_empty(404)
                return
            from candystore.summarize import summarize
            self._send_json(200, {"summary": summarize(ev), "raw": ev})
            return
```

**Step 3: Test**

Run: `curl http://localhost:3001/events/550e8400-e29b-41d4-a716-446655440000/summary`
Expected: `{"summary": {"title": "Session ended — unknown", ...}, "raw": {...}}`

**Step 4: Commit**

```bash
git add candystore/summarize.py
git commit -m "feat(candystore): event-type summarizers"
```

---

## Phase 4 — Web UI

### Task 7: Vite + React scaffold

**Objective:** Frontend build pipeline.

**Files:**
- Create: `candystore/web/package.json`
- Create: `candystore/web/index.html`
- Create: `candystore/web/vite.config.js`
- Create: `candystore/web/src/main.jsx`
- Create: `candystore/web/src/App.jsx`
- Create: `candystore/web/tailwind.config.js`
- Create: `candystore/web/src/index.css`

**Step 1: Write package.json**

```json
{
  "name": "candystore-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0",
    "recharts": "^2.12.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "vite": "^6.0.0"
  }
}
```

**Step 2: Write tailwind.config.js**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
```

**Step 3: Write index.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

**Step 4: Write App.jsx**

```jsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import EventList from "./pages/EventList";
import EventDetail from "./pages/EventDetail";
import HeatMap from "./pages/HeatMap";

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <header className="border-b border-gray-800 px-6 py-4">
          <h1 className="text-xl font-bold text-amber-500">Candystore</h1>
        </header>
        <main className="p-6">
          <Routes>
            <Route path="/" element={<EventList />} />
            <Route path="/events/:id" element={<EventDetail />} />
            <Route path="/heatmap" element={<HeatMap />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
```

**Step 5: Build**

Run:
```bash
cd ~/code/33GOD/candystore/web
npm install
npm run build
```
Expected: `dist/` folder with `index.html` and assets.

**Step 6: Serve static files from Python**

In `main.py`, add static file serving for `/`:

```python
import pathlib
STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"

# In do_GET, before 404:
        if self.path == "/" or self.path == "/index.html":
            index = STATIC_DIR / "index.html"
            if index.exists():
                self._send_file(200, index, "text/html")
                return
        # Serve other static assets… (or proxy to /static/ prefix)
```

Copy build output:
```bash
cp -r ~/code/33GOD/candystore/web/dist/* ~/code/33GOD/candystore/static/
```

**Step 7: Commit**

```bash
git add candystore/web/ candystore/static/
git commit -m "feat(candystore): React + Vite UI scaffold"
```

---

### Task 8: Event list + filter UI

**Objective:** Render events with time range and CLI filters.

**Files:**
- Create: `candystore/web/src/pages/EventList.jsx`
- Create: `candystore/web/src/components/FilterBar.jsx`
- Create: `candystore/web/src/components/EventCard.jsx`

**Step 1: Write EventList.jsx**

```jsx
import { useEffect, useState } from "react";
import FilterBar from "../components/FilterBar";
import EventCard from "../components/EventCard";

const API = import.meta.env.VITE_API_URL || "";

export default function EventList() {
  const [events, setEvents] = useState([]);
  const [filters, setFilters] = useState({ from: "", to: "", cli: "", project: "" });

  useEffect(() => {
    const qs = new URLSearchParams();
    if (filters.from) qs.set("from", filters.from);
    if (filters.to) qs.set("to", filters.to);
    if (filters.cli) qs.set("cli", filters.cli);
    if (filters.project) qs.set("project", filters.project);
    fetch(`${API}/events?${qs}`)
      .then((r) => r.json())
      .then((data) => setEvents(data.events || []));
  }, [filters]);

  return (
    <div className="space-y-4">
      <FilterBar filters={filters} onChange={setFilters} />
      <div className="grid gap-3">
        {events.map((ev) => (
          <EventCard key={ev.id} event={ev} />
        ))}
      </div>
    </div>
  );
}
```

**Step 2: Write EventCard.jsx**

```jsx
import { Link } from "react-router-dom";

const CLI_COLORS = {
  claude: "border-amber-500",
  copilot: "border-blue-500",
  gemini: "border-teal-500",
};

export default function EventCard({ event }) {
  const cli = event.actor?.cli || "unknown";
  const color = CLI_COLORS[cli] || "border-gray-600";
  return (
    <Link to={`/events/${event.id}`} className={`block rounded border-l-4 bg-gray-900 p-4 hover:bg-gray-800 ${color}`}>
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm text-gray-400">{event.type}</span>
        <span className="text-xs text-gray-500">{new Date(event.time).toLocaleString()}</span>
      </div>
      <div className="mt-1 text-sm text-gray-300">
        {event.producer} · {cli}
      </div>
    </Link>
  );
}
```

**Step 3: Write FilterBar.jsx**

```jsx
export default function FilterBar({ filters, onChange }) {
  const update = (key, val) => onChange({ ...filters, [key]: val });
  return (
    <div className="flex flex-wrap gap-3">
      <input type="datetime-local" className="rounded bg-gray-800 px-3 py-2 text-sm" value={filters.from} onChange={(e) => update("from", e.target.value)} />
      <input type="datetime-local" className="rounded bg-gray-800 px-3 py-2 text-sm" value={filters.to} onChange={(e) => update("to", e.target.value)} />
      <select className="rounded bg-gray-800 px-3 py-2 text-sm" value={filters.cli} onChange={(e) => update("cli", e.target.value)}>
        <option value="">All CLIs</option>
        <option value="claude">Claude</option>
        <option value="copilot">Copilot</option>
        <option value="gemini">Gemini</option>
      </select>
      <input type="text" placeholder="Project" className="rounded bg-gray-800 px-3 py-2 text-sm" value={filters.project} onChange={(e) => update("project", e.target.value)} />
    </div>
  );
}
```

**Step 4: Rebuild and verify**

Run: `cd ~/code/33GOD/candystore/web && npm run build && cp -r dist/* ../static/`
Open: `http://localhost:3001/`
Expected: Event list renders with filters.

**Step 5: Commit**

```bash
git add candystore/web/src/
git commit -m "feat(candystore): event list + filter UI"
```

---

### Task 9: Event detail + heat map views

**Objective:** Pretty-printed detail page and heat map chart.

**Files:**
- Create: `candystore/web/src/pages/EventDetail.jsx`
- Create: `candystore/web/src/pages/HeatMap.jsx`

**Step 1: Write EventDetail.jsx**

```jsx
import { useParams } from "react-router-dom";
import { useEffect, useState } from "react";

export default function EventDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch(`/events/${id}/summary`)
      .then((r) => r.json())
      .then(setData);
  }, [id]);

  if (!data) return <div className="p-6">Loading…</div>;

  return (
    <div className="space-y-6">
      <div className="rounded bg-gray-900 p-6">
        <h2 className="text-lg font-bold text-amber-500">{data.summary.title}</h2>
        <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
          {Object.entries(data.summary).filter(([k]) => k !== "title").map(([k, v]) => (
            <div key={k}>
              <dt className="text-gray-500 capitalize">{k.replace(/_/g, " ")}</dt>
              <dd className="text-gray-200">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <details className="rounded bg-gray-900 p-4">
        <summary className="cursor-pointer text-sm text-gray-400">Raw envelope</summary>
        <pre className="mt-2 overflow-auto text-xs text-gray-300">{JSON.stringify(data.raw, null, 2)}</pre>
      </details>
    </div>
  );
}
```

**Step 2: Write HeatMap.jsx**

```jsx
import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export default function HeatMap() {
  const [data, setData] = useState([]);
  const [group, setGroup] = useState("project");

  useEffect(() => {
    fetch(`/summary/heatmap?group=${group}`)
      .then((r) => r.json())
      .then((d) => setData(d.buckets || []));
  }, [group]);

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        {["project", "cli", "domain"].map((g) => (
          <button key={g} onClick={() => setGroup(g)} className={`rounded px-3 py-1 text-sm ${group === g ? "bg-amber-600 text-white" : "bg-gray-800 text-gray-300"}`}>
            {g}
          </button>
        ))}
      </div>
      <div className="h-96 rounded bg-gray-900 p-4">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data.slice(0, 50)}>
            <XAxis dataKey="hour" tick={{ fontSize: 12 }} />
            <YAxis />
            <Tooltip />
            <Bar dataKey="count" fill="#f59e0b" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
```

**Step 3: Rebuild and verify**

Run: `cd ~/code/33GOD/candystore/web && npm run build && cp -r dist/* ../static/`
Test: Navigate to `/events/:id` and `/heatmap`
Expected: Pretty-printed summaries and bar chart render.

**Step 4: Commit**

```bash
git add candystore/web/src/pages/
git commit -m "feat(candystore): detail + heat map views"
```

---

## Phase 5 — Compose + Integration

### Task 10: Docker Compose profile

**Objective:** Candystore boots with the bloodbank sandbox.

**Files:**
- Modify: `bloodbank/compose/docker-compose.yml`
- Create: `candystore/dapr-components/pubsub.yaml`
- Modify: `bloodbank/mise.toml`

**Step 1: Add services to docker-compose.yml**

Append to `bloodbank/compose/docker-compose.yml`:

```yaml
  candystore:
    build:
      context: ../../candystore
    container_name: bloodbank-candystore
    profiles:
      - candystore
    environment:
      APP_PORT: "3001"
      DATABASE_URL: "postgresql://candystore:candystore@postgres:5432/candystore"
    ports:
      - "${BLOODBANK_CANDystore_PORT:-3603}:3001"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - bloodbank-network
    healthcheck:
      test: ["CMD-SHELL", "wget --spider --quiet --timeout=3 http://127.0.0.1:3001/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s
    restart: unless-stopped

  daprd-candystore:
    image: daprio/daprd:1.13.0
    container_name: bloodbank-daprd-candystore
    profiles:
      - candystore
    command:
      - "./daprd"
      - "--app-id=bloodbank-candystore"
      - "--dapr-http-port=3500"
      - "--dapr-grpc-port=50001"
      - "--app-port=3001"
      - "--app-channel-address=candystore"
      - "--app-protocol=http"
      - "--resources-path=/components"
      - "--placement-host-address=dapr-placement:50005"
      - "--log-level=info"
    volumes:
      - ./components:/components:ro
      - ../../candystore/dapr-components:/components/candystore:ro
    depends_on:
      nats-init:
        condition: service_completed_successfully
      candystore:
        condition: service_healthy
    networks:
      - bloodbank-network
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: bloodbank-postgres
    profiles:
      - candystore
    environment:
      POSTGRES_USER: candystore
      POSTGRES_PASSWORD: candystore
      POSTGRES_DB: candystore
    volumes:
      - bloodbank-postgres-data:/var/lib/postgresql/data
      - ../../candystore/migrations:/docker-entrypoint-initdb.d:ro
    networks:
      - bloodbank-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U candystore"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s
    restart: unless-stopped
```

Add to volumes:
```yaml
volumes:
  bloodbank-nats-data:
  bloodbank-apicurio-data:
  bloodbank-postgres-data:
```

**Step 2: Add Dapr component with durable consumer**

Create `candystore/dapr-components/pubsub.yaml`:

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: bloodbank-pubsub
spec:
  type: pubsub.jetstream
  version: v1
  metadata:
    - name: natsURL
      value: "nats://nats:4222"
    - name: name
      value: "bloodbank-pubsub"
    - name: durableName
      value: "candystore-events"
    - name: queueGroupName
      value: "candystore"
    - name: streamName
      value: "BLOODBANK_EVENTS"
    - name: deliverPolicy
      value: "all"
    - name: ackWait
      value: "30s"
```

**Step 3: Add mise task**

In `bloodbank/mise.toml`:

```toml
[tasks."up:candystore"]
description = "Boot candystore profile (postgres + app + daprd)"
run = """
docker compose --project-name $COMPOSE_PROJECT -f $COMPOSE_FILE \
  --profile candystore up -d
"""
```

**Step 4: Smoke test**

Run:
```bash
cd ~/code/33GOD/bloodbank
mise run up
mise run up:candystore
sleep 10
curl http://localhost:3603/healthz
curl -s http://localhost:3603/events | jq '.total'
```
Expected: 204 on healthz, total >= 0.

**Step 5: Commit**

```bash
git add bloodbank/compose/docker-compose.yml bloodbank/mise.toml candystore/dapr-components/
git commit -m "feat(candystore): Docker Compose profile + Dapr durable consumer"
```

---

## Phase 6 — Polish

### Task 11: Pretty-print all session data in UI

**Objective:** When viewing a session-end event, show a rich summary card instead of key-value pairs.

**Files:**
- Modify: `candystore/web/src/pages/EventDetail.jsx`

(Implementation: render `EventTypeCard` components based on `data.raw.type`. Skip detailed code — implementer extends existing pattern.)

### Task 12: Add `/sessions/:id` timeline view

**Objective:** Vertical timeline of all events in a session.

**Files:**
- Create: `candystore/web/src/pages/SessionTimeline.jsx`

(Implementation: fetch `/sessions/:id`, render time-ordered event cards with duration gaps.)

### Task 13: Add nav + routing

**Objective:** Top nav with links to Events, Heatmap, Sessions.

**Files:**
- Modify: `candystore/web/src/App.jsx`

---

## Appendix — Directory Layout

```
candystore/
├── candystore/
│   ├── __init__.py
│   ├── main.py          # HTTP server (ingest + API + static)
│   ├── db.py            # Postgres connection + insert
│   ├── ingest.py        # Dapr event handler
│   ├── query.py         # SELECT builders
│   └── summarize.py     # Pretty-print formatters
├── migrations/
│   └── 001_events.sql
├── dapr-components/
│   └── pubsub.yaml      # Durable consumer manifest
├── web/
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── index.css
│       ├── components/
│       │   ├── FilterBar.jsx
│       │   ├── EventCard.jsx
│       │   └── …
│       └── pages/
│           ├── EventList.jsx
│           ├── EventDetail.jsx
│           ├── HeatMap.jsx
│           └── SessionTimeline.jsx
├── static/              # Built SPA (copied from web/dist)
├── tests/
│   ├── test_db.py
│   ├── test_ingest.py
│   ├── test_query.py
│   └── migration_test.sh
├── pyproject.toml
├── mise.toml
├── Dockerfile
└── .gitignore
```
