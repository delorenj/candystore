from __future__ import annotations

import pytest

from candystore.db import insert_event


def test_insert_event_idempotent(db, sample_event):
    env = sample_event()

    assert insert_event(env) is True
    assert insert_event(env) is False


def test_insert_event_validates_required_fields(db, sample_event):
    env = sample_event()
    del env["producer"]

    with pytest.raises(ValueError, match="producer"):
        insert_event(env)
