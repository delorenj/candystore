"""Readiness and the exact Compose healthcheck command, without a database fixture."""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from candystore.main import Handler

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def serve(handler, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def status(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


@pytest.mark.parametrize("connection_result", [True, False, RuntimeError("DB unavailable")])
def test_health_is_independent_of_readiness(connection_result):
    def check():
        if isinstance(connection_result, Exception):
            raise connection_result
        return connection_result

    with patch("candystore.main.check_connection", side_effect=check), serve(Handler) as port:
        assert status(port, "/healthz") == 204
        assert status(port, "/readyz") == (204 if connection_result is True else 503)


@pytest.fixture(scope="module")
def configured_probe():
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    check = json.loads(result.stdout)["services"]["candystore"]["healthcheck"]
    assert check["test"][0] == "CMD-SHELL"
    assert check["interval"] == "30s"
    assert check["timeout"] == "3s"
    assert check["retries"] == 10
    assert check["start_period"] == "5s"
    command = check["test"][1]
    assert "http://127.0.0.1:3001/readyz" in command
    assert "timeout=3" in command
    return command


class ProbeFixture(BaseHTTPRequestHandler):
    response_code = 204
    delay = 0

    def do_GET(self):
        assert self.path == "/readyz"
        time.sleep(self.delay)
        self.send_response(self.response_code)
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.mark.parametrize("code,delay,expected", [(204, 0, 0), (503, 0, 1), (204, 10, 1)])
def test_exact_configured_probe_against_http_fixture(configured_probe, code, delay, expected):
    class Fixture(ProbeFixture):
        response_code = code

    Fixture.delay = delay

    # Refuse to borrow/modify a process already listening at the configured port.
    with serve(Fixture, port=3001):
        start = time.monotonic()
        result = subprocess.run(configured_probe, shell=True, cwd=ROOT, capture_output=True,
                                text=True, timeout=5)
        elapsed = time.monotonic() - start
    assert (result.returncode == 0) is (expected == 0), result.stderr
    assert elapsed < 4.5


def test_exact_configured_probe_refused(configured_probe):
    # Close the bound listener first, so this address is actually unreachable.
    with serve(ProbeFixture, port=3001):
        pass
    start = time.monotonic()
    result = subprocess.run(configured_probe, shell=True, cwd=ROOT, capture_output=True,
                            text=True, timeout=5)
    assert result.returncode != 0
    assert time.monotonic() - start < 4.5
