from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import psycopg2

from candystore import stats
from candystore.db import check_connection, init_schema, record_dead_letter
from candystore.ingest import handle_event, known_event_routes, subscribe_response
from candystore.lifecycle import get_lifecycle_projection, list_lifecycle_projections
from candystore.query import (
    by_cli,
    by_project,
    daily,
    get_event,
    get_event_record,
    get_session_events,
    get_session_summary,
    heatmap,
    list_events,
)
from candystore.summarize import summarize

logger = logging.getLogger("candystore")

# Default to loopback so a bare `python -m candystore.main` never exposes the
# unauthenticated ingest+query API to the LAN. The container overrides this to
# 0.0.0.0 (see compose.yml) so the Dapr sidecar can reach the app over the
# docker network; the host port is published on 127.0.0.1 there.
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "3001"))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "Candystore/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if self._wants_html() and self._serve_spa_route(path):
            return

        if path == "/dapr/subscribe":
            self._send_json(200, subscribe_response())
            return
        if path == "/healthz":
            self._send_empty(204)
            return
        if path == "/readyz":
            try:
                ok = check_connection()
            except Exception as exc:
                self._send_json(503, {"ready": False, "error": str(exc)})
                return
            self._send_empty(204 if ok else 503)
            return

        if path == "/events":
            qs = parse_qs(parsed.query)
            result = list_events(
                type=_first(qs, "type"),
                domain=_first(qs, "domain"),
                from_time=_first(qs, "from"),
                to_time=_first(qs, "to"),
                correlationid=_first(qs, "correlationid"),
                producer=_first(qs, "producer"),
                service=_first(qs, "service"),
                cli=_first(qs, "cli"),
                project=_first(qs, "project"),
                scope=_first(qs, "scope"),
                limit=int(_first(qs, "limit") or "100"),
                offset=int(_first(qs, "offset") or "0"),
            )
            self._send_json(200, result)
            return

        if path == "/lifecycles":
            qs = parse_qs(parsed.query)
            try:
                as_of = _parse_as_of(_first(qs, "as_of"))
                result = list_lifecycle_projections(
                    as_of=as_of,
                    limit=int(_first(qs, "limit") or "100"),
                    offset=int(_first(qs, "offset") or "0"),
                )
            except (ValueError, TypeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, result)
            return

        if path.startswith("/lifecycles/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2:
                qs = parse_qs(parsed.query)
                try:
                    as_of = _parse_as_of(_first(qs, "as_of"))
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(
                    200,
                    get_lifecycle_projection(unquote(parts[1]), as_of=as_of),
                )
                return

        if path.startswith("/events/") and path.endswith("/summary"):
            event_id = path.split("/")[-2]
            ev = get_event(event_id)
            if ev is None:
                self._send_empty(404)
                return
            self._send_json(200, {"summary": summarize(ev), "raw": ev})
            return

        if path.startswith("/events/") and path.endswith("/raw"):
            event_id = path.split("/")[-2]
            ev = get_event(event_id)
            if ev is None:
                self._send_empty(404)
                return
            self._send_json(200, ev)
            return

        if path.startswith("/events/"):
            event_id = path.split("/")[-1]
            ev = get_event_record(event_id)
            if ev is None:
                self._send_empty(404)
                return
            self._send_json(200, ev)
            return

        if path.startswith("/sessions/") and path.endswith("/summary"):
            session_id = path.split("/")[-2]
            self._send_json(200, get_session_summary(session_id))
            return

        if path.startswith("/sessions/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2:
                session_id = unquote(parts[1])
                self._send_json(
                    200,
                    {"session_id": session_id, "events": get_session_events(session_id)},
                )
                return

        if path == "/summary/heatmap":
            qs = parse_qs(parsed.query)
            group = _first(qs, "group") or "project"
            self._send_json(
                200,
                {
                    "buckets": heatmap(
                        group_by=group,
                        from_time=_first(qs, "from"),
                        to_time=_first(qs, "to"),
                    ),
                    "group_by": group,
                },
            )
            return

        if path == "/summary/daily":
            qs = parse_qs(parsed.query)
            self._send_json(
                200,
                {"days": daily(from_time=_first(qs, "from"), to_time=_first(qs, "to"))},
            )
            return

        if path == "/summary/by-cli":
            qs = parse_qs(parsed.query)
            self._send_json(
                200,
                {"items": by_cli(from_time=_first(qs, "from"), to_time=_first(qs, "to"))},
            )
            return

        if path == "/summary/by-project":
            qs = parse_qs(parsed.query)
            self._send_json(
                200,
                {"items": by_project(from_time=_first(qs, "from"), to_time=_first(qs, "to"))},
            )
            return

        if self._serve_static(path):
            return

        self._send_empty(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in known_event_routes():
            self._send_empty(404)
            return

        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            result = handle_event(raw, topic=path)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            # Malformed / off-contract event (RecursionError = pathologically
            # nested JSON, un-parseable and thus permanent, not transient). Dapr
            # only honors DROP on a 2xx response; a non-2xx means "retry", which
            # turns a bad event into a poison message that redelivers forever.
            self._drop(raw, path, "malformed", exc)
            return
        except (psycopg2.DataError, psycopg2.IntegrityError) as exc:
            # The DB rejected the value as un-storable (e.g. a NUL that slipped
            # past sanitization, an out-of-range timestamp, a NOT NULL breach).
            # This is a permanent data defect, not a transient failure — DROP it
            # (with a dead_letter record) rather than 500-retry it forever.
            self._drop(raw, path, "db-data-error", exc)
            return
        except Exception as exc:
            # Genuinely transient (DB down, connection reset, etc.). RETRY is
            # correct: NATS redelivers and the idempotent insert dedupes.
            stats.incr("retried")
            logger.error("RETRY event on %s: %s", path, exc)
            self._send_json(500, {"status": "RETRY", "error": str(exc)})
            return

        self._send_json(200, result)

    def _drop(self, raw: bytes, topic: str, reason: str, exc: Exception) -> None:
        """Acknowledge (200) an un-storable event after preserving its bytes."""
        stats.incr("dropped")
        record_dead_letter(raw, reason=reason, topic=topic, error=str(exc))
        logger.warning("DROP event on %s (%s): %s", topic, reason, exc)
        self._send_json(200, {"status": "DROP", "error": str(exc)})

    def _send_json(self, status: int, body: object) -> None:
        payload = json.dumps(body, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, status: int, path: Path, content_type: str | None = None) -> None:
        payload = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", content_type or _content_type(path))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _wants_html(self) -> bool:
        return "text/html" in self.headers.get("Accept", "")

    def _serve_spa_route(self, path: str) -> bool:
        if path == "/" or path == "/index.html":
            return self._serve_index()
        if path in {"/heatmap", "/sessions"}:
            return self._serve_index()
        if path.startswith("/sessions/"):
            return self._serve_index()
        if path.startswith("/events/") and not (path.endswith("/summary") or path.endswith("/raw")):
            return self._serve_index()
        return False

    def _serve_index(self) -> bool:
        index = STATIC_DIR / "index.html"
        if not index.exists():
            return False
        self._send_file(200, index, "text/html")
        return True

    def _serve_static(self, path: str) -> bool:
        if path in {"/", "/index.html"}:
            return self._serve_index()
        rel = Path(unquote(path.lstrip("/")))
        if rel.is_absolute() or ".." in rel.parts:
            return False
        target = STATIC_DIR / rel
        if target.is_file() and target.resolve().is_relative_to(STATIC_DIR.resolve()):
            self._send_file(200, target)
            return True
        return False


def run(host: str = APP_HOST, port: int = APP_PORT) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_schema()
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("candystore: listening on %s:%s", host, port)
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    run()
    return 0


def _first(qs: dict[str, list[str]], key: str) -> str | None:
    vals = qs.get(key)
    return vals[0] if vals else None


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be an RFC 3339 timestamp") from exc


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


if __name__ == "__main__":
    raise SystemExit(main())
