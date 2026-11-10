"""The 8683 port contract: compose, the CLI default and $CANDYSTORE_URL agree (no database)."""

from pathlib import Path

from candystore import cli

ROOT = Path(__file__).resolve().parents[1]


def test_compose_publishes_the_contract_ports_on_loopback():
    compose = (ROOT / "compose.yml").read_text()
    assert '"127.0.0.1:${CANDYSTORE_PORT:-8683}:3001"' in compose
    assert '"127.0.0.1:${CANDYSTORE_POSTGRES_PORT:-5434}:5432"' in compose
    assert '"127.0.0.1:${CANDYSTORE_DAPR_HTTP_PORT:-3504}:3500"' in compose


def test_cli_default_is_the_contract():
    assert cli.DEFAULT_URL == "http://127.0.0.1:8683"


def _base_url(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CANDYSTORE_URL", raising=False)
    else:
        monkeypatch.setenv("CANDYSTORE_URL", value)
    return cli.parser().parse_args(["context", "latest"]).base_url


def test_cli_honors_candystore_url(monkeypatch):
    assert _base_url(monkeypatch, "http://127.0.0.1:3603") == "http://127.0.0.1:3603"


def test_cli_unset_or_blank_candystore_url_falls_back(monkeypatch):
    assert _base_url(monkeypatch, None) == "http://127.0.0.1:8683"
    assert _base_url(monkeypatch, "") == "http://127.0.0.1:8683"
