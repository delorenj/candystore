"""Retention guards that need no database: bad input never reaches a connection."""
from __future__ import annotations

from pathlib import Path

import pytest

from candystore import cli, retention


def _no_connect(monkeypatch):
    def refuse():
        raise AssertionError("validation must fail before any connection")
    monkeypatch.setattr(retention.db, "_connect", refuse)


@pytest.mark.parametrize(
    ("types", "days", "batch"),
    [
        ([], 30, 100),
        (["bloodbank.agent.tool.requested'; --"], 30, 100),
        (["bloodbank.agent.tool.requested"], 0, 100),
        (["bloodbank.agent.tool.requested"], 30, 0),
        (["bloodbank.agent.tool.requested"], 30, 50_001),
    ],
)
def test_invalid_prune_input_is_rejected_before_connecting(monkeypatch, types, days, batch):
    _no_connect(monkeypatch)
    with pytest.raises(ValueError):
        retention.prune(types, days, Path("/nonexistent"), batch=batch)


def test_cli_prune_parses_repeatable_types():
    args = cli.parser().parse_args(
        ["prune", "--type", "a.b.tool.requested", "--type", "a.b.tool.completed",
         "--older-than", "30", "--dry-run"]
    )
    assert args.types == ["a.b.tool.requested", "a.b.tool.completed"]
    assert args.older_than == 30 and args.dry_run and args.batch == 5000


def test_cli_prune_reports_validation_errors(monkeypatch, capsys):
    _no_connect(monkeypatch)
    assert cli.main(["prune", "--type", "x y", "--older-than", "30"]) == 1
    assert "event type" in capsys.readouterr().err
