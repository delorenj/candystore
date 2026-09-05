#!/usr/bin/env python3
"""Capture a spread of real envelopes for tests/test_row_contract.py.

The row contract's promise is "every key, every event", and the shapes that
break it are the ones nobody thought to write a fixture for. So the evidence is
real rows -- but sampled by TYPE rather than at random, because 96% of the trail
is tool calls and a random 5,000 would be 4,800 of the same shape.

Usage:  DATABASE_URL=... python tests/fixtures/capture_envelopes.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from candystore.db import cursor  # noqa: E402

OUT = pathlib.Path(__file__).parent / "envelopes.jsonl"
# Per TYPE, not overall. 96% of the trail is tool calls, so a random 5,000
# would be 4,800 of one shape and would prove almost nothing about the other
# 104. 40 x ~105 types keeps the corpus a few MB and still covers every family.
PER_TYPE = 40

# A handful of envelopes are enormous (the largest prompt_text in the trail is
# 287 KB). They are worth ONE sample each for the shape, not forty -- and not
# at the cost of a repo blob nobody will ever read.
MAX_ENVELOPE_BYTES = 24_000

SQL = """
WITH ranked AS (
    SELECT raw, type,
           ROW_NUMBER() OVER (PARTITION BY type ORDER BY time DESC) AS rank
    FROM events
    WHERE time > NOW() - INTERVAL '30 days'
)
SELECT raw FROM ranked WHERE rank <= %s
"""


def main() -> int:
    with cursor() as cur:
        cur.execute(SQL, (PER_TYPE,))
        rows = cur.fetchall()
    written = oversized = 0
    seen_oversized: set[str] = set()
    with OUT.open("w") as handle:
        for (raw,) in rows:
            line = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            if len(line) > MAX_ENVELOPE_BYTES:
                # Keep the first of each type so the shape is represented.
                if raw.get("type") in seen_oversized:
                    oversized += 1
                    continue
                seen_oversized.add(raw.get("type"))
            handle.write(line + "\n")
            written += 1
    print(f"{written} envelopes -> {OUT} ({oversized} oversized duplicates skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
