from __future__ import annotations

import json
import mimetypes
import os
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

from candystore.db import check_connection, init_schema
from candystore.ingest import handle_event, known_event_routes, subscribe_response
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

APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
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
            result = handle_event(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"status": "DROP", "error": str(exc)})
            return
        except Exception as exc:
            self._send_json(500, {"status": "RETRY", "error": str(exc)})
            return

        self._send_json(200, result)

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
    init_schema()
    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"candystore: listening on {host}:{port}\n")
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
